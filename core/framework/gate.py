import re

from framework.canon import hash_value

REQUEST_SCHEMA = "gate-request-0.1-draft"
POLICY_SCHEMA = "gate-policy-0.1-draft"
LEVELS = ("L0", "L1", "L2", "L3", "L4")
CORE_MAX = {
    "read": "L4",
    "draft": "L4",
    "internal_write": "L4",
    "status_transition": "L3",
    "send": "L3",
    "publish": "L3",
    "review_solicitation": "L3",
    "listing_write": "L3",
    "price_change": "L3",
    "ad_spend": "L3",
    "purchase": "L1",
    "delete_internal": "L3",
    "delete_external": "L1",
}
MAIN_CLASS = {
    "read": "read",
    "draft": "draft",
    "internal_write": "internal_write",
    "status_transition": "internal_write",
    "send": "external_send",
    "publish": "external_send",
    "review_solicitation": "external_send",
    "listing_write": "external_send",
    "price_change": "money",
    "ad_spend": "money",
    "purchase": "money",
    "delete_internal": "delete",
    "delete_external": "delete",
}
UNATTENDED_CAPPED = frozenset({"internal_write", "external_send", "money", "delete"})
REQUEST_KEYS = frozenset(
    {"schema_version", "runtime", "tool", "action", "target", "content_hash"}
)
POLICY_KEYS = frozenset({"schema_version", "tool_classes", "levels"})
LIMIT_KEYS = frozenset({"window_seconds", "max_calls"})
WINDOW_MIN = 60
WINDOW_MAX = 86400
CALLS_MAX = 100000
CONTENT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _controls_ok(text):
    for ch in text:
        o = ord(ch)
        if o < 32 or o == 127:
            return False
    return True


def _field_ok(value, minimum, maximum):
    if not isinstance(value, str):
        return False
    if len(value) < minimum or len(value) > maximum:
        return False
    return _controls_ok(value)


def _tool_class_key_ok(key):
    if not isinstance(key, str) or ":" not in key:
        return False
    tool, action = key.split(":", 1)
    if not _field_ok(tool, 1, 128):
        return False
    if action == "*":
        return True
    return _field_ok(action, 1, 128)


def _level_index(level):
    return LEVELS.index(level)


def _level_min(*items):
    chosen = items[0]
    for item in items[1:]:
        if _level_index(item) < _level_index(chosen):
            chosen = item
    return chosen


def _valid_request(request):
    if not isinstance(request, dict):
        return False
    if set(request.keys()) != REQUEST_KEYS:
        return False
    if request["schema_version"] != REQUEST_SCHEMA:
        return False
    for name in ("runtime", "tool", "action"):
        if not _field_ok(request[name], 1, 128):
            return False
    if not isinstance(request["target"], str) or len(request["target"]) > 2048:
        return False
    if not _controls_ok(request["target"]):
        return False
    if not isinstance(request["content_hash"], str):
        return False
    if CONTENT_HASH.fullmatch(request["content_hash"]) is None:
        return False
    return True


def _limit_int_ok(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return minimum <= value <= maximum


def _valid_limits(limits):
    if not isinstance(limits, dict):
        return False
    for key, value in limits.items():
        if not isinstance(key, str) or key not in CORE_MAX:
            return False
        if not isinstance(value, dict):
            return False
        if set(value.keys()) != LIMIT_KEYS:
            return False
        if not _limit_int_ok(value["window_seconds"], WINDOW_MIN, WINDOW_MAX):
            return False
        if not _limit_int_ok(value["max_calls"], 0, CALLS_MAX):
            return False
    return True


def _valid_policy(policy):
    if not isinstance(policy, dict):
        return False
    allowed = POLICY_KEYS
    if "limits" in policy:
        allowed = POLICY_KEYS | {"limits"}
    if set(policy.keys()) != allowed:
        return False
    if policy["schema_version"] != POLICY_SCHEMA:
        return False
    tool_classes = policy["tool_classes"]
    levels = policy["levels"]
    if not isinstance(tool_classes, dict) or not isinstance(levels, dict):
        return False
    for key, value in tool_classes.items():
        if not _tool_class_key_ok(key):
            return False
        if not isinstance(value, str) or value not in CORE_MAX:
            return False
    for subclass, level in levels.items():
        if not isinstance(subclass, str) or subclass not in CORE_MAX:
            return False
        if not isinstance(level, str) or level not in LEVELS:
            return False
        if _level_index(level) > _level_index(CORE_MAX[subclass]):
            return False
    if "limits" in policy and not _valid_limits(policy["limits"]):
        return False
    return True


def _result(decision, reason, action_class, effective_level, request_hash, policy_hash):
    return {
        "decision": decision,
        "reason": reason,
        "action_class": action_class,
        "effective_level": effective_level,
        "request_hash": request_hash,
        "policy_hash": policy_hash,
    }


def _invalid_request():
    return _result("deny", "invalid_request", None, None, None, None)


def _lookup_class(tool_classes, tool, action):
    exact = f"{tool}:{action}"
    if exact in tool_classes:
        return tool_classes[exact]
    wildcard = f"{tool}:*"
    if wildcard in tool_classes:
        return tool_classes[wildcard]
    return None


def _decision_for_level(level):
    if level == "L4":
        return "allow", "ok"
    if level == "L3":
        return "needs_approval", "approval_required"
    return "deny", f"level_{level}"


def decide(request, policy, *, unattended=False):
    try:
        if not isinstance(unattended, bool):
            return _invalid_request()
        if not _valid_request(request):
            return _invalid_request()
        request_hash = hash_value(request)
        if not _valid_policy(policy):
            return _result("deny", "invalid_policy", None, None, request_hash, None)
        policy_hash = hash_value(policy)
        tool = request["tool"]
        action = request["action"]
        action_class = _lookup_class(policy["tool_classes"], tool, action)
        if action_class is None:
            return _result(
                "deny",
                "action_class_unknown",
                None,
                None,
                request_hash,
                policy_hash,
            )
        levels = policy["levels"]
        effective = _level_min(CORE_MAX[action_class], levels.get(action_class, "L0"))
        if unattended and MAIN_CLASS[action_class] in UNATTENDED_CAPPED:
            effective = _level_min(effective, "L2")
        decision, reason = _decision_for_level(effective)
        return _result(
            decision,
            reason,
            action_class,
            effective,
            request_hash,
            policy_hash,
        )
    except Exception:
        return _result("deny", "internal_error", None, None, None, None)