import copy
import tempfile
import unittest
from pathlib import Path

from framework.canon import hash_value
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane, validate_template
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    return __import__("json").loads((ROOT / relative).read_text(encoding="utf-8"))


def lead_values():
    profile = load("profiles/lead-scout-example/profile.json")
    return dict(profile["values"])


def flippy_values():
    profile = load("profiles/flippy-catch-example/profile.json")
    return dict(profile["values"])


class SecurityListTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(
            store,
            (
                ("konrad", "synth-lists", "human", ("author",)),
                ("customer", "synth-lists", "human", ("customer",)),
            ),
        )
        self.plane = ConfigPlane(store, FixedClock("2026-11-13T20:00:00Z"))
        self.template = load("templates/b2b-lead-research/template.json")
        self.plane.register_template("konrad", self.template)
        self.profile = load("profiles/lead-scout-example/profile.json")

    def _release_lead(self, values, key="lead-release"):
        return self.plane.release(
            "konrad",
            self.profile["profile_id"],
            self.template["template_id"],
            self.template["template_version"],
            values,
            key,
        )

    def test_sl01_empty_blocked_domains_rejected(self):
        values = lead_values()
        values["blocked_domains"] = []
        with self.assertRaises(ConfigError) as caught:
            self._release_lead(values)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl02_missing_contains_element_rejected(self):
        values = lead_values()
        values["blocked_domains"] = ["example.com"]
        with self.assertRaises(ConfigError) as caught:
            self._release_lead(values)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl03_invalid_domain_format_rejected(self):
        values = lead_values()
        for blocked in (["LinkedIn.com "], ["https://linkedin.com"]):
            values["blocked_domains"] = blocked
            with self.assertRaises(ConfigError) as caught:
                self._release_lead(values, "bad-" + blocked[0])
            self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl04_duplicate_domains_rejected(self):
        values = lead_values()
        values["blocked_domains"] = ["linkedin.com", "linkedin.com"]
        with self.assertRaises(ConfigError) as caught:
            self._release_lead(values)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl05_extra_domain_with_contains_ok(self):
        values = lead_values()
        values["blocked_domains"] = ["linkedin.com", "example.com"]
        result = self._release_lead(values)
        self.assertEqual(result["values"]["blocked_domains"], ["linkedin.com", "example.com"])

    def test_sl06_invalid_template_shapes_rejected(self):
        cases = (
            {
                "template_id": "probe-string-contains",
                "template_version": "9.0.0",
                "synthetic": True,
                "title": "Probe",
                "fields": {
                    "note": {"access": "locked", "type": "string", "required": True, "contains": ["x"]},
                },
            },
            {
                "template_id": "probe-bad-contains",
                "template_version": "9.0.1",
                "synthetic": True,
                "title": "Probe",
                "fields": {
                    "sites": {
                        "access": "locked",
                        "type": "array",
                        "item_type": "domain",
                        "required": True,
                        "contains": ["NOT_A_DOMAIN"],
                    },
                },
            },
            {
                "template_id": "probe-unique-false",
                "template_version": "9.0.2",
                "synthetic": True,
                "title": "Probe",
                "fields": {
                    "tags": {
                        "access": "locked",
                        "type": "array",
                        "item_type": "string",
                        "required": True,
                        "unique": False,
                    },
                },
            },
            {
                "template_id": "probe-contains-max",
                "template_version": "9.0.3",
                "synthetic": True,
                "title": "Probe",
                "fields": {
                    "sites": {
                        "access": "locked",
                        "type": "array",
                        "item_type": "domain",
                        "required": True,
                        "max": 0,
                        "contains": ["linkedin.com"],
                    },
                },
            },
        )
        for body in cases:
            with self.assertRaises(ConfigError) as caught:
                validate_template(body)
            self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl07_author_apply_invalid_blocked_domains(self):
        self._release_lead(lead_values())
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes(
                "konrad",
                self.profile["profile_id"],
                {"blocked_domains": ["example.com"]},
                "author-bad",
            )
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes(
                "customer",
                self.profile["profile_id"],
                {"blocked_domains": ["example.com"]},
                "cust-bad",
            )
        self.assertEqual(caught.exception.code, "LOCKED_FIELD")

    def test_sl08_flippy_release_and_list_rules(self):
        template = load("templates/ecommerce-product-sales/template.json")
        self.plane.register_template("konrad", template)
        profile = load("profiles/flippy-catch-example/profile.json")
        self.plane.release(
            "konrad",
            profile["profile_id"],
            template["template_id"],
            template["template_version"],
            flippy_values(),
            "flippy-ok",
        )
        bad_claims = flippy_values()
        bad_claims["claims_blocklist"] = [
            "Bestseller-Rang",
            "unverified patent or learner statement",
            "Bestseller-Rang",
        ]
        with self.assertRaises(ConfigError) as caught:
            self.plane.release(
                "konrad",
                "flippy-dup",
                template["template_id"],
                template["template_version"],
                bad_claims,
                "flippy-dup",
            )
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        bad_domains = flippy_values()
        bad_domains["blocked_automation_domains"] = []
        with self.assertRaises(ConfigError) as caught:
            self.plane.release(
                "konrad",
                "flippy-empty",
                template["template_id"],
                template["template_version"],
                bad_domains,
                "flippy-empty",
            )
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_sl09_template_contains_changes_release_hash(self):
        body_a = copy.deepcopy(self.template)
        body_a["template_version"] = "0.3.1"
        body_a["fields"]["blocked_domains"]["contains"] = ["linkedin.com"]
        body_b = copy.deepcopy(self.template)
        body_b["template_version"] = "0.3.2"
        body_b["fields"]["blocked_domains"]["contains"] = ["linkedin.com", "example.com"]
        self.assertNotEqual(hash_value(validate_template(body_a)), hash_value(validate_template(body_b)))
        self.plane.register_template("konrad", body_a)
        self.plane.register_template("konrad", body_b)
        values = lead_values()
        first = self.plane.release("konrad", "cfg-a", body_a["template_id"], "0.3.1", values, "rel-a")
        values_b = lead_values()
        values_b["blocked_domains"] = ["linkedin.com", "example.com"]
        second = self.plane.release("konrad", "cfg-b", body_b["template_id"], "0.3.2", values_b, "rel-b")
        self.assertNotEqual(first["release_hash"], second["release_hash"])


if __name__ == "__main__":
    unittest.main()
