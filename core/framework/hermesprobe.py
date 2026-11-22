import hashlib
import json
from pathlib import Path

from framework.devicecheck import managed_socket_path

PROBE_SCHEMA = "aaf-hermes-probe/1"

PROBE_SCRIPT = '''import json
import sys

PROBE_SCHEMA = "aaf-hermes-probe/1"


def _nested(cfg, keys):
    node = cfg
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _plugins_list(value):
    if value is None:
        return None, "missing"
    if not isinstance(value, list):
        return None, "other"
    if not all(isinstance(item, str) for item in value):
        return None, "other"
    return value, "list"


def _socket_path(value):
    if value is None:
        return None, "missing"
    if isinstance(value, str):
        return value, "str"
    return None, "other"


def _label_ids(value):
    if value is None:
        return None, "missing"
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return value, "list"
        return None, "other"
    if isinstance(value, str):
        return None, "str"
    return None, "other"


def _fail_import():
    line = json.dumps(
        {"error": "hermes_import_failed", "ok": False, "schema": PROBE_SCHEMA},
        sort_keys=True,
    )
    sys.stdout.write(line + "\\n")
    raise SystemExit(2)


def _fail_probe():
    line = json.dumps(
        {"error": "probe_failed", "ok": False, "schema": PROBE_SCHEMA},
        sort_keys=True,
    )
    sys.stdout.write(line + "\\n")
    raise SystemExit(2)


def main():
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_cli.managed_scope import load_managed_config
    except ImportError:
        _fail_import()
    try:
        cfg = load_config_readonly() or {}
        if not isinstance(cfg, dict):
            cfg = {}
        managed_loaded = bool(load_managed_config())
        plugins = _nested(cfg, ["plugins"])
        if not isinstance(plugins, dict):
            plugins = {}
        enabled_value, enabled_type = _plugins_list(plugins.get("enabled"))
        disabled_value, disabled_type = _plugins_list(plugins.get("disabled"))
        settings = _nested(plugins, ["entries", "aaf-gate", "settings"])
        if not isinstance(settings, dict):
            settings = {}
        socket_value, socket_type = _socket_path(settings.get("socket_path"))
        label_value, label_type = _label_ids(settings.get("label_ids"))
        body = {
            "label_ids": label_value,
            "label_ids_type": label_type,
            "managed_loaded": managed_loaded,
            "ok": True,
            "plugins_disabled": disabled_value,
            "plugins_disabled_type": disabled_type,
            "plugins_enabled": enabled_value,
            "plugins_enabled_type": enabled_type,
            "schema": PROBE_SCHEMA,
            "socket_path": socket_value,
            "socket_path_type": socket_type,
        }
        line = json.dumps(body, sort_keys=True)
        sys.stdout.write(line + "\\n")
    except SystemExit:
        raise
    except Exception:
        _fail_probe()


if __name__ == "__main__":
    main()
'''


def evaluate_probe(package_dir, probe_text):
    root = Path(package_dir)
    try:
        provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        managed_text = (root / "managed" / "config.yaml").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return 2, {"error": "package_unreadable", "ok": False}
    socket_expected = managed_socket_path(managed_text)
    if socket_expected is None:
        return 2, {"error": "package_without_gate", "ok": False}
    try:
        probe = json.loads(probe_text.strip().splitlines()[0])
    except (json.JSONDecodeError, IndexError):
        return 2, {"error": "probe_unreadable", "ok": False}
    if not isinstance(probe, dict) or probe.get("schema") != PROBE_SCHEMA:
        return 2, {"error": "probe_unreadable", "ok": False}
    from framework.appliance import PLUGINS_DISABLED, PLUGINS_ENABLED

    issues = []
    if probe.get("ok") is not True:
        issues.append("probe_failed")
    if probe.get("managed_loaded") is not True:
        issues.append("managed_not_loaded")
    enabled = probe.get("plugins_enabled")
    if enabled != list(PLUGINS_ENABLED):
        issues.append("plugins_enabled_mismatch")
    disabled = probe.get("plugins_disabled")
    if sorted(disabled or []) != sorted(PLUGINS_DISABLED):
        issues.append("plugins_disabled_mismatch")
    socket = probe.get("socket_path")
    if socket != socket_expected:
        issues.append("socket_path_mismatch")
    label_type = probe.get("label_ids_type")
    if label_type != "list":
        issues.append("label_ids_not_list")
    elif probe.get("label_ids") != (provenance.get("label_ids") or []):
        issues.append("label_ids_mismatch")
    probe_sha = hashlib.sha256(probe_text.encode("utf-8")).hexdigest()
    body = {
        "issues": sorted(set(issues)),
        "label_ids_type": label_type,
        "managed_loaded": probe.get("managed_loaded"),
        "ok": not issues,
        "probe_sha256": probe_sha,
        "release_hash": provenance.get("release_hash"),
    }
    return (0 if not issues else 1), body