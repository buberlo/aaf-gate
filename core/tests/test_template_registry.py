import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import verify
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store
from framework.templates import expected_hashes

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
HASHES_PATH = ROOT / "templates" / "HASHES.json"
TEMPLATE_DIR = ROOT / "templates"
TENANT = "synth-tenant"
CLOCK = FixedClock("2026-11-14T11:30:00Z")


def template_body():
    return json.loads((TEMPLATE_DIR / "kfz-terminannahme" / "template.json").read_text(encoding="utf-8"))


class TemplateRegistryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(self.store, (("konrad", TENANT, "human", ("author",)),))

    def test_tr01_all_listed_templates_register_with_expected(self):
        expected = expected_hashes(HASHES_PATH)
        plane = ConfigPlane(self.store, CLOCK, expected=expected)
        for path in sorted(TEMPLATE_DIR.glob("*/template.json")):
            body = json.loads(path.read_text(encoding="utf-8"))
            result = plane.register_template("konrad", body)
            self.assertTrue(result["template_hash"].startswith("sha256:"))

    def test_tr02_hash_mismatch_skips_storage(self):
        expected = expected_hashes(HASHES_PATH)
        plane = ConfigPlane(self.store, CLOCK, expected=expected)
        body = copy.deepcopy(template_body())
        body["title"] = "Geänderte Terminannahme"
        with self.assertRaises(ConfigError) as ctx:
            plane.register_template("konrad", body)
        self.assertEqual(ctx.exception.code, "TEMPLATE_HASH_MISMATCH")
        conn = self.store.connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM config_templates WHERE template_id=? AND template_version=?",
            ("kfz-terminannahme", "1.1.0"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_tr03_unlisted_version_rejected(self):
        expected = expected_hashes(HASHES_PATH)
        plane = ConfigPlane(self.store, CLOCK, expected=expected)
        body = copy.deepcopy(template_body())
        body["template_version"] = "9.9.9"
        with self.assertRaises(ConfigError) as ctx:
            plane.register_template("konrad", body)
        self.assertEqual(ctx.exception.code, "TEMPLATE_UNLISTED")

    def test_tr04_rejections_audited_and_chain_valid(self):
        expected = expected_hashes(HASHES_PATH)
        plane = ConfigPlane(self.store, CLOCK, expected=expected)
        mismatch = copy.deepcopy(template_body())
        mismatch["title"] = "Abweichend"
        unlisted = copy.deepcopy(template_body())
        unlisted["template_version"] = "9.9.9"
        with self.assertRaises(ConfigError):
            plane.register_template("konrad", mismatch)
        with self.assertRaises(ConfigError):
            plane.register_template("konrad", unlisted)
        conn = self.store.connect()
        rows = conn.execute(
            """SELECT error_code FROM audit_events
               WHERE operation='config.register_template' AND outcome='rejected'
               ORDER BY sequence"""
        ).fetchall()
        conn.close()
        self.assertEqual([row[0] for row in rows], ["TEMPLATE_HASH_MISMATCH", "TEMPLATE_UNLISTED"])
        conn = self.store.connect()
        result = verify(conn, TENANT)
        conn.close()
        self.assertTrue(result["ok"])

    def test_tr05_development_mode_allows_modified_template(self):
        body = copy.deepcopy(template_body())
        body["title"] = "Entwicklungsmodus"
        dev = ConfigPlane(self.store, CLOCK)
        result = dev.register_template("konrad", body)
        self.assertEqual(result["template_id"], "kfz-terminannahme")

    def test_tr06_idempotent_listed_registration(self):
        expected = expected_hashes(HASHES_PATH)
        plane = ConfigPlane(self.store, CLOCK, expected=expected)
        body = template_body()
        first = plane.register_template("konrad", body)
        second = plane.register_template("konrad", body)
        self.assertEqual(first, second)
        conn = self.store.connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM config_templates WHERE template_id=? AND template_version=?",
            ("kfz-terminannahme", "1.1.0"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()