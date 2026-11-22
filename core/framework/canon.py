import hashlib
import json
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

TEMPLATE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
PRINCIPAL_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")

CANON_VERSION = "canon-1"
SCHEMA_VERSION = "0.2-draft"
BERLIN = ZoneInfo("Europe/Berlin")


def reject_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate_key")
        obj[key] = value
    return obj


def parse_json(raw: str):
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non_finite")))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid_json") from exc


def canonical_bytes(value) -> bytes:
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
    return encoded.encode("utf-8")


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_value(value) -> str:
    return sha256_prefixed(canonical_bytes(value))


def hash_text(text: str) -> str:
    return sha256_prefixed(text.encode("utf-8"))


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def render_value(value) -> str:
    normalized = _normalize_for_render(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(", ", ": "),
        allow_nan=False,
    )


def _normalize_for_render(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return nfc(value)
    if isinstance(value, list):
        collected = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("not_renderable")
            collected.append(nfc(item))
        return collected
    raise ValueError("not_renderable")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("time")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("time") from exc
    return parsed.replace(tzinfo=ZoneInfo("UTC"))


def format_berlin(value: str) -> str:
    moment = parse_utc(value).astimezone(BERLIN)
    return moment.strftime("%Y-%m-%dT%H:%M:%S%z")
