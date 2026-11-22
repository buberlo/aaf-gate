import hashlib
import json
import os
import re
import socket

PLUGIN_VERSION = "0.5.0"
REQUEST_SCHEMA = "gate-request-0.1-draft"
OBSERVATION_SCHEMA = "gate-observation-0.1-draft"
RUNTIME = "hermes"
DEFAULT_ACTION = "call"
MAX_FRAME = 65536
DEFAULT_TIMEOUT = 5.0
LEDGER_MAX = 1024
LEDGER_DECISIONS = frozenset({"allow", "block", "approve"})
VALID_DECISIONS = frozenset({"allow", "deny", "needs_approval"})
LABEL_TOOL = re.compile(r"mcp_[a-z0-9]{1,32}_(?:un)?label_(?:message|thread)")
SYSTEM_LABEL_IDS = frozenset(
    {
        "INBOX",
        "SPAM",
        "TRASH",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "SENT",
        "DRAFT",
        "CATEGORY_PERSONAL",
        "CATEGORY_SOCIAL",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
    }
)
RESERVED_LABEL = re.compile(r"[A-Z][A-Z0-9_]*")


def label_allowlist(value):
    if not isinstance(value, (list, tuple)) or len(value) < 1:
        return None
    allowed = []
    for entry in value:
        if not isinstance(entry, str) or not entry or len(entry) > 128:
            return None
        if entry in SYSTEM_LABEL_IDS:
            return None
        if RESERVED_LABEL.fullmatch(entry) is not None:
            return None
        allowed.append(entry)
    return frozenset(allowed)


def label_action(tool_name, args, label_ids):
    if LABEL_TOOL.fullmatch(tool_name) is None:
        return DEFAULT_ACTION
    value = args.get("labelIds")
    if not isinstance(value, list) or len(value) == 0:
        return "label_invalid"
    for element in value:
        if not isinstance(element, str) or not element:
            return "label_invalid"
    if label_ids is None:
        return "label_unlisted"
    for element in value:
        if element not in label_ids:
            return "label_unlisted"
    return DEFAULT_ACTION


class DecisionLedger:
    def __init__(self):
        self._entries = {}

    def record(self, tool_call_id, content_hash, decision="allow"):
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return
        if tool_call_id in self._entries:
            del self._entries[tool_call_id]
        if decision not in LEDGER_DECISIONS:
            decision = "block"
        self._entries[tool_call_id] = (content_hash, decision)
        while len(self._entries) > LEDGER_MAX:
            oldest = next(iter(self._entries))
            del self._entries[oldest]

    def pop_entry(self, tool_call_id):
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return None
        return self._entries.pop(tool_call_id, None)

    def pop(self, tool_call_id):
        entry = self.pop_entry(tool_call_id)
        if entry is None:
            return None
        return entry[0]

    def __len__(self):
        return len(self._entries)


def hash_value(value):
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("not_canonical") from exc
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_request(tool_name, args, target_keys=None, label_ids=None):
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("invalid_tool")
    if not isinstance(args, dict):
        raise ValueError("invalid_args")
    content_hash = hash_value(args)
    action = label_action(tool_name, args, label_ids)
    target = ""
    if isinstance(target_keys, dict) and tool_name in target_keys:
        key = target_keys[tool_name]
        value = args.get(key)
        if isinstance(value, str):
            target = value
    return {
        "schema_version": REQUEST_SCHEMA,
        "runtime": RUNTIME,
        "tool": tool_name,
        "action": action,
        "target": target,
        "content_hash": content_hash,
    }


def rule_key(request):
    return "aaf:" + request["tool"] + ":" + request["action"] + ":" + request["content_hash"]


def build_observation(tool_name, kind, expected_hash, observed_hash):
    tool = tool_name if isinstance(tool_name, str) else ""
    return {
        "schema_version": OBSERVATION_SCHEMA,
        "runtime": RUNTIME,
        "tool": tool,
        "kind": kind,
        "expected_hash": expected_hash,
        "observed_hash": observed_hash,
    }


def deny(reason):
    return {
        "decision": "deny",
        "reason": reason,
        "action_class": None,
        "effective_level": None,
        "request_hash": None,
        "policy_hash": None,
        "audit_sequence": None,
    }


def observe_fail(reason):
    return {"ok": False, "reason": reason, "audit_sequence": None}


def _read_line(sock, max_bytes):
    buf = bytearray()
    while True:
        if len(buf) > max_bytes:
            return None
        if len(buf) == max_bytes and b"\n" not in buf:
            return None
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, TimeoutError):
            raise
        except OSError:
            return None
        if not chunk:
            if b"\n" in buf:
                idx = buf.index(b"\n")
                if idx > max_bytes:
                    return None
                return bytes(buf[:idx])
            return None
        buf.extend(chunk)
        if b"\n" in buf:
            idx = buf.index(b"\n")
            if idx > max_bytes:
                return None
            return bytes(buf[:idx])


def socket_check(path, timeout=DEFAULT_TIMEOUT):
    def check(request):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                return deny("core_unreachable")
            frame = json.dumps({"request": request}, ensure_ascii=False, sort_keys=True) + "\n"
            sock.sendall(frame.encode("utf-8"))
            try:
                line = _read_line(sock, MAX_FRAME)
            except (socket.timeout, TimeoutError):
                return deny("core_timeout")
            if line is None:
                return deny("invalid_response")
            try:
                result = json.loads(line.decode("utf-8"))
            except Exception:
                return deny("invalid_response")
            if not isinstance(result, dict):
                return deny("invalid_response")
            decision = result.get("decision")
            if decision not in VALID_DECISIONS:
                return deny("invalid_response")
            return result
        except (socket.timeout, TimeoutError):
            return deny("core_timeout")
        except Exception:
            return deny("core_unreachable")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return check


def socket_observe(path, timeout=DEFAULT_TIMEOUT):
    def report(observation):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                return observe_fail("core_unreachable")
            frame = json.dumps({"observation": observation}, ensure_ascii=False, sort_keys=True) + "\n"
            sock.sendall(frame.encode("utf-8"))
            try:
                line = _read_line(sock, MAX_FRAME)
            except (socket.timeout, TimeoutError):
                return observe_fail("core_timeout")
            if line is None:
                return observe_fail("invalid_response")
            try:
                result = json.loads(line.decode("utf-8"))
            except Exception:
                return observe_fail("invalid_response")
            if not isinstance(result, dict):
                return observe_fail("invalid_response")
            if not isinstance(result.get("ok"), bool):
                return observe_fail("invalid_response")
            return result
        except (socket.timeout, TimeoutError):
            return observe_fail("core_timeout")
        except Exception:
            return observe_fail("core_unreachable")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return report


def make_pre_tool_call(check, target_keys=None, ledger=None, label_ids=None):
    def callback(tool_name, args, task_id="", **kwargs):
        try:
            try:
                request = build_request(tool_name, args, target_keys, label_ids)
            except Exception:
                return {"action": "block", "message": "Kern: verweigert (invalid_args)"}
            response = None
            try:
                result = check(request)
            except Exception:
                response = {"action": "block", "message": "Kern: verweigert (adapter_error)"}
            if response is None:
                try:
                    if not isinstance(result, dict):
                        response = {"action": "block", "message": "Kern: verweigert (adapter_error)"}
                    else:
                        decision = result.get("decision")
                        if decision == "allow":
                            response = None
                        elif decision == "needs_approval":
                            action_class = result.get("action_class")
                            label = action_class if isinstance(action_class, str) else "unbekannt"
                            response = {
                                "action": "approve",
                                "message": f"Kern: Freigabe nötig ({label})",
                                "rule_key": rule_key(request),
                            }
                        else:
                            reason = result.get("reason")
                            if not isinstance(reason, str) or not reason:
                                reason = "unknown"
                            response = {
                                "action": "block",
                                "message": f"Kern: verweigert ({reason})",
                            }
                except Exception:
                    response = {"action": "block", "message": "Kern: verweigert (adapter_error)"}
            try:
                if ledger is not None:
                    if isinstance(response, dict) and response.get("action") == "block":
                        ledger_decision = "block"
                    elif isinstance(response, dict) and response.get("action") == "approve":
                        ledger_decision = "approve"
                    else:
                        ledger_decision = "allow"
                    ledger.record(
                        kwargs.get("tool_call_id"),
                        request["content_hash"],
                        ledger_decision,
                    )
            except Exception:
                pass
            return response
        except Exception:
            return {"action": "block", "message": "Kern: verweigert (adapter_error)"}

    return callback


def make_post_tool_call(report, ledger):
    def callback(tool_name, args, result=None, task_id="", **kwargs):
        try:
            observed = None
            if isinstance(args, dict):
                try:
                    observed = hash_value(args)
                except (TypeError, ValueError):
                    observed = None
            tool_call_id = kwargs.get("tool_call_id")
            entry = (
                ledger.pop_entry(tool_call_id)
                if isinstance(tool_call_id, str) and tool_call_id
                else None
            )
            expected = None
            decision = None
            if entry is not None:
                expected, decision = entry
            status = kwargs.get("status")
            kind = None
            if entry is None:
                kind = "gate_unseen"
            elif decision == "block" and status != "blocked":
                kind = "gate_ignored"
            elif observed is None or observed != expected:
                kind = "gate_mismatch"
            elif decision == "approve" and status == "blocked":
                kind = "approval_denied"
            elif decision == "approve" and status != "blocked":
                kind = "approval_granted"
            if kind is not None:
                report(build_observation(tool_name, kind, expected, observed))
        except Exception:
            pass
        return None

    return callback


def _block_unconfigured(tool_name, args, task_id="", **kwargs):
    return {"action": "block", "message": "Kern: verweigert (gate_socket_unconfigured)"}


def register(ctx):
    try:
        getter = getattr(ctx, "get_config", None)
        path = None
        if callable(getter):
            configured = getter("socket_path")
            if isinstance(configured, str):
                path = configured
        if not path or not os.path.isabs(path):
            ctx.register_hook("pre_tool_call", _block_unconfigured)
            return
        label_ids = None
        if callable(getter):
            label_ids = label_allowlist(getter("label_ids"))
        ledger = DecisionLedger()
        ctx.register_hook(
            "pre_tool_call",
            make_pre_tool_call(socket_check(path), None, ledger, label_ids),
        )
        ctx.register_hook("post_tool_call", make_post_tool_call(socket_observe(path), ledger))
    except Exception:
        ctx.register_hook("pre_tool_call", _block_unconfigured)