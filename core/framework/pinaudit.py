import ast
import json
import os
import re
from pathlib import Path

from framework.configplane import ConfigError

TOOL_HOOKS = frozenset({"pre_tool_call", "post_tool_call", "transform_tool_result"})
AUTO_KINDS = frozenset({"backend", "platform"})
NAME_LINE = re.compile(r"^name:\s*(.*)$")
KIND_LINE = re.compile(r"^kind:\s*(.*)$")


def _strip_field(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _field_from_text(text: str, pattern: re.Pattern[str]) -> str | None:
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return _strip_field(match.group(1))
    return None


def read_manifest(directory: Path) -> tuple[str, str] | None:
    directory = Path(directory)
    yaml_path = directory / "plugin.yaml"
    if yaml_path.is_file():
        text = yaml_path.read_text(encoding="utf-8")
        name = _field_from_text(text, NAME_LINE) or directory.name
        kind = _field_from_text(text, KIND_LINE) or ""
        return (name, kind)
    json_path = directory / "plugin.json"
    if json_path.is_file():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        name = str(data.get("name", directory.name))
        kind = str(data.get("kind", ""))
        return (name, kind)
    return None


def _plugin_list_from_config(text: str, list_key: str) -> list[str]:
    header = "  " + list_key + ":"
    in_plugins = False
    active_list: str | None = None
    names: list[str] = []
    for line in text.splitlines():
        if line == "plugins:":
            in_plugins = True
            active_list = None
            continue
        if not in_plugins:
            continue
        if active_list == list_key:
            if line.strip() == "":
                continue
            if line == header:
                continue
            if not line.startswith("    - "):
                indent = len(line) - len(line.lstrip(" "))
                if indent <= 2 and line.strip():
                    active_list = None
                continue
            names.append(line[6:].strip())
            continue
        if line == header:
            active_list = list_key
        elif line and not line.startswith(" "):
            in_plugins = False
            active_list = None
    return names


def disabled_from_config(text: str) -> list[str]:
    return _plugin_list_from_config(text, "disabled")


def enabled_from_config(text: str) -> list[str]:
    return _plugin_list_from_config(text, "enabled")


class _HookVisitor(ast.NodeVisitor):
    def __init__(self):
        self.hooks: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "register_hook":
                hook = "dynamic"
                if node.args and isinstance(node.args[0], ast.Constant):
                    value = node.args[0].value
                    if isinstance(value, str):
                        hook = value
                self.hooks.append(hook)
            elif node.func.attr == "register_middleware":
                kind = "dynamic"
                if node.args and isinstance(node.args[0], ast.Constant):
                    value = node.args[0].value
                    if isinstance(value, str):
                        kind = value
                self.hooks.append("middleware:" + kind)
        self.generic_visit(node)


def _plugin_root(py_file: Path, root: Path) -> Path | None:
    current = py_file.parent
    while True:
        if read_manifest(current) is not None:
            return current
        if current == root:
            return None
        current = current.parent


def audit_plugins(source, disabled) -> dict:
    root = Path(source) / "plugins"
    if not root.is_dir():
        raise ConfigError("plugins_missing")
    issues: list[str] = []
    plugin_hooks: dict[tuple[str, str], set[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            py_file = base / filename
            rel = py_file.relative_to(root).as_posix()
            try:
                text = py_file.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                issues.append(f"unparseable:{rel}")
                continue
            except (UnicodeDecodeError, OSError):
                issues.append(f"unparseable:{rel}")
                continue
            visitor = _HookVisitor()
            visitor.visit(tree)
            if not visitor.hooks:
                continue
            manifest_dir = _plugin_root(py_file, root)
            if manifest_dir is None:
                issues.append(f"orphan_hook:{rel}")
                continue
            manifest = read_manifest(manifest_dir)
            if manifest is None:
                issues.append(f"orphan_hook:{rel}")
                continue
            name, kind = manifest
            key = (name, kind)
            bucket = plugin_hooks.setdefault(key, set())
            bucket.update(visitor.hooks)
    disabled_set = set(disabled)
    for (name, kind), hooks in plugin_hooks.items():
        if name in disabled_set:
            continue
        if (
            kind in AUTO_KINDS
            or hooks & TOOL_HOOKS
            or "dynamic" in hooks
            or any(entry.startswith("middleware:") for entry in hooks)
        ):
            issues.append(f"hook_plugin_not_disabled:{name}")
    plugins = [
        {"name": name, "kind": kind, "hooks": sorted(hooks)}
        for (name, kind), hooks in sorted(plugin_hooks.items())
    ]
    return {"plugins": plugins, "issues": sorted(set(issues))}