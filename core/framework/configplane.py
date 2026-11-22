import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from framework.audit import append as audit_append
from framework.canon import FIELD_NAME, PRINCIPAL_ID, SCHEMA_VERSION, TEMPLATE_ID, hash_value
from framework.clock import Clock
from framework.idempotency import find, replay_result
from framework.policy import policy_hash, resolve as resolve_policy
from framework.principals import Principal, resolve

CONFIG_OPERATION_ROLES = {
    "config.register_template": frozenset({"author"}),
    "config.release": frozenset({"author"}),
    "config.rollback": frozenset({"author"}),
    "config.apply": frozenset({"author", "customer"}),
    "config.active": frozenset({"author", "customer"}),
    "config.release_record": frozenset({"author", "customer"}),
}
APPLY_ROLE_ORDER = ("author", "customer")
CONFIG_AGENT_FORBIDDEN = frozenset(
    {"config.register_template", "config.release", "config.apply", "config.rollback"}
)

STRING_FLOOR_MAX = 4000
ARRAY_FLOOR_MAX = 200
ITEM_FLOOR_MAX = 500

ACCESS = {"locked", "customer_editable", "fixed"}
TYPES = {"string", "integer", "boolean", "array"}
SPEC_KEYS = {"access", "type", "required", "min", "max", "enum", "item_type", "value", "contains", "unique"}
DOMAIN_PATTERN = re.compile(
    r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)


class ConfigError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_template(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ConfigError("INVALID_CONFIG")
    required = {"template_id", "template_version", "synthetic", "title", "fields"}
    if set(body) - required or required - set(body):
        raise ConfigError("INVALID_CONFIG")
    if body["synthetic"] is not True:
        raise ConfigError("INVALID_CONFIG")
    if not isinstance(body["template_id"], str) or not body["template_id"]:
        raise ConfigError("INVALID_CONFIG")
    if TEMPLATE_ID.fullmatch(body["template_id"]) is None:
        raise ConfigError("INVALID_CONFIG")
    if not isinstance(body["template_version"], str) or not body["template_version"]:
        raise ConfigError("INVALID_CONFIG")
    if not isinstance(body["title"], str) or not body["title"]:
        raise ConfigError("INVALID_CONFIG")
    fields = body["fields"]
    if not isinstance(fields, dict) or not fields:
        raise ConfigError("INVALID_CONFIG")
    clean = {}
    for name, spec in fields.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise ConfigError("INVALID_CONFIG")
        if FIELD_NAME.fullmatch(name) is None:
            raise ConfigError("INVALID_CONFIG")
        if not isinstance(spec, dict) or set(spec) - SPEC_KEYS:
            raise ConfigError("INVALID_CONFIG")
        if spec.get("access") not in ACCESS or spec.get("type") not in TYPES:
            raise ConfigError("INVALID_CONFIG")
        if spec.get("required") is not True:
            raise ConfigError("INVALID_CONFIG")
        cleaned = {"access": spec["access"], "required": True, "type": spec["type"]}
        if spec["type"] == "array":
            item_type = spec.get("item_type")
            if item_type not in ("string", "domain"):
                raise ConfigError("INVALID_CONFIG")
            cleaned["item_type"] = item_type
            if "unique" in spec:
                if spec["unique"] is not True:
                    raise ConfigError("INVALID_CONFIG")
                cleaned["unique"] = True
            if "contains" in spec:
                items = spec["contains"]
                if not _valid_contains(item_type, items):
                    raise ConfigError("INVALID_CONFIG")
                if "max" in spec and spec["max"] < len(items):
                    raise ConfigError("INVALID_CONFIG")
                cleaned["contains"] = list(items)
                cleaned["unique"] = True
        elif "item_type" in spec or "contains" in spec or "unique" in spec:
            raise ConfigError("INVALID_CONFIG")
        if "enum" in spec:
            if spec["type"] == "boolean" or not _valid_enum(spec["type"], spec["enum"]):
                raise ConfigError("INVALID_CONFIG")
            cleaned["enum"] = list(spec["enum"])
        for bound in ("min", "max"):
            if bound in spec:
                if spec["type"] == "boolean" or isinstance(spec[bound], bool) or not isinstance(spec[bound], int):
                    raise ConfigError("INVALID_CONFIG")
                cleaned[bound] = spec[bound]
        if "min" in cleaned and "max" in cleaned and cleaned["min"] > cleaned["max"]:
            raise ConfigError("INVALID_CONFIG")
        if cleaned["type"] == "string" and "max" in cleaned and cleaned["max"] > STRING_FLOOR_MAX:
            raise ConfigError("INVALID_CONFIG")
        if cleaned["type"] == "array" and "max" in cleaned and cleaned["max"] > ARRAY_FLOOR_MAX:
            raise ConfigError("INVALID_CONFIG")
        if cleaned["access"] == "fixed":
            if "value" not in spec:
                raise ConfigError("INVALID_CONFIG")
            value = spec["value"]
            if not _check_type(cleaned, value) or not _within_bounds(cleaned, value) or not _array_constraints(cleaned, value):
                raise ConfigError("INVALID_CONFIG")
            if "enum" in cleaned:
                if cleaned["type"] == "array":
                    if any(item not in cleaned["enum"] for item in value):
                        raise ConfigError("INVALID_CONFIG")
                elif value not in cleaned["enum"]:
                    raise ConfigError("INVALID_CONFIG")
            cleaned["value"] = list(value) if cleaned["type"] == "array" else value
        elif "value" in spec:
            raise ConfigError("INVALID_CONFIG")
        clean[name] = cleaned
    return {
        "fields": clean,
        "synthetic": True,
        "template_id": body["template_id"],
        "template_version": body["template_version"],
        "title": body["title"],
    }


def _valid_enum(kind: str, choices) -> bool:
    if not isinstance(choices, list) or not choices or len(choices) != len(set(map(str, choices))):
        return False
    if kind == "integer":
        return all(isinstance(item, int) and not isinstance(item, bool) for item in choices)
    return all(isinstance(item, str) and item for item in choices)


def _valid_domain(item: str) -> bool:
    return bool(re.fullmatch(DOMAIN_PATTERN, item))


def _valid_array_item(item_type: str, item: str) -> bool:
    if not isinstance(item, str) or not item:
        return False
    if item_type == "domain":
        return _valid_domain(item)
    return True


def _valid_contains(item_type: str, items) -> bool:
    if not isinstance(items, list) or not items:
        return False
    if len(items) != len(set(items)):
        return False
    return all(_valid_array_item(item_type, item) and _text_ok(item, ITEM_FLOOR_MAX) for item in items)


def _array_constraints(spec: dict, value) -> bool:
    if spec["type"] != "array":
        return True
    if "contains" in spec:
        if not all(item in value for item in spec["contains"]):
            return False
    if spec.get("unique") or "contains" in spec:
        if len(value) != len(set(value)):
            return False
    return True


def _text_ok(text: str, limit: int) -> bool:
    if not isinstance(text, str):
        return False
    if not text.strip():
        return False
    if len(text) > limit:
        return False
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cf":
            return False
        if category == "Cc" and ch not in "\n\t":
            return False
    return True


def _check_type(spec: dict, value) -> bool:
    kind = spec["type"]
    if kind == "string":
        if not isinstance(value, str):
            return False
        cap = min(spec.get("max", STRING_FLOOR_MAX), STRING_FLOOR_MAX)
        return _text_ok(value, cap)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if not isinstance(value, list):
        return False
    item_type = spec.get("item_type", "string")
    return all(_valid_array_item(item_type, item) and _text_ok(item, ITEM_FLOOR_MAX) for item in value)


def _within_bounds(spec: dict, value) -> bool:
    kind = spec["type"]
    if kind == "integer":
        size = value
    elif kind == "string":
        if "min" in spec and len(value) < spec["min"]:
            return False
        return True
    elif kind == "array":
        size = len(value)
        cap = min(spec.get("max", ARRAY_FLOOR_MAX), ARRAY_FLOOR_MAX)
        if size > cap:
            return False
    else:
        return "min" not in spec and "max" not in spec
    if "min" in spec and size < spec["min"]:
        return False
    if "max" in spec and size > spec["max"]:
        return False
    return True


def validate_values(template: dict, values: dict) -> dict:
    if not isinstance(values, dict):
        raise ConfigError("INVALID_CONFIG")
    values = _profile_values(template, values)
    clean = {}
    for name, spec in template["fields"].items():
        if spec["access"] == "fixed":
            value = spec["value"]
            clean[name] = list(value) if spec["type"] == "array" else value
            continue
        value = values[name]
        if not _check_type(spec, value) or not _within_bounds(spec, value) or not _array_constraints(spec, value):
            raise ConfigError("INVALID_CONFIG")
        if "enum" in spec:
            if spec["type"] == "array":
                if any(item not in spec["enum"] for item in value):
                    raise ConfigError("INVALID_CONFIG")
            elif value not in spec["enum"]:
                raise ConfigError("INVALID_CONFIG")
        if spec["type"] == "array":
            clean[name] = list(value)
        else:
            clean[name] = value
    _enforce_allow_floor(clean)
    return clean


def _enforce_allow_floor(values: dict) -> None:
    for name, value in values.items():
        if name.startswith("allow_") and value is not False:
            raise ConfigError("INVALID_CONFIG")


def _profile_values(template: dict, values: dict) -> dict:
    fixed = {name for name, spec in template["fields"].items() if spec["access"] == "fixed"}
    if fixed & set(values):
        raise ConfigError("INVALID_CONFIG")
    configurable = set(template["fields"]) - fixed
    if set(values) != configurable:
        raise ConfigError("INVALID_CONFIG")
    return values


class ConfigPlane:
    def __init__(self, store, clock=None, expected=None):
        self.store = store
        self.clock = clock or Clock()
        self.expected = expected
        self.mode = "unspecified"
        self._current_acting_role = None
        self._current_command_id = None

    @classmethod
    def for_device(cls, store, mode_path: Path, clock=None):
        from framework.devicemode import load_mode

        loaded = load_mode(mode_path)
        plane = cls(store, clock, expected=loaded["expected"])
        plane.mode = loaded["mode"]
        return plane

    def require(self, principal_id: str, role: str) -> Principal:
        principal = self._resolve_read(principal_id)
        if role not in principal.roles or principal.kind != "human":
            raise ConfigError("FORBIDDEN")
        return principal

    def register_template(self, principal_id: str, body: dict) -> dict:
        principal = self._authorize_write(principal_id, "config.register_template")
        try:
            template = validate_template(body)
        except ConfigError as exc:
            payload_hash = None
            try:
                payload_hash = hash_value(body)
            except (TypeError, ValueError):
                pass
            self._audit_config_reject(principal, "config.register_template", payload_hash, exc.code, None)
            raise
        digest = hash_value(template)
        if self.expected is not None:
            key = f"{template['template_id']}@{template['template_version']}"
            if key not in self.expected:
                self._audit_config_reject(principal, "config.register_template", digest, "TEMPLATE_UNLISTED", None)
                raise ConfigError("TEMPLATE_UNLISTED")
            if self.expected[key] != digest:
                self._audit_config_reject(principal, "config.register_template", digest, "TEMPLATE_HASH_MISMATCH", None)
                raise ConfigError("TEMPLATE_HASH_MISMATCH")
        def work(conn):
            existing = conn.execute(
                "SELECT body_hash FROM config_templates WHERE template_id=? AND template_version=?",
                (template["template_id"], template["template_version"]),
            ).fetchone()
            if existing is not None:
                if existing["body_hash"] != digest:
                    raise ConfigError("INVALID_CONFIG")
                return {"template_hash": digest, "template_id": template["template_id"], "template_version": template["template_version"]}
            conn.execute(
                "INSERT INTO config_templates (template_id, template_version, body_json, body_hash, synthetic) VALUES (?, ?, ?, ?, 1)",
                (
                    template["template_id"],
                    template["template_version"],
                    json.dumps(template, ensure_ascii=False, sort_keys=True),
                    digest,
                ),
            )
            self._audit(conn, principal, "config.register_template", None, "registered", None, digest, None, "committed", None)
            return {"template_hash": digest, "template_id": template["template_id"], "template_version": template["template_version"]}
        return self._mutate(work)

    def release(self, principal_id: str, config_id: str, template_id: str, template_version: str, values: dict, idempotency_key: str) -> dict:
        principal = self._authorize_write(principal_id, "config.release")
        return self._with_key(principal, "config.release", idempotency_key, {
            "config_id": config_id,
            "template_id": template_id,
            "template_version": template_version,
            "values": values,
        }, lambda conn: self._release_new(conn, principal, config_id, template_id, template_version, values, "author"))

    def apply_changes(self, principal_id: str, config_id: str, changes: dict, idempotency_key: str) -> dict:
        principal = self._authorize_write(principal_id, "config.apply")
        role = self._apply_role(principal)
        return self._with_key(principal, "config.apply", idempotency_key, {"changes": changes, "config_id": config_id}, lambda conn: self._apply(conn, principal, role, config_id, changes))

    def rollback(self, principal_id: str, config_id: str, release_no: int, idempotency_key: str) -> dict:
        principal = self._authorize_write(principal_id, "config.rollback")
        return self._with_key(principal, "config.rollback", idempotency_key, {"config_id": config_id, "release_no": release_no}, lambda conn: self._rollback(conn, principal, config_id, release_no))

    def active(self, principal_id: str, config_id: str) -> dict:
        principal = self._resolve_read(principal_id)
        self._ensure_read_role(principal)
        return self._read(lambda conn: self._read_active(conn, principal.tenant_id, config_id))

    def release_record(self, principal_id: str, config_id: str, release_no: int | None = None) -> dict:
        principal = self._resolve_read(principal_id)
        self._ensure_read_role(principal)

        def work(conn):
            target_no = release_no
            if target_no is None:
                target_no = self._read_active(conn, principal.tenant_id, config_id)["release_no"]
            row = conn.execute(
                """SELECT resolved_json, release_hash, release_no, template_id, template_version
                   FROM config_releases WHERE tenant_id=? AND config_id=? AND release_no=?""",
                (principal.tenant_id, config_id, target_no),
            ).fetchone()
            if row is None:
                raise ConfigError("NOT_FOUND")
            return {
                "document": json.loads(row["resolved_json"]),
                "release_hash": row["release_hash"],
                "release_no": row["release_no"],
                "template_id": row["template_id"],
                "template_version": row["template_version"],
            }

        return self._read(work)

    def _release_new(self, conn, principal, config_id, template_id, template_version, values, role, base=None):
        template = self._load_template(conn, template_id, template_version)
        clean = validate_values(template, values)
        current = conn.execute(
            "SELECT release_no FROM config_active WHERE tenant_id=? AND config_id=?",
            (principal.tenant_id, config_id),
        ).fetchone()
        if base == "new" and current is not None:
            raise ConfigError("INVALID_CONFIG")
        number = conn.execute(
            "SELECT COALESCE(MAX(release_no), 0) + 1 FROM config_releases WHERE tenant_id=? AND config_id=?",
            (principal.tenant_id, config_id),
        ).fetchone()[0]
        document = self._document(
            principal.tenant_id,
            config_id,
            template,
            number,
            clean,
            None if current is None else int(current["release_no"]),
        )
        digest = hash_value(document)
        conn.execute(
            """INSERT INTO config_releases (
                tenant_id, config_id, release_no, template_id, template_version, resolved_json,
                release_hash, actor_id, actor_role, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                principal.tenant_id,
                config_id,
                number,
                template["template_id"],
                template["template_version"],
                json.dumps(document, ensure_ascii=False, sort_keys=True),
                digest,
                principal.actor_id,
                role,
                self.clock.now(),
            ),
        )
        if current is None:
            conn.execute(
                "INSERT INTO config_active (tenant_id, config_id, release_no) VALUES (?, ?, ?)",
                (principal.tenant_id, config_id, number),
            )
        else:
            conn.execute(
                "UPDATE config_active SET release_no=? WHERE tenant_id=? AND config_id=?",
                (number, principal.tenant_id, config_id),
            )
        self._audit(conn, principal, "config.release", None, "active", number, template["body_hash"] if "body_hash" in template else hash_value(template), digest, "committed", None)
        return {"config_id": config_id, "release_hash": digest, "release_no": number, "values": clean}

    def _apply(self, conn, principal, role, config_id, changes):
        if not isinstance(changes, dict) or not changes:
            raise ConfigError("INVALID_CONFIG")
        current = self._read_active(conn, principal.tenant_id, config_id)
        template = self._load_template(conn, current["template_id"], current["template_version"])
        names = sorted(changes)
        for name in names:
            if name not in template["fields"]:
                raise ConfigError("INVALID_CONFIG")
        for name in names:
            if template["fields"][name]["access"] == "fixed":
                raise ConfigError("INVALID_CONFIG")
        if role == "customer":
            for name in names:
                if template["fields"][name]["access"] != "customer_editable":
                    raise ConfigError("LOCKED_FIELD")
        merged = dict(current["values"])
        merged.update(changes)
        configurable = {name for name, spec in template["fields"].items() if spec["access"] != "fixed"}
        values = {name: merged[name] for name in configurable}
        return self._release_new(
            conn,
            principal,
            config_id,
            template["template_id"],
            template["template_version"],
            values,
            role,
        )

    def _rollback(self, conn, principal, config_id, release_no):
        if isinstance(release_no, bool) or not isinstance(release_no, int) or release_no < 1:
            raise ConfigError("INVALID_CONFIG")
        row = conn.execute(
            "SELECT release_hash FROM config_releases WHERE tenant_id=? AND config_id=? AND release_no=?",
            (principal.tenant_id, config_id, release_no),
        ).fetchone()
        if row is None:
            raise ConfigError("NOT_FOUND")
        before = conn.execute(
            "SELECT release_no FROM config_active WHERE tenant_id=? AND config_id=?",
            (principal.tenant_id, config_id),
        ).fetchone()
        conn.execute(
            "UPDATE config_active SET release_no=? WHERE tenant_id=? AND config_id=?",
            (release_no, principal.tenant_id, config_id),
        )
        self._audit(
            conn,
            principal,
            "config.rollback",
            str(before["release_no"]) if before else None,
            str(release_no),
            release_no,
            None,
            row["release_hash"],
            "committed",
            None,
        )
        return {"config_id": config_id, "release_hash": row["release_hash"], "release_no": release_no}

    def _document(self, tenant_id, config_id, template, number, values, base_release_no):
        return {
            "base_release_no": base_release_no,
            "config_id": config_id,
            "core_floor_hash": policy_hash(resolve_policy([])),
            "core_schema_version": SCHEMA_VERSION,
            "field_access": {name: spec["access"] for name, spec in template["fields"].items()},
            "release_no": number,
            "synthetic": True,
            "template_body_hash": template["body_hash"],
            "template_id": template["template_id"],
            "template_version": template["template_version"],
            "tenant_id": tenant_id,
            "values": values,
        }

    def _load_template(self, conn, template_id, template_version):
        row = conn.execute(
            "SELECT body_json, body_hash FROM config_templates WHERE template_id=? AND template_version=?",
            (template_id, template_version),
        ).fetchone()
        if row is None:
            raise ConfigError("NOT_FOUND")
        body = json.loads(row["body_json"])
        body["body_hash"] = row["body_hash"]
        return body

    def _read_active(self, conn, tenant_id, config_id):
        row = conn.execute(
            """SELECT r.resolved_json, r.release_hash, r.release_no, r.template_id, r.template_version
               FROM config_active a
               JOIN config_releases r
                 ON r.tenant_id=a.tenant_id AND r.config_id=a.config_id AND r.release_no=a.release_no
               WHERE a.tenant_id=? AND a.config_id=?""",
            (tenant_id, config_id),
        ).fetchone()
        if row is None:
            raise ConfigError("NOT_FOUND")
        document = json.loads(row["resolved_json"])
        return {
            "config_id": config_id,
            "release_hash": row["release_hash"],
            "release_no": row["release_no"],
            "template_id": row["template_id"],
            "template_version": row["template_version"],
            "values": document["values"],
        }

    def _with_key(self, principal, operation, key, payload, work):
        if not isinstance(key, str) or not key:
            raise ConfigError("INVALID_CONFIG")
        request_hash = hash_value(payload)
        self._current_command_id = key
        def wrapped(conn):
            existing = find(conn, "config_idempotency", principal.tenant_id, principal.principal_id, operation, key)
            if existing is not None:
                result = replay_result(existing, request_hash, ConfigError("IDEMPOTENCY_CONFLICT"))
                self._audit(
                    conn,
                    principal,
                    operation,
                    None,
                    None,
                    None,
                    None,
                    request_hash,
                    "replayed",
                    None,
                )
                return result
            try:
                result = work(conn)
            except ConfigError as exc:
                if exc.code == "LOCKED_FIELD":
                    self._audit(conn, principal, operation, None, "unchanged", None, None, request_hash, "rejected", "LOCKED_FIELD")
                raise
            conn.execute(
                """INSERT INTO config_idempotency (
                    tenant_id, actor_id, operation, idempotency_key, request_hash, result_json
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    principal.tenant_id,
                    principal.actor_id,
                    operation,
                    key,
                    request_hash,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                ),
            )
            return result
        try:
            return self._mutate(wrapped)
        except ConfigError as exc:
            if exc.code not in {"LOCKED_FIELD", "STORAGE_UNAVAILABLE"}:
                self._audit_config_reject(principal, operation, request_hash, exc.code, key)
            raise
        finally:
            self._current_command_id = None

    def _rollback_quiet(self, conn):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _read(self, work):
        conn = None
        try:
            conn = self.store.connect()
            return work(conn)
        except ConfigError:
            raise
        except sqlite3.Error:
            raise ConfigError("STORAGE_UNAVAILABLE") from None
        finally:
            if conn is not None:
                conn.close()

    def _mutate(self, work):
        try:
            conn = self.store.connect()
        except Exception:
            raise ConfigError("STORAGE_UNAVAILABLE") from None
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error:
                raise ConfigError("STORAGE_UNAVAILABLE") from None
            try:
                result = work(conn)
            except ConfigError as exc:
                if exc.code == "LOCKED_FIELD":
                    conn.execute("COMMIT")
                else:
                    self._rollback_quiet(conn)
                raise
            except sqlite3.IntegrityError:
                self._rollback_quiet(conn)
                raise ConfigError("CONFLICT") from None
            except sqlite3.Error:
                self._rollback_quiet(conn)
                raise ConfigError("STORAGE_UNAVAILABLE") from None
            except Exception:
                self._rollback_quiet(conn)
                raise
            try:
                conn.execute("COMMIT")
            except sqlite3.Error:
                raise ConfigError("STORAGE_UNAVAILABLE") from None
            return result
        finally:
            conn.close()

    def _audit(self, conn, principal, operation, before, after, revision, rule_hash, payload_hash, outcome, error_code, command_id=None):
        audit_append(
            conn,
            self.clock.now(),
            schema_version=SCHEMA_VERSION,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            operation=operation,
            outcome=outcome,
            principal_kind=principal.kind,
            acting_role=self._current_acting_role,
            run_id=None,
            command_id=command_id if command_id is not None else self._current_command_id,
            before=before,
            after=after,
            revision=revision,
            rule_hash=rule_hash,
            payload_hash=payload_hash,
            approval_id=None,
            error_code=error_code,
        )

    def _audit_config_reject(self, principal, operation, request_hash, error_code, command_id=None):
        def work(conn):
            self._audit(conn, principal, operation, None, None, None, None, request_hash, "rejected", error_code, command_id)
        self._mutate(work)

    def _audit_reject(self, tenant_id, actor_id, operation, principal_kind, acting_role):
        def work(conn):
            principal = Principal(actor_id, tenant_id, principal_kind, frozenset())
            self._current_acting_role = acting_role
            self._audit(conn, principal, operation, None, None, None, None, None, "rejected", "FORBIDDEN")
        self._mutate(work)

    def _resolve_read(self, principal_id: str) -> Principal:
        if not isinstance(principal_id, str) or PRINCIPAL_ID.fullmatch(principal_id) is None:
            raise ConfigError("FORBIDDEN")
        principal = self._read(lambda conn: resolve(conn, principal_id))
        if principal is None:
            raise ConfigError("FORBIDDEN")
        return principal

    def _ensure_read_role(self, principal: Principal) -> None:
        needed = CONFIG_OPERATION_ROLES["config.active"]
        if not principal.roles & needed:
            raise ConfigError("FORBIDDEN")

    def _apply_role(self, principal: Principal) -> str:
        for role in APPLY_ROLE_ORDER:
            if role in principal.roles:
                return role
        raise ConfigError("FORBIDDEN")

    def _authorize_write(self, principal_id: str, operation: str) -> Principal:
        if not isinstance(principal_id, str):
            self._audit_reject("unresolved", "invalid", operation, None, None)
            raise ConfigError("FORBIDDEN")
        audit_id = principal_id if PRINCIPAL_ID.fullmatch(principal_id) else "invalid"
        if audit_id == "invalid":
            self._audit_reject("unresolved", "invalid", operation, None, None)
            raise ConfigError("FORBIDDEN")
        principal = self._read(lambda conn: resolve(conn, principal_id))
        if principal is None:
            self._audit_reject("unresolved", audit_id, operation, None, None)
            raise ConfigError("FORBIDDEN")
        self._current_acting_role = None
        if principal.kind == "agent" and operation in CONFIG_AGENT_FORBIDDEN:
            self._audit_reject(principal.tenant_id, principal.actor_id, operation, principal.kind, None)
            raise ConfigError("FORBIDDEN")
        needed = CONFIG_OPERATION_ROLES[operation]
        if not principal.roles & needed:
            self._audit_reject(principal.tenant_id, principal.actor_id, operation, principal.kind, None)
            raise ConfigError("FORBIDDEN")
        if operation == "config.apply":
            self._current_acting_role = self._apply_role(principal)
        else:
            self._current_acting_role = "author"
        return principal
