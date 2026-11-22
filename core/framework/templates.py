import json
from pathlib import Path

from framework.canon import hash_value
from framework.configplane import ConfigError, validate_template


def template_key(template_id, template_version):
    return f"{template_id}@{template_version}"


def expected_hashes(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("INVALID_CONFIG")
    for key, value in data.items():
        if not isinstance(key, str) or key.count("@") != 1:
            raise ConfigError("INVALID_CONFIG")
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise ConfigError("INVALID_CONFIG")
    return data


def check_template(body: dict, expected: dict) -> str:
    cleaned = validate_template(body)
    key = template_key(cleaned["template_id"], cleaned["template_version"])
    if key not in expected:
        raise ConfigError("TEMPLATE_UNLISTED")
    digest = hash_value(cleaned)
    if digest != expected[key]:
        raise ConfigError("TEMPLATE_HASH_MISMATCH")
    return digest