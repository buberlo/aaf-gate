import re
from pathlib import Path

from framework.canon import hash_text, hash_value, nfc, parse_json
from framework.policy import FLOOR_FORBIDDEN

FORBIDDEN_ACTION_PATTERN = re.compile(r"^[a-z_]+$")

ALLOWED_RULE_KEYS = {
    "schema_version",
    "module_id",
    "module_version",
    "rules_version",
    "max_documents",
    "max_bytes",
    "max_claims",
    "forbidden_actions",
    "quantity_pattern",
    "date_value_pattern",
    "date_open_markers",
    "instruction_markers",
    "output_template_version",
    "draft_prefix",
}


def load_rules(path: Path) -> dict:
    return validate_rules(parse_json(path.read_text(encoding="utf-8")))


def validate_rules(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) - ALLOWED_RULE_KEYS:
        raise ValueError("rules_keys")
    missing = ALLOWED_RULE_KEYS - {"draft_prefix"} - set(body)
    if missing:
        raise ValueError("rules_missing")
    if body["module_id"] != "material-prep-1":
        raise ValueError("module")
    forbidden = set(body["forbidden_actions"])
    if not FLOOR_FORBIDDEN <= forbidden:
        raise ValueError("forbidden")
    if not all(FORBIDDEN_ACTION_PATTERN.fullmatch(item) for item in forbidden):
        raise ValueError("forbidden")
    for key in ("max_documents", "max_bytes", "max_claims"):
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("limit")
    if not isinstance(body["date_open_markers"], list) or not isinstance(body["instruction_markers"], list):
        raise ValueError("lists")
    re.compile(body["quantity_pattern"])
    re.compile(body["date_value_pattern"])
    return body


def manifest(sources: list[dict]) -> list[dict]:
    rows = [
        {
            "content_hash": source["content_hash"],
            "source_id": source["source_id"],
            "source_revision": source["source_revision"],
        }
        for source in sources
    ]
    return sorted(rows, key=lambda item: (item["source_id"], item["source_revision"]))


def decide(sources: list[dict], rules: dict) -> dict:
    claims = []
    ignored = []
    date_values = []
    open_markers = []
    superseded = set()
    for source in sorted(sources, key=lambda item: item["source_id"]):
        for segment in source["segments"]:
            text = segment["text"]
            locator = segment["locator"]
            reference = {
                "content_hash": source["content_hash"],
                "locator": locator,
                "source_id": source["source_id"],
                "source_revision": source["source_revision"],
                "text_hash": hash_text(text),
            }
            lowered = text.casefold()
            for marker in rules["instruction_markers"]:
                if marker.casefold() in lowered:
                    ignored.append(
                        {
                            "locator": locator,
                            "reason": "source_text_not_instruction",
                            "source_id": source["source_id"],
                        }
                    )
                    break
            non_supersede = re.findall(
                r"does not supersede\s+([A-Za-z0-9_-]+)",
                text,
                flags=re.IGNORECASE,
            )
            for match in re.finditer(r"supersedes\s+([A-Za-z0-9_-]+)", text, flags=re.IGNORECASE):
                target = match.group(1)
                if target in non_supersede:
                    continue
                superseded.add(target)
            for match in re.finditer(rules["quantity_pattern"], text):
                claims.append(
                    {
                        "claim_type": "test_specimens",
                        "kind": "fact",
                        "references": [reference],
                        "reviewer_note": "",
                        "statement": nfc(f"Test specimens: {match.group(1)}"),
                        "support_status": "supported",
                        "value": match.group(1),
                    }
                )
            for match in re.finditer(rules["date_value_pattern"], text):
                date_values.append({"reference": reference, "value": match.group(1)})
            for marker in rules["date_open_markers"]:
                if marker in text:
                    open_markers.append(reference)
    active = [claim for claim in claims if claim["references"][0]["source_id"] not in superseded]
    values = {claim["value"] for claim in active}
    if len(values) > 1:
        for claim in active:
            claim["support_status"] = "conflict"
    conflicts = []
    if len(values) > 1:
        conflicts.append(
            {
                "references": [claim["references"][0] for claim in active],
                "type": "test_specimens",
                "values": sorted(values, key=int),
            }
        )
    open_questions = []
    if not date_values:
        open_questions.append(
            {
                "detail": "Delivery date unknown",
                "references": open_markers,
                "topic": "lieferdatum",
            }
        )
    facts = []
    for claim in active:
        if claim["support_status"] == "supported":
            facts.append(_public_claim(claim))
    prefix = rules.get("draft_prefix") or ""
    draft_parts = []
    if prefix:
        draft_parts.append(prefix)
    draft_parts.append("Synthetic work preparation.")
    if conflicts:
        bits = []
        for claim in active:
            ref = claim["references"][0]
            bits.append(f"{claim['value']} (Source {ref['source_id']}, Locator {ref['locator']})")
        draft_parts.append("Test specimens: conflict between " + " and ".join(bits) + ".")
        draft_parts.append("No quantity adopted.")
    elif facts:
        draft_parts.append(f"Test specimens documented: {facts[0]['statement']}.")
    else:
        draft_parts.append("No documented quantity.")
    if open_questions:
        draft_parts.append("Delivery date: open.")
    if ignored:
        draft_parts.append("Sources with control instructions were not executed.")
    draft_parts.append("No silent resolution.")
    stored_claims = [_public_claim(claim) for claim in active]
    return {
        "claims": stored_claims,
        "conflicts": conflicts,
        "facts": facts,
        "ignored_instructions": ignored,
        "open_questions": open_questions,
        "work_draft": " ".join(draft_parts),
    }


def decision_view(artifact: dict) -> dict:
    claims = []
    for claim in artifact["claims"]:
        claims.append(
            {
                "claim_type": claim["claim_type"],
                "kind": claim["kind"],
                "references": claim["references"],
                "statement": claim["statement"],
                "support_status": claim["support_status"],
            }
        )
    claims.sort(key=lambda item: (item["statement"], item["references"][0]["source_id"], item["references"][0]["locator"]))
    return {
        "claims": claims,
        "conflicts": artifact["conflicts"],
        "facts": artifact["facts"],
        "ignored_instructions": artifact["ignored_instructions"],
        "open_questions": artifact["open_questions"],
        "work_draft": artifact["work_draft"],
    }


def _public_claim(claim: dict) -> dict:
    return {
        "claim_type": claim["claim_type"],
        "kind": claim["kind"],
        "references": claim["references"],
        "reviewer_note": claim["reviewer_note"],
        "statement": claim["statement"],
        "support_status": claim["support_status"],
    }
