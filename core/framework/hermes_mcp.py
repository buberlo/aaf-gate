import re

from framework.gate import POLICY_SCHEMA

SERVER_NAME = re.compile(r"[a-z0-9]{1,32}")
TOOL_NAME = re.compile(r"[a-z0-9_]{1,64}")

HERMES_PINNED_VERSION = "0.23.1"

RESERVED_SERVER_NAMES = frozenset(
    {
        "browser",
        "catalog",
        "clarify",
        "coding",
        "connections",
        "cronjob",
        "debugging",
        "delegation",
        "discord",
        "file",
        "kanban",
        "memory",
        "onboarding",
        "project",
        "safe",
        "search",
        "skills",
        "terminal",
        "todo",
        "tts",
        "video",
        "vision",
        "web",
    }
)

GMAIL_MCP_URL = "https://gmailmcp.googleapis.com/mcp/v1"

GMAIL_MCP_TOOLS = (
    "create_draft",
    "get_message",
    "get_thread",
    "label_message",
    "label_thread",
    "list_drafts",
    "list_labels",
    "search_threads",
    "unlabel_message",
    "unlabel_thread",
)

PILOT1_CLASSES = {
    "search_threads": "read",
    "get_thread": "read",
    "get_message": "read",
    "list_labels": "read",
    "list_drafts": "read",
    "create_draft": "draft",
    "label_message": "internal_write",
    "label_thread": "internal_write",
    "unlabel_message": "internal_write",
    "unlabel_thread": "internal_write",
}

PILOT1_LEVELS = {"read": "L4", "draft": "L4", "internal_write": "L4"}

PILOT1_LIMITS = {
    "read": {"window_seconds": 3600, "max_calls": 160},
    "draft": {"window_seconds": 3600, "max_calls": 40},
    "internal_write": {"window_seconds": 3600, "max_calls": 80},
}


def _validate_server_name(server):
    if not isinstance(server, str) or not SERVER_NAME.fullmatch(server):
        raise ValueError("invalid_mcp_name")
    if server in RESERVED_SERVER_NAMES:
        raise ValueError("reserved_mcp_name")


def registered_name(server, tool):
    _validate_server_name(server)
    if not isinstance(tool, str) or not TOOL_NAME.fullmatch(tool):
        raise ValueError("invalid_mcp_name")
    return "mcp_" + server + "_" + tool


def pilot1_policy(server="gmail"):
    _validate_server_name(server)
    tool_classes = {
        registered_name(server, t) + ":call": PILOT1_CLASSES[t] for t in GMAIL_MCP_TOOLS
    }
    return {
        "schema_version": POLICY_SCHEMA,
        "tool_classes": tool_classes,
        "levels": dict(PILOT1_LEVELS),
        "limits": {klass: dict(spec) for klass, spec in PILOT1_LIMITS.items()},
    }


def hermes_server_entry(server="gmail"):
    _validate_server_name(server)
    return {server: {"url": GMAIL_MCP_URL, "tools": {"include": list(GMAIL_MCP_TOOLS)}}}


def _mcp_norm(name):
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def check_mcp_servers(mcp_servers, allowed=("gmail",)):
    try:
        if not isinstance(mcp_servers, dict):
            return [{"code": "mcp_servers_invalid", "server": None}]
        allowed_set = frozenset(
            name
            for name in allowed
            if isinstance(name, str)
            and SERVER_NAME.fullmatch(name)
            and name not in RESERVED_SERVER_NAMES
        )
        tool_set = frozenset(GMAIL_MCP_TOOLS)
        findings = []
        seen = set()

        def add(code, server):
            key = (code, server or "")
            if key in seen:
                return
            seen.add(key)
            findings.append({"code": code, "server": server})

        str_keys = []
        valid_allowed = []
        for name in mcp_servers:
            if not isinstance(name, str):
                add("server_name_invalid", None)
                continue
            str_keys.append(name)
            if not SERVER_NAME.fullmatch(name):
                add("server_name_invalid", None)
                continue
            if name in RESERVED_SERVER_NAMES:
                add("server_name_reserved", name)
                continue
            if name not in allowed_set:
                add("server_not_allowed", name)
            else:
                valid_allowed.append(name)
        norms = {_mcp_norm(name): name for name in str_keys}
        for a in str_keys:
            na = _mcp_norm(a)
            for b in str_keys:
                if a == b:
                    continue
                nb = _mcp_norm(b)
                if na == nb or (na + "_").startswith(nb + "_"):
                    add("prefix_collision", None)
        entry_keys = frozenset({"url", "tools", "enabled"})
        tool_keys = frozenset({"include", "exclude"})
        for name in valid_allowed:
            entry = mcp_servers[name]
            if not isinstance(entry, dict):
                add("entry_invalid", name)
                continue
            if entry.get("url") != GMAIL_MCP_URL:
                add("url_mismatch", name)
            tools = entry.get("tools")
            include = tools.get("include") if isinstance(tools, dict) else None
            if include is None or not isinstance(include, list):
                add("include_missing", name)
            else:
                bad_include = False
                for item in include:
                    if not isinstance(item, str):
                        bad_include = True
                        break
                if bad_include:
                    add("include_missing", name)
                else:
                    for item in include:
                        if item not in tool_set:
                            add("include_not_allowed", name)
                            break
            if isinstance(tools, dict) and "exclude" in tools:
                add("exclude_present", name)
            for key in entry:
                if key not in entry_keys:
                    add("entry_key_unknown", name)
            if isinstance(tools, dict):
                for key in tools:
                    if key not in tool_keys:
                        add("entry_key_unknown", name)
            if "enabled" in entry and not isinstance(entry["enabled"], bool):
                add("entry_invalid", name)
        return sorted(findings, key=lambda row: (row["code"], row["server"] or ""))
    except Exception:
        return [{"code": "mcp_servers_invalid", "server": None}]