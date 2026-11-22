from pathlib import Path

from framework.canon import hash_value
from framework.configplane import ConfigError

PLUGIN_FILES = ("__init__.py", "plugin.yaml")
KIND_ORDER = ("digest_mismatch", "extra", "missing", "symlink")


def plugin_digest(plugin_dir: Path) -> str:
    if not isinstance(plugin_dir, Path):
        plugin_dir = Path(plugin_dir)
    if not plugin_dir.is_dir() or plugin_dir.is_symlink():
        raise ConfigError("PLUGIN_INVALID")
    payload = {}
    for name in PLUGIN_FILES:
        path = plugin_dir / name
        if not path.is_file() or path.is_symlink():
            raise ConfigError("PLUGIN_INVALID")
        payload[name] = path.read_text(encoding="utf-8")
    return hash_value(payload)


def _sort_findings(findings):
    return sorted(findings, key=lambda row: (KIND_ORDER.index(row["kind"]), row["path"]))


def _scan_pycache(cache_dir, add):
    if cache_dir.is_symlink():
        add("symlink", "__pycache__")
        return
    if not cache_dir.is_dir():
        add("extra", "__pycache__")
        return
    try:
        entries = list(cache_dir.iterdir())
    except OSError:
        add("extra", "__pycache__")
        return
    for child in entries:
        rel = "__pycache__/" + child.name
        if child.is_symlink() or child.is_dir():
            add("extra", rel)
        elif not child.is_file() or not child.name.endswith(".pyc"):
            add("extra", rel)


def verify_plugin(installed_dir: Path, expected_digest: str):
    if not isinstance(installed_dir, Path):
        installed_dir = Path(installed_dir)
    findings = []
    seen = set()

    def add(kind, path):
        key = (kind, path)
        if key in seen:
            return
        seen.add(key)
        findings.append({"kind": kind, "path": path})

    try:
        if not installed_dir.exists():
            return [{"kind": "missing", "path": ""}]
        if installed_dir.is_symlink():
            return [{"kind": "symlink", "path": ""}]
        if not installed_dir.is_dir():
            return [{"kind": "missing", "path": ""}]
        for name in PLUGIN_FILES:
            path = installed_dir / name
            if not path.exists():
                add("missing", name)
            elif path.is_symlink():
                add("symlink", name)
        try:
            names = set()
            for entry in installed_dir.iterdir():
                names.add(entry.name)
        except OSError:
            names = set()
        for name in names:
            if name in PLUGIN_FILES:
                continue
            path = installed_dir / name
            if name == "__pycache__":
                _scan_pycache(path, add)
            elif path.is_symlink():
                add("symlink", name)
            else:
                add("extra", name)
        if not any(row["kind"] in ("missing", "symlink") for row in findings):
            try:
                if plugin_digest(installed_dir) != expected_digest:
                    add("digest_mismatch", "")
            except ConfigError:
                add("digest_mismatch", "")
    except Exception:
        pass
    return _sort_findings(findings)