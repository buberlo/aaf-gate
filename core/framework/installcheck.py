import os
from pathlib import Path

from framework.canon import hash_value
from framework.configplane import ConfigError
from framework.devicemode import load_mode

KIND_ORDER = ("changed", "device_mode_invalid", "extra", "missing", "package_hash_mismatch", "symlink")


def package_files(package_dir: Path) -> dict[str, str]:
    if not package_dir.is_dir():
        raise ConfigError("PACKAGE_INVALID")
    files = {}
    root = package_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        for name in dirnames:
            if (current / name).is_symlink():
                raise ConfigError("PACKAGE_INVALID")
        for name in filenames:
            path = current / name
            if path.is_symlink():
                raise ConfigError("PACKAGE_INVALID")
            relative = path.relative_to(root).as_posix()
            files[relative] = path.read_text(encoding="utf-8")
    return files


def _first_symlink(installed_root: Path, relative: str) -> str | None:
    current = installed_root
    for index, part in enumerate(relative.split("/")):
        current = current / part
        if current.is_symlink():
            return "/".join(relative.split("/")[: index + 1])
    return None


def verify_installation(package_dir: Path, installed_root: Path, package_hash: str | None = None) -> list[dict]:
    package = package_files(package_dir)
    if package_hash is not None and hash_value(package) != package_hash:
        return [{"kind": "package_hash_mismatch", "path": ""}]
    issues = []
    seen = set()
    symlink_paths = set()
    installed_root = installed_root.resolve()

    def add_issue(kind: str, path: str) -> None:
        key = (kind, path)
        if key in seen:
            return
        seen.add(key)
        issues.append({"kind": kind, "path": path})

    def add_symlink(path: str) -> None:
        if path in symlink_paths:
            return
        symlink_paths.add(path)
        add_issue("symlink", path)

    for relative, content in sorted(package.items()):
        link = _first_symlink(installed_root, relative)
        if link is not None:
            add_symlink(link)
            continue
        target = installed_root / relative
        if not target.is_file():
            add_issue("missing", relative)
            continue
        if target.read_bytes() != content.encode("utf-8"):
            add_issue("changed", relative)

    package_tops = {part.split("/")[0] for part in package if "/" in part}
    for entry in sorted(installed_root.iterdir(), key=lambda item: item.name):
        relative = entry.name
        if entry.is_symlink():
            add_symlink(relative)
            continue
        if entry.is_file():
            if relative not in package:
                add_issue("extra", relative)
            continue
        if not entry.is_dir() or relative not in package_tops:
            continue
        for dirpath, dirnames, filenames in os.walk(entry, followlinks=False):
            current = Path(dirpath)
            for name in dirnames:
                child = current / name
                if child.is_symlink():
                    add_symlink(child.relative_to(installed_root).as_posix())
            for name in filenames:
                path = current / name
                subrelative = path.relative_to(installed_root).as_posix()
                if path.is_symlink():
                    add_symlink(subrelative)
                elif subrelative not in package:
                    add_issue("extra", subrelative)

    mode_key = "device/device-mode.json"
    blocked_symlink = mode_key in symlink_paths or any(
        item["kind"] == "symlink" and (item["path"] == mode_key or mode_key.startswith(item["path"] + "/"))
        for item in issues
    )
    has_missing = any(item["kind"] == "missing" and item["path"] == mode_key for item in issues)
    if mode_key in package and not has_missing and not blocked_symlink:
        mode_path = installed_root / mode_key
        if mode_path.is_file():
            try:
                load_mode(mode_path)
            except ConfigError:
                add_issue("device_mode_invalid", mode_key)
    issues.sort(key=lambda item: (KIND_ORDER.index(item["kind"]), item["path"]))
    return issues