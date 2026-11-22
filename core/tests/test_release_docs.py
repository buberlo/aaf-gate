import re
import unittest
from pathlib import Path

from framework.version import VERSION

STATUS_HEADINGS = [
    "## Implemented and locally tested",
    "## Open on device",
    "## Open with Google",
    "## Open legally and organizationally before real customer data",
    "## Not included",
]

INSTALL_HEADINGS = [
    "## Prerequisites",
    "## Build package",
    "## Initial installation",
    "## Operations",
    "## Backup and restore",
    "## Limits",
]

FORBIDDEN = ("dsgvo-konform", "gdpr-compliant", "compliant", "zertifiziert", "garantiert", "€", " eur")

REQUIRED_COMMANDS = (
    "gate-init",
    "verify-install",
    "verify-device",
    "gate-stop",
    "gate-resume",
    "gate-status",
    "gate-backup",
    "gate-restore",
    "version",
)


class ReleaseDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]

    def _heading_order(self, text, headings):
        indices = [text.index(h) for h in headings]
        self.assertEqual(indices, sorted(indices))

    def test_rd01_status_headings(self):
        path = self.root / "docs/v1/STATUS.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        self.assertIn(VERSION, text)
        self.assertIn("not released for production", text)
        self._heading_order(text, STATUS_HEADINGS)

    def test_rd02_status_google_and_device(self):
        text = (self.root / "docs/v1/STATUS.md").read_text(encoding="utf-8")
        google = text.split("## Open with Google", 1)[1].split("##", 1)[0]
        device = text.split("## Open on device", 1)[1].split("##", 1)[0]
        self.assertIn("Gmail", google)
        self.assertIn("Developer Preview", google)
        for token in ("G1", "S9", "B3", "verify-device"):
            self.assertIn(token, device)

    def test_rd03_install_headings(self):
        path = self.root / "docs/v1/INSTALLATION-AND-OPERATIONS.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        self.assertIn(VERSION, text)
        self.assertIn("not yet executed on device", text)
        self._heading_order(text, INSTALL_HEADINGS)

    def test_rd04_install_commands_exist(self):
        cli = (self.root / "core/framework/cli.py").read_text(encoding="utf-8")
        names = set(re.findall(r'add_parser\("([a-z0-9-]+)"', cli))
        install = (self.root / "docs/v1/INSTALLATION-AND-OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        for cmd in REQUIRED_COMMANDS:
            self.assertIn(cmd, install)
        tokens = set(re.findall(r"`(gate-[a-z0-9-]+|verify-[a-z0-9-]+|version)`", install))
        for token in tokens:
            self.assertIn(token, names)

    def test_rd05_no_claims(self):
        for name in ("STATUS.md", "INSTALLATION-AND-OPERATIONS.md"):
            text = (self.root / "docs/v1" / name).read_text(encoding="utf-8").lower()
            for word in FORBIDDEN:
                self.assertNotIn(word, text)

    def test_rd07_status_limits_assumed(self):
        status = (self.root / "docs/v1/STATUS.md").read_text(encoding="utf-8")
        impl = status.split("## Implemented and locally tested", 1)[1].split("##", 1)[0]
        self.assertIn("Assumption", impl)
        self.assertIn("pilot week", impl)
        self.assertIn("reset", impl)
        self.assertNotIn("Pilot 1 start values open", impl)
        self.assertNotIn("not set yet", impl)
        not_included = status.split("## Not included", 1)[1]
        self.assertNotIn("Call limit start values", not_included)
        for token in ("Sending mail", "Computer Use", "build_appliance_package"):
            self.assertIn(token, not_included)
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("start values open", readme.lower())

    def test_rd08_kostenlimit_doc(self):
        path = self.root / "docs/v0.5/171-call-limits.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        for token in ("E48-1", "Assumption", "3600", "160", "40", "80", "gate_calls"):
            self.assertIn(token, text)
        v05 = (self.root / "docs/v0.5/README.md").read_text(encoding="utf-8")
        self.assertIn("171-call-limits.md", v05)
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("58dccbe", changelog)
        self.assertIn("160", changelog)

    def test_rd06_links(self):
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("docs/v1/STATUS.md", readme)
        self.assertIn("docs/v1/INSTALLATION-AND-OPERATIONS.md", readme)
        self.assertIn("79dd30b", changelog)
        self.assertIn("docs/v1/STATUS.md", changelog)


if __name__ == "__main__":
    unittest.main()