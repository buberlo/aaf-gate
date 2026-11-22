import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from framework import audit
from framework.canon import PRINCIPAL_ID
from framework.clock import Clock
from framework.devicecheck import sqlite_ro_uri

BACKUP_FORMAT = "aaf-gate-backup-1"
ANCHOR_VALUE_RE = re.compile(r"^[1-9][0-9]*:sha256:[0-9a-f]{64}$")


def _audit_anchor(verify_result: dict) -> str | None:
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


def _fail(code, **extra):
    body = {"error": code, "ok": False}
    body.update(extra)
    return body


def _parent_writable_bits(parent: str) -> bool:
    return bool(stat.S_IMODE(os.stat(parent).st_mode) & 0o022)


def _unlink_quiet(path: str) -> None:
    try:
        if os.path.lexists(path):
            os.unlink(path)
    except OSError:
        pass


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_ro_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _tenant_ids(conn: sqlite3.Connection) -> list[str]:
    found: set[str] = set()
    for table in ("audit_heads", "audit_events"):
        try:
            for row in conn.execute("SELECT DISTINCT tenant_id FROM " + table):
                found.add(row[0])
        except sqlite3.Error:
            pass
    return sorted(found)


def _tenant_snapshot(conn: sqlite3.Connection, tenant: str) -> dict:
    verify_result = audit.verify(conn, tenant)
    return {
        "anchor": _audit_anchor(verify_result),
        "count": verify_result["count"],
        "head": verify_result["head"],
        "verify": verify_result,
    }


def _stopped(conn: sqlite3.Connection, tenant: str) -> bool:
    try:
        row = conn.execute(
            "SELECT stopped FROM gate_stops WHERE tenant_id=?", (tenant,)
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    return int(row["stopped"]) == 1


def _manifest_matches_db(conn: sqlite3.Connection, manifest: dict) -> tuple[bool, str | None]:
    tenants = manifest.get("tenants")
    if not isinstance(tenants, dict):
        return False, None
    db_tenants = _tenant_ids(conn)
    if sorted(tenants.keys()) != db_tenants:
        return False, None
    for tenant in db_tenants:
        entry = tenants[tenant]
        if not isinstance(entry, dict):
            return False, tenant
        snap = _tenant_snapshot(conn, tenant)
        if not snap["verify"]["ok"]:
            return False, tenant
        if entry.get("count") != snap["count"]:
            return False, tenant
        if entry.get("head") != snap["head"]:
            return False, tenant
        if entry.get("anchor") != snap["anchor"]:
            return False, tenant
    return True, None


def _parse_expect_anchors(items: list[str] | None) -> tuple[str | None, dict[str, str] | None]:
    if not items:
        return None, {}
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            return "invalid_anchor", None
        tenant, anchor = item.split("=", 1)
        if PRINCIPAL_ID.fullmatch(tenant) is None:
            return "invalid_anchor", None
        if ANCHOR_VALUE_RE.fullmatch(anchor) is None:
            return "invalid_anchor", None
        if tenant in parsed:
            return "invalid_anchor", None
        parsed[tenant] = anchor
    return None, parsed


def _source_db_preflight(db: str) -> str | None:
    if not os.path.isabs(db):
        return "db_path_not_absolute"
    try:
        st = os.lstat(db)
    except OSError:
        return "gate_db_unreadable"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return "gate_db_unreadable"
    return None


def run_gate_backup(db: str, out: str) -> tuple[int, dict]:
    preflight = _source_db_preflight(db)
    if preflight is not None:
        return 2, _fail(preflight)
    if not os.path.isabs(out):
        return 2, _fail("out_path_not_absolute")
    if os.path.lexists(out):
        return 2, _fail("out_exists")
    manifest_path = out + ".manifest.json"
    if os.path.lexists(manifest_path):
        return 2, _fail("manifest_exists")
    parent = os.path.dirname(out)
    if not os.path.isdir(parent):
        return 2, _fail("out_dir_missing")
    if _parent_writable_bits(parent):
        return 2, _fail("out_dir_insecure")
    try:
        fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(out, 0o600)
        src = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    except (sqlite3.Error, OSError):
        _unlink_quiet(out)
        return 2, _fail("backup_failed")
    try:
        conn = _open_ro(out)
        try:
            if not _integrity_ok(conn):
                _unlink_quiet(out)
                return 1, _fail("backup_integrity_failed")
            tenants: dict[str, dict] = {}
            for tenant in _tenant_ids(conn):
                snap = _tenant_snapshot(conn, tenant)
                if not snap["verify"]["ok"]:
                    _unlink_quiet(out)
                    return (
                        1,
                        _fail(
                            "chain_invalid",
                            first_break=snap["verify"].get("first_break"),
                            tenant=tenant,
                        ),
                    )
                tenants[tenant] = {
                    "anchor": snap["anchor"],
                    "count": snap["count"],
                    "head": snap["head"],
                }
        finally:
            conn.close()
        db_sha256 = _file_sha256(out)
        manifest_body = {
            "created_at": Clock().now(),
            "db_sha256": db_sha256,
            "format": BACKUP_FORMAT,
            "tenants": tenants,
        }
        try:
            mfd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(mfd, json.dumps(manifest_body, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            os.close(mfd)
            os.chmod(manifest_path, 0o600)
        except OSError:
            _unlink_quiet(out)
            _unlink_quiet(manifest_path)
            return 2, _fail("manifest_write_failed")
    except sqlite3.Error:
        _unlink_quiet(out)
        _unlink_quiet(manifest_path)
        return 2, _fail("backup_failed")
    return (
        0,
        {
            "db_sha256": db_sha256,
            "manifest": manifest_path,
            "ok": True,
            "out": out,
            "tenants": tenants,
        },
    )


def _load_manifest(manifest_path: str) -> tuple[int, dict | None, dict | None]:
    try:
        st = os.lstat(manifest_path)
    except OSError:
        return 2, None, _fail("manifest_invalid")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return 2, None, _fail("manifest_invalid")
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 2, None, _fail("manifest_invalid")
    if not isinstance(manifest, dict):
        return 2, None, _fail("manifest_invalid")
    if manifest.get("format") != BACKUP_FORMAT:
        return 2, None, _fail("manifest_invalid")
    tenants = manifest.get("tenants")
    db_sha256 = manifest.get("db_sha256")
    if not isinstance(db_sha256, str) or not isinstance(tenants, dict):
        return 2, None, _fail("manifest_invalid")
    return 0, manifest, None


def run_gate_restore(
    backup: str,
    db: str,
    expect_anchors: list[str] | None = None,
) -> tuple[int, dict]:
    if not os.path.isabs(backup):
        return 2, _fail("backup_path_not_absolute")
    try:
        st = os.lstat(backup)
    except OSError:
        return 2, _fail("backup_unreadable")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return 2, _fail("backup_unreadable")
    if stat.S_IMODE(st.st_mode) & 0o077:
        return 2, _fail("backup_mode_insecure")
    manifest_path = backup + ".manifest.json"
    code, manifest, err = _load_manifest(manifest_path)
    if err is not None:
        return code, err
    parse_err, expected_map = _parse_expect_anchors(expect_anchors)
    if parse_err is not None:
        return 2, _fail(parse_err)
    if not os.path.isabs(db):
        return 2, _fail("db_path_not_absolute")
    if os.path.lexists(db):
        return 2, _fail("db_exists")
    parent = os.path.dirname(db)
    if not os.path.isdir(parent):
        return 2, _fail("db_dir_missing")
    if _parent_writable_bits(parent):
        return 2, _fail("db_dir_insecure")
    file_hash = _file_sha256(backup)
    if file_hash != manifest["db_sha256"]:
        return 1, _fail("backup_hash_mismatch")
    try:
        conn = _open_ro(backup)
        try:
            if not _integrity_ok(conn):
                return 1, _fail("backup_integrity_failed")
            ok, tenant = _manifest_matches_db(conn, manifest)
            if not ok:
                extra = {"tenant": tenant} if tenant else {}
                return 1, _fail("manifest_mismatch", **extra)
        finally:
            conn.close()
        try:
            fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            os.chmod(db, 0o600)
            src = sqlite3.connect(sqlite_ro_uri(backup), uri=True)
            dst = sqlite3.connect(db)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        except (sqlite3.Error, OSError):
            _unlink_quiet(db)
            return 2, _fail("restore_failed")
        conn = _open_ro(db)
        try:
            if not _integrity_ok(conn):
                _unlink_quiet(db)
                return 1, _fail("restore_verify_failed")
            ok, tenant = _manifest_matches_db(conn, manifest)
            if not ok:
                _unlink_quiet(db)
                extra = {"tenant": tenant} if tenant else {}
                return 1, _fail("restore_verify_failed", **extra)
            manifest_tenants = manifest["tenants"]
            for tenant, expected_anchor in expected_map.items():
                if tenant not in manifest_tenants:
                    _unlink_quiet(db)
                    return (
                        1,
                        _fail(
                            "anchor_mismatch",
                            actual=None,
                            expected=expected_anchor,
                            tenant=tenant,
                        ),
                    )
                snap = _tenant_snapshot(conn, tenant)
                actual = snap["anchor"]
                if actual != expected_anchor:
                    _unlink_quiet(db)
                    return (
                        1,
                        _fail(
                            "anchor_mismatch",
                            actual=actual,
                            expected=expected_anchor,
                            tenant=tenant,
                        ),
                    )
            result_tenants = {}
            for tenant in sorted(manifest_tenants.keys()):
                snap = _tenant_snapshot(conn, tenant)
                result_tenants[tenant] = {
                    "anchor": snap["anchor"],
                    "count": snap["count"],
                    "head": snap["head"],
                    "stopped": _stopped(conn, tenant),
                }
        finally:
            conn.close()
    except sqlite3.Error:
        _unlink_quiet(db)
        return 2, _fail("restore_failed")
    return 0, {"db": db, "ok": True, "tenants": result_tenants}