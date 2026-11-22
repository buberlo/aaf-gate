import json
import os
import plistlib
import re
import sqlite3
import stat
from pathlib import Path

from framework import audit
from framework.canon import hash_value
from framework.configplane import ConfigError
from framework.dumpcheck import verify_dump_commit
from framework.hookcheck import verify_hermes_home
from framework.installcheck import package_files, verify_installation
from framework.launchdcheck import package_launchd_expectation, verify_launchd_file
from framework.plugincheck import verify_plugin
from framework.pinaudit import disabled_from_config, enabled_from_config
from framework.relaycheck import verify_relay

GATE_ONLY_ENABLED = ["aaf-gate"]
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
AUDIT_ANCHOR_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def package_customer_profile(package) -> tuple[bool, str | None]:
    prov_path = Path(package) / "provenance.json"
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (False, None)
    if not isinstance(data, dict):
        return (False, None)
    if "customer_profile" not in data:
        return (True, None)
    value = data.get("customer_profile")
    if value is None:
        return (True, None)
    if isinstance(value, str):
        return (True, value)
    return (False, None)


def package_tenant_id(package) -> tuple[bool, str | None]:
    prov_path = Path(package) / "provenance.json"
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (False, None)
    if not isinstance(data, dict):
        return (False, None)
    value = data.get("tenant_id")
    if not isinstance(value, str):
        return (True, None)
    return (True, value)


def package_plugin_digest(package) -> str | None:
    path = Path(package) / "provenance.json"
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        digest = data.get("hermes_plugin_digest")
        if not isinstance(digest, str):
            return None
        return digest
    except Exception:
        return None


def check_hooks(hermes_home) -> dict:
    issues = verify_hermes_home(hermes_home)
    return {"issues": issues, "ok": not issues}


def check_plugin(package, plugin_dir) -> dict:
    digest = package_plugin_digest(package)
    if digest is None:
        return {"error": "PLUGIN_DIGEST_UNSET", "ok": False}
    try:
        issues = verify_plugin(Path(plugin_dir), digest)
    except ConfigError as exc:
        return {"error": exc.code, "ok": False}
    return {"issues": issues, "ok": not issues}


def check_launchd(installed, package) -> dict:
    try:
        label, digest = package_launchd_expectation(package)
    except ConfigError as exc:
        return {"error": exc.code, "ok": False}
    issues = verify_launchd_file(installed, digest, expected_uid=0)
    body = {"issues": issues, "label": label, "ok": not issues}
    return body


def check_relay(environ, home, system_root) -> dict:
    issues = verify_relay(environ, home, system_root)
    return {"issues": issues, "ok": not issues}


def check_hermes_dump(dump_file, package) -> dict:
    try:
        text = Path(dump_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"error": "dump_unreadable", "ok": False}
    prov_path = Path(package) / "provenance.json"
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "provenance_unreadable", "ok": False}
    if not isinstance(data, dict):
        return {"error": "provenance_unreadable", "ok": False}
    commit = data.get("hermes_commit")
    if not isinstance(commit, str):
        commit = None
    issues = verify_dump_commit(text, commit)
    return {"issues": issues, "ok": not issues}


def check_install(package, installed_root, package_hash) -> dict:
    try:
        issues = verify_installation(Path(package), Path(installed_root), package_hash)
    except ConfigError as exc:
        return {"error": exc.code, "ok": False}
    return {"issues": issues, "ok": not issues}


def profile_gate_settings_present(text: str) -> bool:
    for line in text.splitlines():
        if line == "    - aaf-gate":
            continue
        if "aaf-gate" in line or "socket_path" in line or "label_ids" in line:
            return True
    return False


def check_profile_plugins(profile_home, required_disabled) -> dict:
    config = Path(profile_home) / "config.yaml"
    try:
        st = os.lstat(config)
    except OSError:
        return {"issues": ["config_missing"], "ok": False}
    if stat.S_ISLNK(st.st_mode):
        return {"issues": ["config_symlink"], "ok": False}
    if not stat.S_ISREG(st.st_mode):
        return {"issues": ["config_missing"], "ok": False}
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"issues": ["config_missing"], "ok": False}
    issues: list[str] = []
    enabled = enabled_from_config(text)
    if enabled != GATE_ONLY_ENABLED:
        issues.append("plugins_enabled_not_gate_only")
    present = set(disabled_from_config(text))
    for name in required_disabled:
        if name not in present:
            issues.append("plugins_disabled_missing:" + name)
    if profile_gate_settings_present(text):
        issues.append("gate_settings_in_profile")
    return {"issues": sorted(issues), "ok": not issues}


def check_active_profile(hermes_home, expected) -> dict:
    home = Path(hermes_home)
    if expected != "default" and PROFILE_ID_RE.fullmatch(expected) is None:
        return {"error": "expected_profile_invalid", "ok": False}
    issues: list[str] = []
    if expected != "default" and not (home / "profiles" / expected).is_dir():
        issues.append("expected_profile_missing")
    active_path = home / "active_profile"
    actual = None
    try:
        st = os.lstat(active_path)
    except OSError:
        actual = "default"
    else:
        if stat.S_ISLNK(st.st_mode):
            issues.append("active_profile_symlink")
        elif not stat.S_ISREG(st.st_mode):
            issues.append("active_profile_unreadable")
        else:
            try:
                raw = active_path.read_text(encoding="utf-8-sig").strip()
                actual = raw if raw else "default"
            except (OSError, UnicodeDecodeError):
                issues.append("active_profile_unreadable")
    if actual is not None:
        if actual != "default" and PROFILE_ID_RE.fullmatch(actual) is None:
            issues.append("active_profile_invalid")
        elif actual != "default" and not (home / "profiles" / actual).is_dir():
            issues.append("active_profile_dangling")
        if actual != expected:
            issues.append("active_profile_mismatch")
    return {
        "active": actual,
        "expected": expected,
        "issues": sorted(set(issues)),
        "ok": not issues,
    }


def check_profile_set(hermes_home, allowed) -> dict:
    allowed_list = list(allowed)
    for name in allowed_list:
        if name == "default" or PROFILE_ID_RE.fullmatch(name) is None:
            return {"error": "allowed_profile_invalid", "ok": False}
    issues: list[str] = []
    present: list[str] = []
    profiles = Path(hermes_home) / "profiles"
    try:
        st = os.lstat(profiles)
    except OSError:
        for name in allowed_list:
            issues.append("profile_missing:" + name)
        return {
            "allowed": sorted(set(allowed_list)),
            "issues": sorted(set(issues)),
            "ok": not issues,
            "present": [],
        }
    if stat.S_ISLNK(st.st_mode):
        return {
            "allowed": sorted(set(allowed_list)),
            "issues": ["profiles_symlink"],
            "ok": False,
            "present": [],
        }
    if not stat.S_ISDIR(st.st_mode):
        return {
            "allowed": sorted(set(allowed_list)),
            "issues": ["profiles_not_dir"],
            "ok": False,
            "present": [],
        }
    allowed_set = set(allowed_list)
    for name in sorted(os.listdir(profiles)):
        entry = profiles / name
        try:
            est = os.lstat(entry)
        except OSError:
            issues.append("profile_not_dir:" + name)
            continue
        if stat.S_ISLNK(est.st_mode):
            issues.append("profile_symlink:" + name)
        elif not stat.S_ISDIR(est.st_mode):
            issues.append("profile_not_dir:" + name)
        elif name not in allowed_set:
            issues.append("profile_unexpected:" + name)
        else:
            present.append(name)
    for name in allowed_list:
        if name not in present:
            issues.append("profile_missing:" + name)
    return {
        "allowed": sorted(set(allowed_list)),
        "issues": sorted(set(issues)),
        "ok": not issues,
        "present": sorted(present),
    }


def managed_socket_path(text: str) -> str | None:
    phase = None
    for line in text.splitlines():
        if line == "plugins:":
            phase = "plugins"
            continue
        if phase is None:
            continue
        if phase == "plugins":
            if line.strip() and not line.startswith(" "):
                return None
            if line == "  entries:":
                phase = "entries"
            continue
        if phase == "entries":
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent < 2:
                return None
            if line == "    aaf-gate:":
                phase = "aaf-gate"
            continue
        if phase == "aaf-gate":
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent < 4:
                return None
            if line == "      settings:":
                phase = "settings"
            continue
        if phase == "settings":
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent < 6:
                return None
            if line.startswith("        socket_path: "):
                payload = line[len("        socket_path: ") :]
                try:
                    value = json.loads(payload)
                except json.JSONDecodeError:
                    return None
                if isinstance(value, str) and value:
                    return value
                return None
    return None


def sqlite_ro_uri(path: str) -> str:
    encoded: list[str] = []
    for ch in path:
        if (
            ("A" <= ch <= "Z")
            or ("a" <= ch <= "z")
            or ("0" <= ch <= "9")
            or ch in "/._-~"
        ):
            encoded.append(ch)
        else:
            encoded.append("%{:02X}".format(ord(ch)))
    return "file:" + "".join(encoded) + "?mode=ro"


def parse_audit_anchor(text: str) -> tuple[int, str] | None:
    if not isinstance(text, str) or not text:
        return None
    if ":" not in text:
        return None
    seq_part, hash_part = text.split(":", 1)
    if not seq_part.isdecimal():
        return None
    if len(seq_part) > 1 and seq_part[0] == "0":
        return None
    seq = int(seq_part)
    if seq < 1:
        return None
    if AUDIT_ANCHOR_HASH_RE.fullmatch(hash_part) is None:
        return None
    return (seq, hash_part)


def _gate_plist_db_access(launchd_plist, package):
    issues: list[str] = []
    readable, tenant = package_tenant_id(package)
    if not readable or tenant is None:
        issues.append("package_tenant_missing")
        return issues, None, None, None
    try:
        st = os.lstat(launchd_plist)
    except OSError:
        issues.append("plist_unreadable")
        return issues, tenant, None, None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        issues.append("plist_unreadable")
        return issues, tenant, None, None
    try:
        plist_bytes = Path(launchd_plist).read_bytes()
    except OSError:
        issues.append("plist_unreadable")
        return issues, tenant, None, None
    args = plist_gate_args(plist_bytes)
    if args is None:
        issues.append("plist_args_missing")
        return issues, tenant, None, None
    db = args["db"]
    principal = args["principal"]
    try:
        st = os.lstat(db)
    except OSError:
        issues.append("gate_db_unreadable")
        return issues, tenant, db, principal
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        issues.append("gate_db_unreadable")
        return issues, tenant, db, principal
    return issues, tenant, db, principal


def plist_gate_args(data: bytes) -> dict | None:
    try:
        plist = plistlib.loads(data)
    except Exception:
        return None
    args = plist.get("ProgramArguments")
    if not isinstance(args, list):
        return None
    db = None
    principal = None
    for index, entry in enumerate(args):
        if entry == "--db" and index + 1 < len(args):
            value = args[index + 1]
            if isinstance(value, str) and value:
                db = value
        if entry == "--principal" and index + 1 < len(args):
            value = args[index + 1]
            if isinstance(value, str) and value:
                principal = value
    if db is None or principal is None:
        return None
    return {"db": db, "principal": principal}


def check_gate_tenant(launchd_plist, package) -> dict:
    issues: list[str] = []
    package_tenant = None
    gate_tenant = None
    principal = None

    def body():
        return {
            "gate_tenant": gate_tenant,
            "issues": sorted(set(issues)),
            "ok": not issues,
            "package_tenant": package_tenant,
            "principal": principal,
        }

    pre_issues, tenant, db, gate_principal = _gate_plist_db_access(launchd_plist, package)
    issues.extend(pre_issues)
    if pre_issues:
        if tenant is not None:
            package_tenant = tenant
        if gate_principal is not None:
            principal = gate_principal
        return body()
    package_tenant = tenant
    principal = gate_principal

    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        row = conn.execute(
            "SELECT tenant_id, kind, disabled FROM principals WHERE principal_id=?",
            (principal,),
        ).fetchone()
    except sqlite3.Error:
        issues.append("gate_db_unreadable")
        return body()
    finally:
        if conn is not None:
            conn.close()

    if row is None:
        issues.append("gate_principal_missing")
        return body()
    gate_tenant = row[0]
    if row[2] != 0:
        issues.append("gate_principal_disabled")
    if gate_tenant != package_tenant:
        issues.append("gate_tenant_mismatch")
    return body()


def check_gate_audit(launchd_plist, package, anchor=None) -> dict:
    issues: list[str] = []
    anchor_text = None
    if anchor is not None:
        anchor_text = f"{anchor[0]}:{anchor[1]}"

    def body(count=None, head=None):
        return {
            "anchor": anchor_text,
            "count": count,
            "head": head,
            "issues": sorted(set(issues)),
            "ok": not issues,
        }

    pre_issues, tenant, db, _principal = _gate_plist_db_access(launchd_plist, package)
    issues.extend(pre_issues)
    if pre_issues:
        return body()

    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(db), uri=True)
        conn.row_factory = sqlite3.Row
        verify_result = audit.verify(conn, tenant)
        count = verify_result.get("count")
        head = verify_result.get("head")
        if verify_result.get("first_break") is not None:
            issues.append("audit_chain_broken")
        if verify_result.get("head_mismatch"):
            issues.append("audit_head_mismatch")
        if anchor is not None:
            row = conn.execute(
                "SELECT event_hash FROM audit_events WHERE tenant_id=? AND sequence=?",
                (tenant, anchor[0]),
            ).fetchone()
            if row is None:
                issues.append("audit_anchor_missing")
            elif row[0] != anchor[1]:
                issues.append("audit_anchor_mismatch")
        return body(count, head)
    except sqlite3.Error:
        issues.append("gate_db_unreadable")
        return body()
    finally:
        if conn is not None:
            conn.close()


def plist_socket_path(data: bytes) -> str | None:
    try:
        plist = plistlib.loads(data)
    except Exception:
        return None
    args = plist.get("ProgramArguments")
    if not isinstance(args, list):
        return None
    for index, entry in enumerate(args):
        if entry == "--socket" and index + 1 < len(args):
            value = args[index + 1]
            if isinstance(value, str) and value:
                return value
            return None
    return None


def check_socket_path(launchd_plist, managed_dir) -> dict:
    issues: list[str] = []
    plist_value = None
    managed_value = None
    try:
        st = os.lstat(launchd_plist)
    except OSError:
        issues.append("plist_unreadable")
    else:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            issues.append("plist_unreadable")
        else:
            try:
                data = Path(launchd_plist).read_bytes()
            except OSError:
                issues.append("plist_unreadable")
            else:
                plist_value = plist_socket_path(data)
                if plist_value is None:
                    issues.append("plist_socket_missing")
    target = Path(managed_dir) / "config.yaml"
    try:
        st = os.lstat(target)
    except OSError:
        issues.append("managed_unreadable")
    else:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            issues.append("managed_unreadable")
        else:
            try:
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                issues.append("managed_unreadable")
            else:
                managed_value = managed_socket_path(text)
                if managed_value is None:
                    issues.append("managed_socket_missing")
    if (
        plist_value is not None
        and managed_value is not None
        and plist_value != managed_value
    ):
        issues.append("socket_path_mismatch")
    return {
        "issues": sorted(set(issues)),
        "managed": managed_value,
        "ok": not issues,
        "plist": plist_value,
    }


def check_managed(package, managed_dir) -> dict:
    source = Path(package) / "managed" / "config.yaml"
    try:
        expected = source.read_text(encoding="utf-8")
    except OSError:
        return {"error": "managed_source_missing", "ok": False}
    target = Path(managed_dir) / "config.yaml"
    try:
        st = os.lstat(target)
    except OSError:
        return {"issues": ["managed_config_missing"], "ok": False}
    if stat.S_ISLNK(st.st_mode):
        return {"issues": ["managed_config_symlink"], "ok": False}
    if not stat.S_ISREG(st.st_mode):
        return {"issues": ["managed_config_missing"], "ok": False}
    try:
        actual = target.read_text(encoding="utf-8")
    except OSError:
        return {"issues": ["managed_config_missing"], "ok": False}
    if actual != expected:
        return {"issues": ["managed_config_changed"], "ok": False}
    return {"issues": [], "ok": True}


def package_identity(package) -> dict:
    try:
        files = package_files(Path(package))
    except ConfigError as exc:
        return {"error": exc.code}
    identity = {"package_hash": hash_value(files)}
    prov_path = Path(package) / "provenance.json"
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "provenance_unreadable"}
    if not isinstance(data, dict):
        return {"error": "provenance_unreadable"}
    commit = data.get("hermes_commit")
    identity["hermes_commit"] = commit if isinstance(commit, str) else None
    config_id = data.get("config_id")
    identity["config_id"] = config_id if isinstance(config_id, str) else None
    profile = data.get("customer_profile")
    identity["customer_profile"] = profile if isinstance(profile, str) else None
    tenant_id = data.get("tenant_id")
    identity["tenant_id"] = tenant_id if isinstance(tenant_id, str) else None
    release_hash = data.get("release_hash")
    identity["release_hash"] = release_hash if isinstance(release_hash, str) else None
    release_no = data.get("release_no")
    identity["release_no"] = release_no if isinstance(release_no, int) else None
    return identity


def run_device_checks(
    package,
    hermes_home,
    plugin_dir,
    launchd_plist,
    dump_file,
    home,
    system_root,
    environ,
    *,
    installed_root,
    package_hash,
    managed_dir,
    expected_profile="default",
    allowed_profiles=None,
    audit_anchor=None,
) -> dict:
    checks: dict = {}
    home_path = Path(hermes_home)
    profiles = home_path / "profiles"
    checks["hooks:."] = check_hooks(home_path)
    profile_dirs: list[tuple[str, Path]] = []
    if profiles.is_dir():
        for name in sorted(os.listdir(profiles)):
            sub = profiles / name
            if sub.is_dir():
                profile_dirs.append((name, sub))
                checks["hooks:profiles/" + name] = check_hooks(sub)
    managed_source = Path(package) / "managed" / "config.yaml"
    try:
        managed_text = managed_source.read_text(encoding="utf-8")
        required_disabled = disabled_from_config(managed_text)
        checks["profile_plugins:."] = check_profile_plugins(home_path, required_disabled)
        for name, sub in profile_dirs:
            checks["profile_plugins:profiles/" + name] = check_profile_plugins(
                sub, required_disabled
            )
    except OSError:
        err = {"error": "managed_source_missing", "ok": False}
        checks["profile_plugins:."] = err
        for name, _sub in profile_dirs:
            checks["profile_plugins:profiles/" + name] = err
    checks["active_profile"] = check_active_profile(home_path, expected_profile)
    if allowed_profiles is not None:
        checks["profile_set"] = check_profile_set(home_path, allowed_profiles)
    checks["plugin"] = check_plugin(package, plugin_dir)
    checks["launchd"] = check_launchd(launchd_plist, package)
    checks["gate_tenant"] = check_gate_tenant(launchd_plist, package)
    checks["gate_audit"] = check_gate_audit(launchd_plist, package, audit_anchor)
    checks["relay"] = check_relay(environ, home, system_root)
    checks["hermes_dump"] = check_hermes_dump(dump_file, package)
    checks["install"] = check_install(package, installed_root, package_hash)
    checks["managed"] = check_managed(package, managed_dir)
    checks["socket_path"] = check_socket_path(launchd_plist, managed_dir)
    identity = package_identity(package)
    ok = "error" not in identity and all(entry.get("ok") for entry in checks.values())
    return {"checks": checks, "ok": ok, "package": identity}