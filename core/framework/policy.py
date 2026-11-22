import json
from pathlib import Path

from framework.canon import hash_value, parse_json

FLOOR_PATH = Path(__file__).resolve().parents[1] / "policy" / "floor.json"
LAYER_KEYS = frozenset({"forbidden_operations", "allow_operations"})


def load_floor() -> dict:
    body = parse_json(FLOOR_PATH.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or set(body) != {"schema_version", "forbidden_operations"}:
        raise ValueError("policy_floor")
    if body["schema_version"] != "policy-0.1-draft":
        raise ValueError("policy_floor")
    operations = body["forbidden_operations"]
    if not isinstance(operations, list) or not operations or not all(isinstance(item, str) for item in operations):
        raise ValueError("policy_floor")
    return body


FLOOR_FORBIDDEN = frozenset(load_floor()["forbidden_operations"])


def resolve(layers: list) -> dict:
    if not isinstance(layers, list):
        raise ValueError("policy_keys")
    combined = set(FLOOR_FORBIDDEN)
    seen = set()
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("policy_keys")
        if set(layer) - LAYER_KEYS:
            raise ValueError("policy_keys")
        for key, value in layer.items():
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("policy_keys")
        forbidden = set(layer.get("forbidden_operations", []))
        allowed = set(layer.get("allow_operations", []))
        if allowed & FLOOR_FORBIDDEN:
            raise ValueError("policy_loosen")
        if allowed & seen:
            raise ValueError("policy_loosen")
        seen |= forbidden
        combined |= forbidden
    return {
        "schema_version": "policy-0.1-draft",
        "forbidden_operations": sorted(combined),
    }


def evaluate(resolved: dict, operation: str) -> dict:
    if operation in resolved["forbidden_operations"]:
        return {"decision": "deny", "reason": "policy_forbidden"}
    return {"decision": "allow", "reason": "ok"}


def policy_hash(resolved: dict) -> str:
    return hash_value(resolved)