import json
from pathlib import Path

from framework.configplane import ConfigError
from framework.templates import expected_hashes

ALLOWED_KEYS = frozenset({"mode", "hashes_path"})


def _writable_by_group_or_other(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o022)


def _path_has_symlink(base: Path, relative: Path) -> bool:
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    return False


def _stays_under_base(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def load_mode(path: Path) -> dict:
    if path.is_symlink():
        raise ConfigError("DEVICE_MODE_INVALID")
    if not path.is_file():
        raise ConfigError("DEVICE_MODE_INVALID")
    if _writable_by_group_or_other(path):
        raise ConfigError("DEVICE_MODE_INVALID")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ConfigError("DEVICE_MODE_INVALID")
    if not isinstance(data, dict):
        raise ConfigError("DEVICE_MODE_INVALID")
    if set(data) - ALLOWED_KEYS:
        raise ConfigError("DEVICE_MODE_INVALID")
    mode = data.get("mode")
    if mode not in ("appliance", "development"):
        raise ConfigError("DEVICE_MODE_INVALID")
    if mode == "appliance":
        hashes_path = data.get("hashes_path")
        if not isinstance(hashes_path, str) or not hashes_path:
            raise ConfigError("DEVICE_MODE_INVALID")
        relative = Path(hashes_path)
        if relative.is_absolute():
            raise ConfigError("DEVICE_MODE_INVALID")
        if ".." in relative.parts:
            raise ConfigError("DEVICE_MODE_INVALID")
        resolved = path.parent / relative
        if _path_has_symlink(path.parent, relative):
            raise ConfigError("DEVICE_MODE_INVALID")
        if not _stays_under_base(path.parent, resolved):
            raise ConfigError("DEVICE_MODE_INVALID")
        if not resolved.is_file():
            raise ConfigError("DEVICE_MODE_INVALID")
        if _writable_by_group_or_other(resolved):
            raise ConfigError("DEVICE_MODE_INVALID")
        try:
            expected = expected_hashes(resolved)
        except ConfigError:
            raise ConfigError("DEVICE_MODE_INVALID")
        return {"mode": "appliance", "expected": expected}
    if "hashes_path" in data:
        raise ConfigError("DEVICE_MODE_INVALID")
    return {"mode": "development", "expected": None}