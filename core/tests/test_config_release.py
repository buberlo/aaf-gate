import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.canon import hash_value
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane, validate_template, validate_values
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
CANARY = "GESPERRT-WERT-9f3c"


def template_body():
    return json.loads((ROOT / "templates" / "kfz-terminannahme" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


class ConfigReleaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T15:00:00Z"))
        seed_config(
            self.store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("cust", "synth-tenant", "human", ("customer",)),
                ("other", "other-tenant", "human", ("customer",)),
            ),
        )
        self.plane.register_template("konrad", template_body())

    def test_schema_accepts_array_enum_and_bounds(self):
        body = {
            "fields": {
                "tags": {"access": "customer_editable", "enum": ["a", "b"], "item_type": "string", "required": True, "type": "array", "min": 0, "max": 2},
                "level": {"access": "locked", "enum": [1, 2], "max": 2, "min": 1, "required": True, "type": "integer"},
            },
            "synthetic": True,
            "template_id": "schema-probe",
            "template_version": "0.1.0",
            "title": "Schema",
        }
        parsed = validate_template(body)
        self.assertEqual(validate_values(parsed, {"level": 1, "tags": []})["tags"], [])
        with self.assertRaises(ConfigError):
            validate_values(parsed, {"level": 3, "tags": ["a"]})
        with self.assertRaises(ConfigError):
            validate_values(parsed, {"level": 1, "tags": ["c"]})
        with self.assertRaises(ConfigError):
            validate_values(parsed, {"level": 1, "tags": ["a", "b", "a"]})

    def test_template_rejects_unknown_and_unlocked_shape(self):
        broken = template_body()
        broken["fields"]["shop_name"]["access"] = "anyone"
        with self.assertRaises(ConfigError) as caught:
            validate_template(broken)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_release_is_immutable_and_deterministic(self):
        first = self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        again = self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.assertEqual(first, again)
        self.assertEqual(self.plane.active("konrad", "cfg-1")["release_no"], 1)
        twin_store = Store(str(Path(self.folder) / "other.sqlite"))
        seed_config(twin_store, (("konrad", "synth-tenant", "human", ("author",)),))
        twin = ConfigPlane(twin_store, FixedClock("2026-11-12T15:00:00Z"))
        twin.register_template("konrad", template_body())
        second = twin.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.assertEqual(first["release_hash"], second["release_hash"])
        self.assertTrue(first["release_hash"].startswith("sha256:"))

    def test_customer_cannot_change_locked_field_and_can_change_released_field(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        before = self.plane.active("cust", "cfg-1")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes(
                "cust",
                "cfg-1",
                {"followup_questions": CANARY},
                "bad-1",
            )
        self.assertEqual(caught.exception.code, "LOCKED_FIELD")
        self.assertNotIn(CANARY, str(caught.exception))
        after_reject = self.plane.active("cust", "cfg-1")
        self.assertEqual(after_reject["release_hash"], before["release_hash"])
        self.assertEqual(after_reject["values"]["followup_questions"], before["values"]["followup_questions"])
        changed = self.plane.apply_changes(
            "cust",
            "cfg-1",
            {"opening_hours": "Mo-Fr 9-18"},
            "ok-1",
        )
        self.assertEqual(changed["release_no"], 2)
        self.assertNotEqual(changed["release_hash"], before["release_hash"])
        self.assertEqual(self.plane.active("cust", "cfg-1")["values"]["opening_hours"], "Mo-Fr 9-18")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        stored = conn.execute(
            "SELECT resolved_json FROM config_releases WHERE config_id=? AND release_no=1",
            ("cfg-1",),
        ).fetchone()[0]
        self.assertIn("Mo-Fr 8-17", stored)
        self.assertNotIn("Mo-Fr 9-18", stored)
        self.assertNotIn(CANARY, stored)
        audits = conn.execute(
            "SELECT operation, outcome, error_code, payload_hash FROM audit_events WHERE operation='config.apply'"
        ).fetchall()
        conn.close()
        self.assertTrue(any(row[1] == "rejected" and row[2] == "LOCKED_FIELD" for row in audits))
        self.assertTrue(all(CANARY not in "".join(str(part) for part in row) for row in audits))

    def test_author_can_change_locked_field_and_rollback_restores_prior_release(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        original = self.plane.active("konrad", "cfg-1")["release_hash"]
        self.plane.apply_changes("cust", "cfg-1", {"shop_name": "Synth-Werkstatt Nord"}, "ok-name")
        updated = self.plane.apply_changes(
            "konrad",
            "cfg-1",
            {"followup_questions": "Nur: Welche Arbeit?"},
            "author-lock",
        )
        self.assertEqual(updated["values"]["followup_questions"], "Nur: Welche Arbeit?")
        with self.assertRaises(ConfigError) as caught:
            self.plane.rollback("cust", "cfg-1", 1, "nope")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        rolled = self.plane.rollback("konrad", "cfg-1", 1, "back-1")
        self.assertEqual(rolled["release_no"], 1)
        self.assertEqual(rolled["release_hash"], original)
        self.assertEqual(self.plane.active("konrad", "cfg-1")["release_hash"], original)
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        count = conn.execute("SELECT COUNT(*) FROM config_releases WHERE config_id=?", ("cfg-1",)).fetchone()[0]
        rollback_outcomes = [
            row[0]
            for row in conn.execute(
                "SELECT outcome FROM audit_events WHERE operation='config.rollback'"
            ).fetchall()
        ]
        conn.close()
        self.assertEqual(count, 3)
        self.assertIn("committed", rollback_outcomes)
        self.assertIn("rejected", rollback_outcomes)

    def test_other_tenant_sees_nothing(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        with self.assertRaises(ConfigError) as caught:
            self.plane.active("other", "cfg-1")
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("other", "cfg-1", {"opening_hours": "immer"}, "x")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_same_values_same_release_number_same_hash(self):
        body = template_body()
        template = validate_template(body)
        document = {
            "base_release_no": None,
            "config_id": "cfg-1",
            "field_access": {name: spec["access"] for name, spec in template["fields"].items()},
            "release_no": 1,
            "synthetic": True,
            "template_id": template["template_id"],
            "template_version": template["template_version"],
            "tenant_id": "synth-tenant",
            "values": validate_values(template, initial_values()),
        }
        self.assertEqual(hash_value(document), hash_value(json.loads(json.dumps(document))))

    def test_author_cannot_release_fixed_field_in_values(self):
        values = dict(initial_values())
        values["allow_unattended_send"] = True
        with self.assertRaises(ConfigError) as caught:
            self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", values, "rel-open")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_customer_cannot_change_fixed_field(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("cust", "cfg-1", {"allow_unattended_send": True}, "fixed-1")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_fixed_value_in_release_and_hash_tracks_template_version(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        record = self.plane.release_record("konrad", "cfg-1", 1)
        self.assertIs(record["document"]["values"]["allow_unattended_send"], False)
        self.assertEqual(record["document"]["field_access"]["allow_unattended_send"], "fixed")
        other = dict(template_body())
        other["template_version"] = "1.2.0"
        self.plane.register_template("konrad", other)
        second = self.plane.release("konrad", "cfg-2", "kfz-terminannahme", "1.2.0", initial_values(), "rel-2")
        first = self.plane.release_record("konrad", "cfg-1", 1)
        self.assertNotEqual(second["release_hash"], first["release_hash"])

    def test_template_rejects_fixed_without_value(self):
        broken = template_body()
        broken["fields"]["allow_unattended_send"] = {
            "access": "fixed",
            "type": "boolean",
            "required": True,
        }
        with self.assertRaises(ConfigError) as caught:
            validate_template(broken)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_change_after_rollback_gets_next_number(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "c1")
        stored = self.plane.release_record("konrad", "cfg-1", 2)
        self.plane.rollback("konrad", "cfg-1", 1, "b1")
        changed = self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 7-16"}, "c2")
        self.assertEqual(changed["release_no"], 3)
        active = self.plane.active("konrad", "cfg-1")
        self.assertEqual(active["release_no"], 3)
        self.assertEqual(active["values"]["opening_hours"], "Mo-Fr 7-16")
        self.assertEqual(self.plane.release_record("konrad", "cfg-1", 2), stored)
        self.assertEqual(stored["document"]["values"]["opening_hours"], "Mo-Fr 9-18")
        self.assertEqual(self.plane.release_record("konrad", "cfg-1", 3)["document"]["base_release_no"], 1)
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        count = conn.execute("SELECT COUNT(*) FROM config_releases WHERE config_id=?", ("cfg-1",)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 3)

    def test_customer_change_after_rollback_works(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "c1")
        self.plane.rollback("konrad", "cfg-1", 1, "b1")
        changed = self.plane.apply_changes("cust", "cfg-1", {"opening_hours": "Mo-Fr 7-16"}, "c2")
        self.assertEqual(changed["release_no"], 3)
        self.assertEqual(self.plane.active("cust", "cfg-1")["values"]["opening_hours"], "Mo-Fr 7-16")

    def test_base_release_no_tracks_parent(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.assertIsNone(self.plane.release_record("konrad", "cfg-1", 1)["document"]["base_release_no"])
        self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "c1")
        self.assertEqual(self.plane.release_record("konrad", "cfg-1", 2)["document"]["base_release_no"], 1)

    def test_replay_after_rollback_change_is_idempotent(self):
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "c1")
        self.plane.rollback("konrad", "cfg-1", 1, "b1")
        changed = self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 7-16"}, "c2")
        again = self.plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 7-16"}, "c2")
        self.assertEqual(changed, again)
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        count = conn.execute("SELECT COUNT(*) FROM config_releases WHERE config_id=?", ("cfg-1",)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
