import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from framework import canon
from framework.cli import main
from framework.version import VERSION


class VersionTests(unittest.TestCase):
    def test_ve01_version_constant(self):
        self.assertEqual(VERSION, "1.0.0rc1")
        self.assertIsNotNone(re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", VERSION))

    def test_ve02_schema_unchanged(self):
        self.assertEqual(canon.SCHEMA_VERSION, "0.2-draft")

    def test_ve03_cli_version(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["version"])
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue())
        self.assertEqual(set(body.keys()), {"core_version", "ok", "schema_version"})
        self.assertEqual(body["core_version"], VERSION)
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema_version"], canon.SCHEMA_VERSION)

    def test_ve04_readme_lists_cli_commands(self):
        root = Path(__file__).resolve().parents[2]
        cli_source = (root / "core/framework/cli.py").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        names = re.findall(r'add_parser\("([a-z0-9-]+)"', cli_source)
        self.assertTrue(names)
        self.assertIn("version", names)
        for name in names:
            self.assertIn("`" + name + "`", readme)

    def test_ve05_changelog(self):
        root = Path(__file__).resolve().parents[2]
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 1.0.0rc1", changelog)
        for short in ("41575e0", "58dccbe", "8c8da50", "813fe28", "575bef9"):
            self.assertIn(short, changelog)

    def test_ve06_docs_and_readme_updated(self):
        root = Path(__file__).resolve().parents[2]
        readme = (root / "README.md").read_text(encoding="utf-8")
        spur = (root / "docs/v0.4/06-APPLIANCE-SPUR-A.md").read_text(encoding="utf-8")
        self.assertNotIn("Agentenschleife, Kanäle, Versand, Stopp", readme)
        self.assertNotIn("Der Schalter ist nicht implementiert.", spur)


if __name__ == "__main__":
    unittest.main()