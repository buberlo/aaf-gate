import os
import plistlib
import re

from framework import hermes_mcp

LABEL_PATTERN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")
GROUP_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _forbidden_char(value):
    if not isinstance(value, str):
        return True
    return "\n" in value or "\r" in value or "\x00" in value


def _check_absolute(path, code="path_not_absolute"):
    if not os.path.isabs(path):
        raise ValueError(code)


def gate_plist(
    label,
    python,
    workdir,
    db,
    socket_path,
    principal,
    user,
    group=None,
    server="gmail",
    mode="0600",
    unattended=False,
    log_path=None,
):
    values = [label, python, workdir, db, socket_path, principal, user, server, mode]
    if group is not None:
        values.append(group)
    if log_path is not None:
        values.append(log_path)
    for value in values:
        if _forbidden_char(value):
            raise ValueError("argument_invalid")
    if not isinstance(label, str) or LABEL_PATTERN.fullmatch(label) is None:
        raise ValueError("label_invalid")
    for path in (python, workdir, db, socket_path):
        _check_absolute(path)
    if log_path is not None:
        _check_absolute(log_path)
    if not isinstance(user, str) or USER_PATTERN.fullmatch(user) is None:
        raise ValueError("user_invalid")
    if user == "root":
        raise ValueError("user_root_forbidden")
    if not isinstance(principal, str) or not principal:
        raise ValueError("principal_invalid")
    if mode not in ("0600", "0660"):
        raise ValueError("invalid_socket_mode")
    if group is not None:
        if not isinstance(group, str) or GROUP_PATTERN.fullmatch(group) is None:
            raise ValueError("group_invalid")
    hermes_mcp.pilot1_policy(server)
    args = [
        python,
        "-m",
        "framework.cli",
        "gate-serve",
        "--db",
        db,
        "--socket",
        socket_path,
        "--principal",
        principal,
        "--server",
        server,
        "--mode",
        mode,
    ]
    if group is not None:
        args.extend(["--group", group])
    if unattended:
        args.append("--unattended")
    data = {
        "KeepAlive": True,
        "Label": label,
        "ProgramArguments": args,
        "UserName": user,
        "WorkingDirectory": workdir,
    }
    if log_path is not None:
        data["StandardErrorPath"] = log_path
        data["StandardOutPath"] = log_path
    return plistlib.dumps(data, fmt=plistlib.FMT_XML, sort_keys=True)