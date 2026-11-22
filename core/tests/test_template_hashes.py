import copy
import json
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store
from framework.templates import check_template, expected_hashes

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
HASHES_PATH = ROOT / "templates" / "HASHES.json"
TEMPLATE_DIR = ROOT / "templates"


def template_body():
    return json.loads((TEMPLATE_DIR / "kfz-terminannahme" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


class TemplateHashTests(unittest.TestCase):
    def test_th01_golden_hashes_match_repo_templates(self):
        expected = expected_hashes(HASHES_PATH)
        templates = sorted(TEMPLATE_DIR.glob("*/template.json"))
        self.assertEqual(len(expected), len(templates))
        for path in templates:
            body = json.loads(path.read_text(encoding="utf-8"))
            check_template(body, expected)

    def test_th02_modified_template_raises_mismatch(self):
        expected = expected_hashes(HASHES_PATH)
        body = copy.deepcopy(template_body())
        body["title"] = "Geänderte Terminannahme"
        with self.assertRaises(ConfigError) as ctx:
            check_template(body, expected)
        self.assertEqual(ctx.exception.code, "TEMPLATE_HASH_MISMATCH")

    def test_th03_unknown_version_unlisted(self):
        expected = expected_hashes(HASHES_PATH)
        body = copy.deepcopy(template_body())
        body["template_version"] = "9.9.9"
        with self.assertRaises(ConfigError) as ctx:
            check_template(body, expected)
        self.assertEqual(ctx.exception.code, "TEMPLATE_UNLISTED")

    def test_th04_invalid_hashes_file(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_key = Path(folder) / "bad-key.json"
            bad_key.write_text(json.dumps({"kfz-terminannahme": "sha256:00"}), encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                expected_hashes(bad_key)
            self.assertEqual(ctx.exception.code, "INVALID_CONFIG")
            bad_value = Path(folder) / "bad-value.json"
            bad_value.write_text(json.dumps({"kfz-terminannahme@1.1.0": "deadbeef"}), encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                expected_hashes(bad_value)
            self.assertEqual(ctx.exception.code, "INVALID_CONFIG")

    def _kfz_plane(self):
        folder = tempfile.mkdtemp()
        store = Store(str(Path(folder) / "cfg.sqlite"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane = ConfigPlane(store, FixedClock("2026-11-14T11:00:00Z"))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        return folder, plane

    def test_th05_package_build_with_expected_hashes(self):
        folder, plane = self._kfz_plane()
        expected = expected_hashes(HASHES_PATH)
        package = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", package, expected=expected)
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["template_body_hash"], expected["kfz-terminannahme@1.1.0"])

    def test_th06_wrong_expected_prevents_package_dir(self):
        folder, plane = self._kfz_plane()
        expected = dict(expected_hashes(HASHES_PATH))
        expected["kfz-terminannahme@1.1.0"] = "sha256:" + "f" * 64
        package = Path(folder) / "pkg-bad"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(plane, "konrad", "cfg-1", package, expected=expected)
        self.assertEqual(ctx.exception.code, "TEMPLATE_HASH_MISMATCH")
        self.assertFalse(package.exists())

    def test_th07_package_build_without_expected_unchanged(self):
        folder, plane = self._kfz_plane()
        package = Path(folder) / "pkg-open"
        result = build_appliance_package(plane, "konrad", "cfg-1", package)
        self.assertTrue(package.is_dir())
        self.assertIn("release_hash", result)


if __name__ == "__main__":
    unittest.main()