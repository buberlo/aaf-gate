import argparse
import grp
import json
import os
import re
import signal
import sqlite3
import stat
import sys
import tempfile
import threading
from pathlib import Path

from framework import audit
from framework.baseline import compute as baseline_compute
from framework.canon import PRINCIPAL_ID, SCHEMA_VERSION, parse_json, parse_utc
from framework.version import VERSION
from framework.configplane import ConfigError
from framework.gatekeeper import GATE_KINDS, GateKeeper
from framework.backup import run_gate_backup, run_gate_restore
from framework.hermesprobe import evaluate_probe
from framework import hermes_mcp
from framework.gatesocket import GateServer
from framework.launchd import gate_plist
from framework.devicecheck import (
    check_hermes_dump,
    check_hooks,
    check_launchd,
    check_relay,
    package_customer_profile,
    package_plugin_digest,
    package_tenant_id,
    parse_audit_anchor,
    run_device_checks,
    sqlite_ro_uri,
)
from framework.pinaudit import audit_plugins, disabled_from_config
from framework.installcheck import verify_installation
from framework.plugincheck import plugin_digest, verify_plugin
from framework.principals import resolve
from framework.service import Service
from framework.stopswitch import StopSwitch
from framework.store import Store

GATE_PRINCIPAL_ROLES = frozenset({"operator", "reviewer", "author", "customer"})

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

DEMO_PRINCIPAL = "cli-demo"
DEMO_TENANT = "demo-a"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ensure_demo_principal(store) -> None:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT principal_id FROM principals WHERE principal_id=?",
            (DEMO_PRINCIPAL,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        return
    store.add_principal(DEMO_PRINCIPAL, DEMO_TENANT, "human")
    store.bind_role(DEMO_PRINCIPAL, "operator")
    store.bind_role(DEMO_PRINCIPAL, "reviewer")


def run_demo(service: Service) -> dict:
    created = service.command(
        DEMO_PRINCIPAL,
        {"operation": "create_run", "idempotency_key": "demo-create", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
    )
    run_id = created["run_id"]
    for name in ("d1.json", "d2.json", "d3.json"):
        service.command(
            DEMO_PRINCIPAL,
            {
                "operation": "register_source",
                "run_id": run_id,
                "idempotency_key": f"demo-{name}",
                "expected_state_version": 1,
                "expected_payload_revision": 0,
                "payload": load_fixture(name),
            },
        )
    service.command(
        DEMO_PRINCIPAL,
        {"operation": "validate", "run_id": run_id, "idempotency_key": "demo-validate", "expected_state_version": 1, "expected_payload_revision": 0},
    )
    drafted = service.command(
        DEMO_PRINCIPAL,
        {"operation": "build_draft", "run_id": run_id, "idempotency_key": "demo-draft", "expected_state_version": 2, "expected_payload_revision": 0},
    )
    reviewed = service.command(
        DEMO_PRINCIPAL,
        {
            "operation": "request_review",
            "run_id": run_id,
            "idempotency_key": "demo-review",
            "expected_state_version": drafted["state_version"],
            "expected_payload_revision": drafted["payload_revision"],
        },
    )
    exported = service.command(DEMO_PRINCIPAL, {"operation": "export_run", "run_id": run_id})
    artifact = exported["run"]["artifact"]
    return {
        "conflicts": artifact["conflicts"],
        "facts": artifact["facts"],
        "ignored_instructions": artifact["ignored_instructions"],
        "network": "disabled",
        "open_questions": artifact["open_questions"],
        "sources": [
            {
                "content_hash": source["content_hash"],
                "locators": [segment["locator"] for segment in source["segments"]],
                "source_id": source["source_id"],
                "synthetic": source["synthetic"],
            }
            for source in exported["sources"]
        ],
        "state": reviewed["state"],
        "work_draft": artifact["work_draft"],
    }


def _run_demo() -> int:
    with tempfile.TemporaryDirectory() as folder:
        service = Service.open(str(Path(folder) / "demo.sqlite"))
        ensure_demo_principal(service.store)
        print(json.dumps(run_demo(service), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_verify_plugin(installed: str, digest: str | None, package: str | None) -> int:
    if package is not None:
        digest = package_plugin_digest(package)
        if digest is None:
            print(
                json.dumps(
                    {"error": "PLUGIN_DIGEST_UNSET", "ok": False},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
    try:
        issues = verify_plugin(Path(installed), digest)
    except ConfigError as exc:
        print(json.dumps({"error": exc.code, "ok": False}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    body = {"issues": issues, "ok": not issues}
    print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


def _run_plugin_digest(directory: str) -> int:
    try:
        digest = plugin_digest(Path(directory))
    except ConfigError as exc:
        print(json.dumps({"error": exc.code, "ok": False}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps({"digest": digest, "ok": True}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _emit_line(out, body):
    out.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
    out.flush()


def _read_json_file(path: str) -> str:
    if not os.path.isabs(path):
        _emit_line(sys.stdout, {"error": "path_not_absolute", "ok": False})
        raise _PilotBaselineExit(2)
    try:
        st = os.lstat(path)
    except OSError:
        _emit_line(sys.stdout, {"error": "file_unreadable", "ok": False})
        raise _PilotBaselineExit(2)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _emit_line(sys.stdout, {"error": "file_unreadable", "ok": False})
        raise _PilotBaselineExit(2)
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        _emit_line(sys.stdout, {"error": "file_unreadable", "ok": False})
        raise _PilotBaselineExit(2)


class _PilotBaselineExit(Exception):
    def __init__(self, code: int):
        self.code = code


def _run_pilot_baseline(
    summary_path: str,
    timesheet_path: str,
    min_samples: int,
    require_clean: bool = False,
) -> int:
    try:
        summary_text = _read_json_file(summary_path)
    except _PilotBaselineExit as exc:
        return exc.code
    try:
        timesheet_text = _read_json_file(timesheet_path)
    except _PilotBaselineExit as exc:
        return exc.code
    try:
        summary = parse_json(summary_text)
    except ValueError:
        _emit_line(sys.stdout, {"error": "summary_invalid", "ok": False})
        return 2
    try:
        timesheet = parse_json(timesheet_text)
    except ValueError:
        _emit_line(sys.stdout, {"error": "timesheet_invalid", "ok": False})
        return 2
    try:
        result = baseline_compute(summary, timesheet, min_samples)
    except ValueError as exc:
        _emit_line(sys.stdout, {"error": str(exc), "ok": False})
        return 2
    if require_clean and result.get("observations_clean") is not True:
        _emit_line(sys.stdout, {"error": "observations_not_clean", "ok": False})
        return 1
    _emit_line(sys.stdout, {"baseline": result, "ok": True})
    return 0


def _gate_audit_event_context(operation, before_state):
    if operation != "gate_decide" or before_state is None:
        return None
    try:
        data = parse_json(before_state)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    keys = set(data.keys())
    if keys == {"action", "action_class", "tool"}:
        return data
    if keys == {"action", "action_class", "content_hash", "tool"}:
        return data
    return None


def _gate_audit_decide_content_hash(before_state):
    ctx = _gate_audit_event_context("gate_decide", before_state)
    if ctx is None:
        return None
    return ctx.get("content_hash")


def _gate_audit_decide_action_class(before_state):
    ctx = _gate_audit_event_context("gate_decide", before_state)
    if ctx is None:
        return None
    if ctx["action_class"] is None:
        return "action_class_unknown"
    return ctx["action_class"]


def _gate_audit_approvals_by_class(conn, tenant, since, until):
    obs_sql = (
        "SELECT sequence, actor_id, before_state, error_code FROM audit_events"
        " WHERE tenant_id=? AND operation='gate_observe'"
        " AND error_code IN ('approval_granted', 'approval_denied')"
    )
    obs_params: list = [tenant]
    if since is not None:
        obs_sql += " AND occurred_at >= ?"
        obs_params.append(since)
    if until is not None:
        obs_sql += " AND occurred_at < ?"
        obs_params.append(until)
    decide_rows = conn.execute(
        "SELECT sequence, actor_id, before_state FROM audit_events"
        " WHERE tenant_id=? AND operation='gate_decide' AND outcome='needs_approval'"
        " ORDER BY sequence ASC",
        (tenant,),
    ).fetchall()
    obs_rows = sorted(
        conn.execute(obs_sql, obs_params).fetchall(),
        key=lambda row: row["sequence"],
    )
    used: set[int] = set()
    by_class: dict[str, dict[str, int]] = {}
    for row in obs_rows:
        obs_seq = row["sequence"]
        actor = row["actor_id"]
        content = row["before_state"]
        field = "granted" if row["error_code"] == "approval_granted" else "denied"
        class_key = "unlinked"
        for decide in reversed(decide_rows):
            seq = decide["sequence"]
            if seq >= obs_seq or decide["actor_id"] != actor or seq in used:
                continue
            content_hash = _gate_audit_decide_content_hash(decide["before_state"])
            if content_hash is None or content_hash != content:
                continue
            action_class = _gate_audit_decide_action_class(decide["before_state"])
            if action_class is None:
                continue
            class_key = action_class
            used.add(seq)
            break
        by_class.setdefault(class_key, {"granted": 0, "denied": 0})
        by_class[class_key][field] += 1
    return by_class


def _gate_audit_anchor_value(verify_result: dict) -> str | None:
    if not verify_result.get("ok"):
        return None
    count = verify_result.get("count")
    head = verify_result.get("head")
    if not isinstance(count, int) or count < 1:
        return None
    if not isinstance(head, str):
        return None
    if re.fullmatch(r"sha256:[0-9a-f]{64}", head) is None:
        return None
    return str(count) + ":" + head


def _gate_db_preflight(db):
    if not os.path.isabs(db):
        return "db_path_not_absolute"
    try:
        st = os.lstat(db)
    except OSError:
        return "gate_db_unreadable"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return "gate_db_unreadable"
    return None


def _gate_audit_summary_stops(conn, tenant, since, until):
    time_sql = ""
    time_params: list = []
    if since is not None:
        time_sql += " AND occurred_at >= ?"
        time_params.append(since)
    if until is not None:
        time_sql += " AND occurred_at < ?"
        time_params.append(until)

    def count_query(extra):
        sql = (
            "SELECT COUNT(*) FROM audit_events"
            " WHERE tenant_id=? AND "
            + extra
            + time_sql
        )
        return int(conn.execute(sql, [tenant, *time_params]).fetchone()[0])

    denied_while_stopped = count_query(
        "operation='gate_decide' AND error_code='stopped'"
    )
    stop_count = count_query("operation='gate_stop' AND outcome='allow'")
    resume_count = count_query("operation='gate_resume' AND outcome='allow'")
    rejected = count_query(
        "operation IN ('gate_stop', 'gate_resume') AND outcome='deny'"
    )
    stopped_now = None
    try:
        row = conn.execute(
            "SELECT stopped FROM gate_stops WHERE tenant_id=?",
            (tenant,),
        ).fetchone()
        if row is None:
            stopped_now = False
        else:
            stopped_now = int(row["stopped"]) == 1
    except sqlite3.Error:
        stopped_now = None
    return {
        "denied_while_stopped": denied_while_stopped,
        "rejected": rejected,
        "resume": resume_count,
        "stop": stop_count,
        "stopped": stopped_now,
    }


def _run_gate_principal_add(db, principal, tenant, roles):
    err = _gate_db_preflight(db)
    if err is not None:
        _emit_line(sys.stdout, {"error": err, "ok": False})
        return 2
    if not isinstance(principal, str) or PRINCIPAL_ID.fullmatch(principal) is None:
        _emit_line(sys.stdout, {"error": "invalid_principal_id", "ok": False})
        return 2
    if not isinstance(tenant, str) or PRINCIPAL_ID.fullmatch(tenant) is None:
        _emit_line(sys.stdout, {"error": "invalid_tenant_id", "ok": False})
        return 2
    role_list = sorted(set(roles))
    for role in role_list:
        if role not in GATE_PRINCIPAL_ROLES:
            _emit_line(sys.stdout, {"error": "invalid_role", "ok": False})
            return 2
    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        if conn.execute(
            "SELECT 1 FROM principals WHERE principal_id=?",
            (principal,),
        ).fetchone() is not None:
            _emit_line(sys.stdout, {"error": "principal_exists", "ok": False})
            return 2
        if (
            conn.execute(
                "SELECT 1 FROM principals WHERE tenant_id=? LIMIT 1",
                (tenant,),
            ).fetchone()
            is None
        ):
            _emit_line(sys.stdout, {"error": "tenant_unknown", "ok": False})
            return 2
    except sqlite3.Error:
        _emit_line(sys.stdout, {"error": "gate_db_unreadable", "ok": False})
        return 2
    finally:
        if conn is not None:
            conn.close()
    try:
        store = Store(db)
        store.add_principal(principal, tenant, "human")
        for role in role_list:
            store.bind_role(principal, role)
    except Exception:
        _emit_line(sys.stdout, {"error": "store_write_failed", "ok": False})
        return 2
    _emit_line(
        sys.stdout,
        {
            "kind": "human",
            "ok": True,
            "principal": principal,
            "roles": role_list,
            "tenant": tenant,
        },
    )
    return 0


def _run_gate_stop_resume(db, principal, command):
    err = _gate_db_preflight(db)
    if err is not None:
        _emit_line(sys.stdout, {"error": err, "ok": False})
        return 2
    try:
        switch = StopSwitch(Store(db))
    except Exception:
        _emit_line(sys.stdout, {"error": "storage_unavailable", "ok": False})
        return 2
    if command == "gate-stop":
        result = switch.stop(principal)
    else:
        result = switch.resume(principal)
    body = dict(result)
    body["command"] = command
    _emit_line(sys.stdout, body)
    if result.get("ok"):
        return 0
    reason = result.get("reason")
    if reason in ("principal_unknown", "principal_kind_forbidden", "role_forbidden"):
        return 1
    return 2


def _run_gate_status(db, tenant):
    err = _gate_db_preflight(db)
    if err is not None:
        _emit_line(sys.stdout, {"error": err, "ok": False})
        return 2
    if not isinstance(tenant, str) or PRINCIPAL_ID.fullmatch(tenant) is None:
        _emit_line(sys.stdout, {"error": "invalid_tenant_id", "ok": False})
        return 2
    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT stopped, changed_at, changed_by, audit_sequence"
            " FROM gate_stops WHERE tenant_id=?",
            (tenant,),
        ).fetchone()
    except sqlite3.Error:
        _emit_line(sys.stdout, {"error": "stop_state_unavailable", "ok": False})
        return 2
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        body = {
            "audit_sequence": None,
            "changed_at": None,
            "changed_by": None,
            "ok": True,
            "stopped": False,
            "tenant": tenant,
        }
    else:
        body = {
            "audit_sequence": int(row["audit_sequence"]),
            "changed_at": row["changed_at"],
            "changed_by": row["changed_by"],
            "ok": True,
            "stopped": int(row["stopped"]) == 1,
            "tenant": tenant,
        }
    _emit_line(sys.stdout, body)
    return 0


def _gate_audit_summary_keys(before_state):
    ctx = _gate_audit_event_context("gate_decide", before_state)
    if ctx is None:
        return "no_context", "no_context"
    if ctx["action_class"] is None:
        return "action_class_unknown", ctx["tool"]
    return ctx["action_class"], ctx["tool"]


def _run_gate_audit(
    db: str,
    tenant: str,
    limit: int,
    summary: bool = False,
    since: str | None = None,
    until: str | None = None,
    anchor: bool = False,
) -> int:
    if not os.path.isabs(db):
        _emit_line(sys.stdout, {"error": "db_path_not_absolute", "ok": False})
        return 2
    if limit < 1 or limit > 1000:
        _emit_line(sys.stdout, {"error": "limit_invalid", "ok": False})
        return 2
    if anchor and (summary or since is not None or until is not None):
        _emit_line(sys.stdout, {"error": "anchor_conflict", "ok": False})
        return 2
    if (since is not None or until is not None) and not summary:
        _emit_line(sys.stdout, {"error": "time_requires_summary", "ok": False})
        return 2
    if since is not None:
        try:
            parse_utc(since)
        except ValueError:
            _emit_line(sys.stdout, {"error": "time_invalid", "ok": False})
            return 2
    if until is not None:
        try:
            parse_utc(until)
        except ValueError:
            _emit_line(sys.stdout, {"error": "time_invalid", "ok": False})
            return 2
    if since is not None and until is not None and since >= until:
        _emit_line(sys.stdout, {"error": "time_range_invalid", "ok": False})
        return 2
    try:
        st = os.lstat(db)
    except OSError:
        _emit_line(sys.stdout, {"error": "gate_db_unreadable", "ok": False})
        return 2
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _emit_line(sys.stdout, {"error": "gate_db_unreadable", "ok": False})
        return 2
    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        conn.row_factory = sqlite3.Row
        result = audit.verify(conn, tenant)
        if anchor:
            body = {
                "anchor": _gate_audit_anchor_value(result),
                "ok": result["ok"],
                "tenant": tenant,
                "verify": result,
            }
            _emit_line(sys.stdout, body)
            return 0 if result["ok"] else 1
        if summary:
            sql = (
                "SELECT outcome, before_state FROM audit_events"
                " WHERE tenant_id=? AND operation='gate_decide'"
            )
            params: list = [tenant]
            if since is not None:
                sql += " AND occurred_at >= ?"
                params.append(since)
            if until is not None:
                sql += " AND occurred_at < ?"
                params.append(until)
            by_class: dict[str, dict[str, int]] = {}
            by_tool: dict[str, dict[str, int]] = {}
            total = 0
            for row in conn.execute(sql, params):
                total += 1
                class_key, tool_key = _gate_audit_summary_keys(row["before_state"])
                outcome = row["outcome"]
                by_class.setdefault(class_key, {})
                by_class[class_key][outcome] = by_class[class_key].get(outcome, 0) + 1
                by_tool.setdefault(tool_key, {})
                by_tool[tool_key][outcome] = by_tool[tool_key].get(outcome, 0) + 1
            obs_sql = (
                "SELECT error_code FROM audit_events"
                " WHERE tenant_id=? AND operation='gate_observe'"
            )
            obs_params: list = [tenant]
            if since is not None:
                obs_sql += " AND occurred_at >= ?"
                obs_params.append(since)
            if until is not None:
                obs_sql += " AND occurred_at < ?"
                obs_params.append(until)
            observations = {
                "gate_mismatch": 0,
                "gate_unseen": 0,
                "gate_ignored": 0,
                "other": 0,
            }
            approvals = {"granted": 0, "denied": 0}
            for row in conn.execute(obs_sql, obs_params):
                error_code = row["error_code"]
                if error_code == "gate_mismatch":
                    observations["gate_mismatch"] += 1
                elif error_code == "gate_unseen":
                    observations["gate_unseen"] += 1
                elif error_code == "gate_ignored":
                    observations["gate_ignored"] += 1
                elif error_code == "approval_granted":
                    approvals["granted"] += 1
                elif error_code == "approval_denied":
                    approvals["denied"] += 1
                else:
                    observations["other"] += 1
            approvals_by_class = _gate_audit_approvals_by_class(conn, tenant, since, until)
            stops = _gate_audit_summary_stops(conn, tenant, since, until)
            body = {
                "ok": result["ok"],
                "since": since,
                "summary": {
                    "approvals": approvals,
                    "approvals_by_class": approvals_by_class,
                    "by_class": by_class,
                    "by_tool": by_tool,
                    "observations": observations,
                    "stops": stops,
                    "total": total,
                },
                "tenant": tenant,
                "until": until,
                "verify": result,
            }
            _emit_line(sys.stdout, body)
            return 0 if result["ok"] else 1
        rows = conn.execute(
            """SELECT sequence, occurred_at, operation, outcome, error_code, actor_id,
               principal_kind, payload_hash, rule_hash, approval_id, before_state
               FROM audit_events WHERE tenant_id=? ORDER BY sequence DESC LIMIT ?""",
            (tenant, limit),
        ).fetchall()
        events = [
            {
                "actor_id": row["actor_id"],
                "approval_id": row["approval_id"],
                "context": _gate_audit_event_context(row["operation"], row["before_state"]),
                "error_code": row["error_code"],
                "occurred_at": row["occurred_at"],
                "operation": row["operation"],
                "outcome": row["outcome"],
                "payload_hash": row["payload_hash"],
                "principal_kind": row["principal_kind"],
                "rule_hash": row["rule_hash"],
                "sequence": row["sequence"],
            }
            for row in reversed(rows)
        ]
        body = {"events": events, "ok": result["ok"], "tenant": tenant, "verify": result}
        _emit_line(sys.stdout, body)
        return 0 if result["ok"] else 1
    except sqlite3.Error:
        _emit_line(sys.stdout, {"error": "gate_db_unreadable", "ok": False})
        return 2
    finally:
        if conn is not None:
            conn.close()


def serve_gate(
    db,
    socket_path,
    principal_id,
    server="gmail",
    mode="0600",
    group=None,
    unattended=False,
    stop=None,
    ready=None,
    out=None,
):
    stream = out or sys.stdout
    gate = None

    def fail(code):
        _emit_line(stream, {"error": code, "ok": False})
        return 2

    if not os.path.isabs(socket_path):
        return fail("socket_path_not_absolute")
    if mode not in ("0600", "0660"):
        return fail("invalid_socket_mode")
    gid = None
    if group is not None:
        if isinstance(group, str) and group.isdigit():
            gid = int(group)
        elif isinstance(group, str):
            try:
                gid = grp.getgrnam(group).gr_gid
            except KeyError:
                return fail("group_unknown")
        else:
            return fail("group_unknown")
    if os.path.islink(db):
        return fail("store_symlink")
    if not Path(db).is_file():
        return fail("store_missing")
    db_stat = os.stat(db)
    if stat.S_IMODE(db_stat.st_mode) & 0o077:
        return fail("store_mode_insecure")
    parent = os.path.dirname(os.path.abspath(db))
    if stat.S_IMODE(os.stat(parent).st_mode) & 0o022:
        return fail("store_dir_insecure")
    db_mode = format(stat.S_IMODE(db_stat.st_mode), "04o")
    store = Store(db)
    conn = store.connect()
    try:
        principal = resolve(conn, principal_id)
    finally:
        conn.close()
    if principal is None:
        return fail("principal_unknown")
    if principal.kind not in GATE_KINDS:
        return fail("principal_kind_forbidden")
    try:
        policy = hermes_mcp.pilot1_policy(server)
    except ValueError as exc:
        return fail(str(exc))
    keeper = GateKeeper(store)
    try:
        gate = GateServer(
            socket_path,
            keeper.bind(principal_id, policy, unattended=unattended),
            keeper.bind_observe(principal_id),
            mode=int(mode, 8),
            group=gid,
        )
        gate.start()
    except ValueError as exc:
        return fail(str(exc))
    except FileExistsError:
        return fail("socket_path_occupied")
    _emit_line(
        stream,
        {
            "db_mode": db_mode,
            "mode": mode,
            "ok": True,
            "principal": principal_id,
            "server": server,
            "socket": socket_path,
            "unattended": bool(unattended),
        },
    )
    if ready is not None:
        ready.set()
    if stop is None:
        stop = threading.Event()
    try:
        stop.wait()
    finally:
        if gate is not None:
            gate.close()
    return 0


def init_gate_db(db, principal_id, tenant_id, kind="agent", out=None):
    stream = out or sys.stdout

    def fail(code):
        _emit_line(stream, {"error": code, "ok": False})
        return 2

    if not os.path.isabs(db):
        return fail("db_path_not_absolute")
    if not isinstance(principal_id, str) or PRINCIPAL_ID.fullmatch(principal_id) is None:
        return fail("invalid_principal_id")
    if not isinstance(tenant_id, str) or PRINCIPAL_ID.fullmatch(tenant_id) is None:
        return fail("invalid_tenant_id")
    if kind not in GATE_KINDS:
        return fail("principal_kind_forbidden")
    parent = os.path.dirname(db)
    if not os.path.isdir(parent):
        return fail("db_dir_missing")
    if os.path.lexists(db):
        return fail("db_exists")
    try:
        fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(db, 0o600)
        store = Store(db)
        store.add_principal(principal_id, tenant_id, kind)
    except Exception:
        try:
            if os.path.lexists(db):
                os.unlink(db)
        except OSError:
            pass
        return fail("store_init_failed")
    _emit_line(
        stream,
        {
            "db": db,
            "kind": kind,
            "ok": True,
            "principal": principal_id,
            "tenant": tenant_id,
        },
    )
    return 0


def _run_pin_audit(source: str, config: str) -> int:
    try:
        text = Path(config).read_text(encoding="utf-8")
    except OSError:
        _emit_line(sys.stdout, {"error": "config_unreadable", "ok": False})
        return 2
    try:
        result = audit_plugins(source, set(disabled_from_config(text)))
    except ConfigError as exc:
        _emit_line(sys.stdout, {"error": exc.code, "ok": False})
        return 2
    issues = result["issues"]
    body = {"issues": issues, "ok": not issues, "plugins": result["plugins"]}
    _emit_line(sys.stdout, body)
    return 0 if not issues else 1


def _run_verify_hermes_dump(dump_file: str, package: str) -> int:
    body = check_hermes_dump(dump_file, package)
    if body.get("error"):
        _emit_line(sys.stdout, {"error": body["error"], "ok": False})
        return 2
    _emit_line(sys.stdout, body)
    return 0 if body["ok"] else 1


def _run_verify_hermes_probe(probe_file: str, package: str) -> int:
    try:
        probe_text = Path(probe_file).read_text(encoding="utf-8")
    except OSError:
        _emit_line(sys.stdout, {"error": "probe_unreadable", "ok": False})
        return 2
    code, body = evaluate_probe(package, probe_text)
    _emit_line(sys.stdout, body)
    return code


def _run_verify_relay(home: str | None, system_root: str) -> int:
    if home is None or not str(home).strip():
        _emit_line(sys.stdout, {"error": "home_missing", "ok": False})
        return 2
    body = check_relay(os.environ, home, system_root)
    _emit_line(sys.stdout, body)
    return 0 if body["ok"] else 1


def _run_verify_hooks(home: str) -> int:
    body = check_hooks(home)
    _emit_line(sys.stdout, body)
    return 0 if body["ok"] else 1


def _run_verify_launchd(installed: str, package: str) -> int:
    body = check_launchd(installed, package)
    if body.get("error"):
        _emit_line(sys.stdout, {"error": body["error"], "ok": False})
        return 2
    _emit_line(sys.stdout, body)
    return 0 if body["ok"] else 1


def _run_verify_device(
    package,
    hermes_home,
    plugin_dir,
    launchd_plist,
    dump_file,
    home,
    system_root,
    installed_root,
    package_hash,
    managed_dir,
    expected_profile,
    allowed_profiles,
    audit_anchor,
) -> int:
    body = run_device_checks(
        package,
        hermes_home,
        plugin_dir,
        launchd_plist,
        dump_file,
        home,
        system_root,
        os.environ,
        installed_root=installed_root,
        package_hash=package_hash,
        managed_dir=managed_dir,
        expected_profile=expected_profile,
        allowed_profiles=allowed_profiles,
        audit_anchor=audit_anchor,
    )
    _emit_line(sys.stdout, body)
    return 0 if body["ok"] else 1


def _run_gate_plist(args) -> int:
    try:
        payload = gate_plist(
            args.label,
            args.python,
            args.workdir,
            args.db,
            args.socket,
            args.principal,
            args.user,
            group=args.group,
            server=args.server,
            mode=args.mode,
            unattended=args.unattended,
            log_path=args.log,
        )
    except ValueError as exc:
        _emit_line(sys.stdout, {"error": str(exc), "ok": False})
        return 2
    sys.stdout.buffer.write(payload)
    return 0


def _run_verify_install(package: str, target: str, package_hash: str | None) -> int:
    try:
        issues = verify_installation(Path(package), Path(target), package_hash)
    except ConfigError as exc:
        print(json.dumps({"error": exc.code, "ok": False}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    body = {"issues": issues, "ok": not issues}
    print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="framework.cli")
    subparsers = parser.add_subparsers(dest="command")
    verify = subparsers.add_parser("verify-install")
    verify.add_argument("package")
    verify.add_argument("target")
    verify.add_argument("--package-hash", default=None)
    verify_plugin_cmd = subparsers.add_parser("verify-plugin")
    verify_plugin_cmd.add_argument("installed")
    digest_group = verify_plugin_cmd.add_mutually_exclusive_group(required=True)
    digest_group.add_argument("--digest")
    digest_group.add_argument("--package")
    plugin_digest_cmd = subparsers.add_parser("plugin-digest")
    plugin_digest_cmd.add_argument("dir")
    gate_serve_cmd = subparsers.add_parser("gate-serve")
    gate_serve_cmd.add_argument("--db", required=True)
    gate_serve_cmd.add_argument("--socket", required=True)
    gate_serve_cmd.add_argument("--principal", required=True)
    gate_serve_cmd.add_argument("--server", default="gmail")
    gate_serve_cmd.add_argument("--mode", default="0600")
    gate_serve_cmd.add_argument("--group", default=None)
    gate_serve_cmd.add_argument("--unattended", action="store_true")
    gate_plist_cmd = subparsers.add_parser("gate-plist")
    gate_plist_cmd.add_argument("--label", required=True)
    gate_plist_cmd.add_argument("--python", required=True)
    gate_plist_cmd.add_argument("--workdir", required=True)
    gate_plist_cmd.add_argument("--db", required=True)
    gate_plist_cmd.add_argument("--socket", required=True)
    gate_plist_cmd.add_argument("--principal", required=True)
    gate_plist_cmd.add_argument("--user", required=True)
    gate_plist_cmd.add_argument("--group", default=None)
    gate_plist_cmd.add_argument("--server", default="gmail")
    gate_plist_cmd.add_argument("--mode", default="0600")
    gate_plist_cmd.add_argument("--unattended", action="store_true")
    gate_plist_cmd.add_argument("--log", default=None, dest="log")
    verify_launchd_cmd = subparsers.add_parser("verify-launchd")
    verify_launchd_cmd.add_argument("installed")
    verify_launchd_cmd.add_argument("--package", required=True)
    verify_hooks_cmd = subparsers.add_parser("verify-hooks")
    verify_hooks_cmd.add_argument("home")
    verify_relay_cmd = subparsers.add_parser("verify-relay")
    verify_relay_cmd.add_argument("--home", default=None)
    verify_relay_cmd.add_argument("--system-root", default="/")
    pin_audit_cmd = subparsers.add_parser("pin-audit")
    pin_audit_cmd.add_argument("source")
    pin_audit_cmd.add_argument("config")
    verify_dump_cmd = subparsers.add_parser("verify-hermes-dump")
    verify_dump_cmd.add_argument("dump_file")
    verify_dump_cmd.add_argument("--package", required=True)
    verify_probe_cmd = subparsers.add_parser("verify-hermes-probe")
    verify_probe_cmd.add_argument("probe_file")
    verify_probe_cmd.add_argument("--package", required=True)
    verify_device_cmd = subparsers.add_parser("verify-device")
    verify_device_cmd.add_argument("--package", required=True)
    verify_device_cmd.add_argument("--installed", required=True)
    verify_device_cmd.add_argument("--package-hash", required=True)
    verify_device_cmd.add_argument("--managed-dir", default="/etc/hermes")
    verify_device_cmd.add_argument("--hermes-home", required=True)
    verify_device_cmd.add_argument("--plugin", required=True)
    verify_device_cmd.add_argument("--launchd", required=True)
    verify_device_cmd.add_argument("--dump", required=True)
    verify_device_cmd.add_argument("--home", default=None)
    verify_device_cmd.add_argument("--system-root", default="/")
    verify_device_cmd.add_argument("--profile", default=None)
    verify_device_cmd.add_argument("--allowed-profile", action="append", default=None)
    verify_device_cmd.add_argument("--audit-anchor", default=None)
    gate_init_cmd = subparsers.add_parser("gate-init")
    gate_init_cmd.add_argument("--db", required=True)
    gate_init_cmd.add_argument("--principal", required=True)
    gate_init_cmd.add_argument("--tenant", required=True)
    gate_init_cmd.add_argument("--package", default=None)
    gate_init_cmd.add_argument("--kind", default="agent")
    gate_audit_cmd = subparsers.add_parser("gate-audit")
    gate_audit_cmd.add_argument("--db", required=True)
    gate_audit_cmd.add_argument("--tenant", required=True)
    gate_audit_cmd.add_argument("--limit", type=int, default=50)
    gate_audit_cmd.add_argument("--summary", action="store_true")
    gate_audit_cmd.add_argument("--since", default=None)
    gate_audit_cmd.add_argument("--until", default=None)
    gate_audit_cmd.add_argument("--anchor", action="store_true")
    gate_principal_add_cmd = subparsers.add_parser("gate-principal-add")
    gate_principal_add_cmd.add_argument("--db", required=True)
    gate_principal_add_cmd.add_argument("--principal", required=True)
    gate_principal_add_cmd.add_argument("--tenant", required=True)
    gate_principal_add_cmd.add_argument("--role", action="append", required=True)
    gate_stop_cmd = subparsers.add_parser("gate-stop")
    gate_stop_cmd.add_argument("--db", required=True)
    gate_stop_cmd.add_argument("--principal", required=True)
    gate_resume_cmd = subparsers.add_parser("gate-resume")
    gate_resume_cmd.add_argument("--db", required=True)
    gate_resume_cmd.add_argument("--principal", required=True)
    gate_status_cmd = subparsers.add_parser("gate-status")
    gate_status_cmd.add_argument("--db", required=True)
    gate_status_cmd.add_argument("--tenant", required=True)
    gate_backup_cmd = subparsers.add_parser("gate-backup")
    gate_backup_cmd.add_argument("--db", required=True)
    gate_backup_cmd.add_argument("--out", required=True)
    gate_restore_cmd = subparsers.add_parser("gate-restore")
    gate_restore_cmd.add_argument("--backup", required=True)
    gate_restore_cmd.add_argument("--db", required=True)
    gate_restore_cmd.add_argument("--expect-anchor", action="append", default=None)
    pilot_baseline_cmd = subparsers.add_parser("pilot-baseline")
    pilot_baseline_cmd.add_argument("--summary", required=True)
    pilot_baseline_cmd.add_argument("--timesheet", required=True)
    pilot_baseline_cmd.add_argument("--min-samples", type=int, default=5)
    pilot_baseline_cmd.add_argument("--require-clean", action="store_true")
    subparsers.add_parser("version")
    args = parser.parse_args(argv)
    if args.command is None:
        return _run_demo()
    if args.command == "verify-install":
        return _run_verify_install(args.package, args.target, args.package_hash)
    if args.command == "verify-plugin":
        return _run_verify_plugin(args.installed, args.digest, args.package)
    if args.command == "plugin-digest":
        return _run_plugin_digest(args.dir)
    if args.command == "gate-serve":
        stop = threading.Event()

        def _stop_handler(*_args):
            stop.set()

        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGINT, _stop_handler)
        return serve_gate(
            args.db,
            args.socket,
            args.principal,
            server=args.server,
            mode=args.mode,
            group=args.group,
            unattended=args.unattended,
            stop=stop,
        )
    if args.command == "gate-plist":
        return _run_gate_plist(args)
    if args.command == "verify-launchd":
        return _run_verify_launchd(args.installed, args.package)
    if args.command == "verify-hooks":
        return _run_verify_hooks(args.home)
    if args.command == "verify-relay":
        home = args.home if args.home is not None else os.environ.get("HOME")
        return _run_verify_relay(home, args.system_root)
    if args.command == "pin-audit":
        return _run_pin_audit(args.source, args.config)
    if args.command == "verify-hermes-dump":
        return _run_verify_hermes_dump(args.dump_file, args.package)
    if args.command == "verify-hermes-probe":
        return _run_verify_hermes_probe(args.probe_file, args.package)
    if args.command == "verify-device":
        home = args.home if args.home is not None else os.environ.get("HOME")
        if home is None or not str(home).strip():
            _emit_line(sys.stdout, {"error": "home_missing", "ok": False})
            return 2
        readable, pkg_profile = package_customer_profile(args.package)
        if not readable:
            _emit_line(sys.stdout, {"error": "provenance_unreadable", "ok": False})
            return 2
        if pkg_profile is not None:
            if args.profile is not None and args.profile != pkg_profile:
                _emit_line(
                    sys.stdout,
                    {"error": "profile_package_mismatch", "expected": pkg_profile, "ok": False},
                )
                return 2
            profile = pkg_profile
            if args.allowed_profile is None:
                allowed_profiles = (pkg_profile,)
            else:
                normalized = tuple(sorted(set(args.allowed_profile)))
                if normalized != (pkg_profile,):
                    _emit_line(
                        sys.stdout,
                        {"error": "profile_package_mismatch", "expected": pkg_profile, "ok": False},
                    )
                    return 2
                allowed_profiles = normalized
        else:
            profile = args.profile if args.profile is not None else "default"
            if args.allowed_profile is None:
                if profile == "default":
                    allowed_profiles = ()
                else:
                    allowed_profiles = (profile,)
            else:
                allowed_profiles = tuple(args.allowed_profile)
        parsed_anchor = None
        if args.audit_anchor is not None:
            parsed_anchor = parse_audit_anchor(args.audit_anchor)
            if parsed_anchor is None:
                _emit_line(sys.stdout, {"error": "audit_anchor_invalid", "ok": False})
                return 2
        return _run_verify_device(
            args.package,
            args.hermes_home,
            args.plugin,
            args.launchd,
            args.dump,
            home,
            args.system_root,
            args.installed,
            args.package_hash,
            args.managed_dir,
            profile,
            allowed_profiles,
            parsed_anchor,
        )
    if args.command == "gate-init":
        if args.package is not None:
            readable, package_tenant = package_tenant_id(args.package)
            if not readable or package_tenant is None:
                _emit_line(sys.stdout, {"error": "package_tenant_missing", "ok": False})
                return 2
            if args.tenant != package_tenant:
                _emit_line(sys.stdout, {"error": "tenant_package_mismatch", "ok": False})
                return 2
        return init_gate_db(args.db, args.principal, args.tenant, kind=args.kind)
    if args.command == "gate-audit":
        return _run_gate_audit(
            args.db,
            args.tenant,
            args.limit,
            summary=args.summary,
            since=args.since,
            until=args.until,
            anchor=args.anchor,
        )
    if args.command == "gate-principal-add":
        return _run_gate_principal_add(
            args.db, args.principal, args.tenant, args.role
        )
    if args.command == "gate-stop":
        return _run_gate_stop_resume(args.db, args.principal, "gate-stop")
    if args.command == "gate-resume":
        return _run_gate_stop_resume(args.db, args.principal, "gate-resume")
    if args.command == "gate-status":
        return _run_gate_status(args.db, args.tenant)
    if args.command == "gate-backup":
        code, body = run_gate_backup(args.db, args.out)
        _emit_line(sys.stdout, body)
        return code
    if args.command == "gate-restore":
        code, body = run_gate_restore(
            args.backup, args.db, expect_anchors=args.expect_anchor
        )
        _emit_line(sys.stdout, body)
        return code
    if args.command == "pilot-baseline":
        return _run_pilot_baseline(
            args.summary,
            args.timesheet,
            args.min_samples,
            require_clean=args.require_clean,
        )
    if args.command == "version":
        print(
            json.dumps(
                {
                    "core_version": VERSION,
                    "ok": True,
                    "schema_version": SCHEMA_VERSION,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())