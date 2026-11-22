import os
import re
import stat
from pathlib import Path

HOOKS_LINE = re.compile(r"^hooks:(.*)$")
PLUGIN_NAME_LINE = re.compile(r"^name:\s*(.*)$")
EMPTY_HOOKS_VALUES = frozenset({"{}", "[]", "null", "~"})
GATE_PLUGIN_NAME = "aaf-gate"


def verify_hermes_home(home):
    if not isinstance(home, Path):
        home = Path(home)
    issues: list[str] = []

    def add(item: str) -> None:
        issues.append(item)

    try:
        st_home = os.lstat(home)
    except FileNotFoundError:
        return ["home_missing"]
    except NotADirectoryError:
        return ["home_missing"]
    except OSError:
        return ["home_unreadable"]

    if stat.S_ISLNK(st_home.st_mode):
        return ["home_symlink"]

    if not stat.S_ISDIR(st_home.st_mode):
        return ["home_missing"]

    hooks_unreadable = False
    plugins_unreadable = False
    plugins_is_symlink = False
    for name in ("hooks", "plugins"):
        sub = home / name
        try:
            st = os.lstat(sub)
        except FileNotFoundError:
            continue
        except OSError:
            add(f"unreadable:{name}")
            if name == "hooks":
                hooks_unreadable = True
            if name == "plugins":
                plugins_unreadable = True
            continue
        if stat.S_ISLNK(st.st_mode):
            add(f"symlink:{name}")
            if name == "plugins":
                plugins_is_symlink = True
        elif st.st_mode & 0o022:
            add(f"writable_by_others:{name}")

    hooks_path = home / "hooks"
    if not hooks_unreadable:
        try:
            st_hooks = os.lstat(hooks_path)
        except FileNotFoundError:
            st_hooks = None
        except OSError:
            add("unreadable:hooks")
            st_hooks = None
            hooks_unreadable = True
    else:
        st_hooks = None

    if (
        not hooks_unreadable
        and st_hooks is not None
        and stat.S_ISDIR(st_hooks.st_mode)
        and not stat.S_ISLNK(st_hooks.st_mode)
    ):
        try:
            entries = os.listdir(hooks_path)
        except OSError:
            add("unreadable:hooks")
            entries = []
        for entry in sorted(entries):
            entry_path = hooks_path / entry
            try:
                st_e = os.lstat(entry_path)
            except FileNotFoundError:
                continue
            except OSError:
                add(f"unreadable:hooks/{entry}")
                continue
            if stat.S_ISLNK(st_e.st_mode):
                add(f"symlink:hooks/{entry}")
                continue
            if stat.S_ISDIR(st_e.st_mode):
                hook_yaml = entry_path / "HOOK.yaml"
                handler = entry_path / "handler.py"
                try:
                    os.lstat(hook_yaml)
                    os.lstat(handler)
                    add(f"gateway_hook:hooks/{entry}")
                except OSError:
                    pass

    plugins_path = home / "plugins"
    if not plugins_unreadable and not plugins_is_symlink:
        try:
            st_plugins = os.lstat(plugins_path)
        except FileNotFoundError:
            st_plugins = None
        except OSError:
            add("unreadable:plugins")
            st_plugins = None
            plugins_unreadable = True
    else:
        st_plugins = None

    if (
        not plugins_unreadable
        and not plugins_is_symlink
        and st_plugins is not None
        and stat.S_ISDIR(st_plugins.st_mode)
        and not stat.S_ISLNK(st_plugins.st_mode)
    ):
        try:
            plugin_entries = os.listdir(plugins_path)
        except OSError:
            add("unreadable:plugins")
            plugin_entries = []
        for entry in sorted(plugin_entries):
            if entry.startswith("__") and entry.endswith("__"):
                continue
            entry_path = plugins_path / entry
            try:
                st_e = os.lstat(entry_path)
            except FileNotFoundError:
                continue
            except OSError:
                add(f"unreadable:plugins/{entry}")
                continue
            if entry != GATE_PLUGIN_NAME:
                add(f"foreign_plugin:plugins/{entry}")
                continue
            if stat.S_ISLNK(st_e.st_mode):
                add("symlink:plugins/aaf-gate")
                continue
            if not stat.S_ISDIR(st_e.st_mode):
                add("gate_plugin_invalid")
                continue
            plugin_yaml = entry_path / "plugin.yaml"
            try:
                yaml_text = plugin_yaml.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                add("gate_plugin_invalid")
                continue
            if not _plugin_name_is_gate(yaml_text):
                add("gate_plugin_invalid")

    config_path = home / "config.yaml"
    try:
        st_cfg = os.lstat(config_path)
    except FileNotFoundError:
        st_cfg = None
    except OSError:
        add("unreadable:config.yaml")
        st_cfg = None

    if st_cfg is not None:
        if stat.S_ISLNK(st_cfg.st_mode):
            add("symlink:config.yaml")
        else:
            if st_cfg.st_mode & 0o022:
                add("writable_by_others:config.yaml")
            try:
                text = config_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                add("config_unreadable:config.yaml")
                text = None
            except OSError:
                add("unreadable:config.yaml")
                text = None
            if text is not None:
                _scan_config_hooks(text, add)

    return sorted(set(issues))


def _plugin_name_is_gate(text: str) -> bool:
    for line in text.splitlines():
        match = PLUGIN_NAME_LINE.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1].strip()
        return value == GATE_PLUGIN_NAME
    return False


def _scan_config_hooks(text: str, add) -> None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = HOOKS_LINE.match(line)
        if not match:
            continue
        rest = match.group(1).split("#", 1)[0].strip()
        if rest in EMPTY_HOOKS_VALUES:
            continue
        if not rest:
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("#"):
                    j += 1
                    continue
                if lines[j][:1] in (" ", "\t"):
                    add("shell_hooks:config.yaml")
                break
            continue
        add("shell_hooks:config.yaml")