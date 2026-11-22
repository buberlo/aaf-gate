import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.canon import SCHEMA_VERSION
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.policy import policy_hash, resolve
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
CLOCK = FixedClock("2026-11-14T10:00:00Z")


def template_body():
    return json.loads((ROOT / "templates" / "kfz-terminannahme" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


def fresh_plane(folder=None):
    folder = folder or tempfile.mkdtemp()
    store = Store(str(Path(folder) / "cfg.sqlite"))
    seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
    plane = ConfigPlane(store, CLOCK)
    plane.register_template("konrad", template_body())
    return folder, plane


class ReleaseBindingTests(unittest.TestCase):
    def test_rb01_deterministic_release_hash(self):
        hashes = []
        for _ in range(2):
            folder = tempfile.mkdtemp()
            _, plane = fresh_plane(folder)
            released = plane.release(
                "konrad",
                "cfg-1",
                "kfz-terminannahme",
                "1.1.0",
                initial_values(),
                "rb1",
            )
            hashes.append(released["release_hash"])
        self.assertEqual(hashes[0], hashes[1])
        self.assertTrue(hashes[0].startswith("sha256:"))

    def test_rb02_template_shape_changes_release_hash(self):
        first = tempfile.mkdtemp()
        _, plane_a = fresh_plane(first)
        base = plane_a.release("konrad", "cfg-a", "kfz-terminannahme", "1.1.0", initial_values(), "a")
        second = tempfile.mkdtemp()
        store = Store(str(Path(second) / "cfg.sqlite"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane_b = ConfigPlane(store, CLOCK)
        variant = copy.deepcopy(template_body())
        variant["fields"]["shop_name"]["max"] = 1000
        plane_b.register_template("konrad", variant)
        other = plane_b.release("konrad", "cfg-b", "kfz-terminannahme", "1.1.0", initial_values(), "b")
        self.assertNotEqual(base["release_hash"], other["release_hash"])
        conn = sqlite3.connect(str(Path(second) / "cfg.sqlite"))
        hashes = {
            row[0]
            for row in conn.execute(
                "SELECT body_hash FROM config_templates WHERE template_id='kfz-terminannahme'"
            ).fetchall()
        }
        conn.close()
        self.assertEqual(len(hashes), 1)
        record = plane_b.release_record("konrad", "cfg-b", 1)
        self.assertEqual(record["document"]["template_body_hash"], plane_b.release_record("konrad", "cfg-b", 1)["document"]["template_body_hash"])
        self.assertNotEqual(base["release_hash"], other["release_hash"])

    def test_rb03_document_carries_binding_fields(self):
        folder, plane = fresh_plane()
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rb3")
        record = plane.release_record("konrad", "cfg-1", 1)
        doc = record["document"]
        conn = sqlite3.connect(str(Path(folder) / "cfg.sqlite"))
        body_hash = conn.execute(
            "SELECT body_hash FROM config_templates WHERE template_id=? AND template_version=?",
            ("kfz-terminannahme", "1.1.0"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(doc["template_body_hash"], body_hash)
        self.assertEqual(doc["core_schema_version"], SCHEMA_VERSION)
        self.assertEqual(doc["core_floor_hash"], policy_hash(resolve([])))

    def test_rb04_rollback_preserves_binding_fields(self):
        folder, plane = fresh_plane()
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "r1")
        first = plane.release_record("konrad", "cfg-1", 1)["document"]
        plane.apply_changes("konrad", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "r2")
        plane.rollback("konrad", "cfg-1", 1, "rb")
        rolled = plane.release_record("konrad", "cfg-1", 1)["document"]
        active = plane.active("konrad", "cfg-1")
        self.assertEqual(active["release_no"], 1)
        for key in ("template_body_hash", "core_schema_version", "core_floor_hash"):
            self.assertEqual(rolled[key], first[key])

    def test_rb05_provenance_carries_binding_fields(self):
        from framework.appliance import build_appliance_package

        folder = tempfile.mkdtemp()
        _, plane = fresh_plane(folder)
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rb5")
        document = plane.release_record("konrad", "cfg-1")["document"]
        package = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", package)
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["template_body_hash"], document["template_body_hash"])
        self.assertEqual(provenance["core_schema_version"], document["core_schema_version"])
        self.assertEqual(provenance["core_floor_hash"], document["core_floor_hash"])


if __name__ == "__main__":
    unittest.main()