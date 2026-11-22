import re
import unittest
from pathlib import Path

from framework.version import VERSION

STATUS_HEADINGS = [
    "## Implementiert und lokal getestet",
    "## Offen am Gerät",
    "## Offen bei Google",
    "## Offen rechtlich und organisatorisch vor echten Kundendaten",
    "## Nicht enthalten",
]

INSTALL_HEADINGS = [
    "## Voraussetzungen",
    "## Paket bauen",
    "## Erstinstallation",
    "## Betrieb",
    "## Sicherung und Wiederherstellung",
    "## Grenzen",
]

FORBIDDEN = ("dsgvo-konform", "compliant", "zertifiziert", "garantiert", "€", " eur")

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
        self.assertIn("nicht produktiv freigegeben", text)
        self._heading_order(text, STATUS_HEADINGS)

    def test_rd02_status_google_and_device(self):
        text = (self.root / "docs/v1/STATUS.md").read_text(encoding="utf-8")
        google = text.split("## Offen bei Google", 1)[1].split("##", 1)[0]
        device = text.split("## Offen am Gerät", 1)[1].split("##", 1)[0]
        self.assertIn("Gmail", google)
        self.assertIn("Developer Preview", google)
        for token in ("G1", "S9", "B3", "verify-device"):
            self.assertIn(token, device)

    def test_rd03_install_headings(self):
        path = self.root / "docs/v1/INSTALLATION-UND-BETRIEB.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        self.assertIn(VERSION, text)
        self.assertIn("nicht am Gerät ausgeführt", text)
        self._heading_order(text, INSTALL_HEADINGS)

    def test_rd04_install_commands_exist(self):
        cli = (self.root / "core/framework/cli.py").read_text(encoding="utf-8")
        names = set(re.findall(r'add_parser\("([a-z0-9-]+)"', cli))
        install = (self.root / "docs/v1/INSTALLATION-UND-BETRIEB.md").read_text(
            encoding="utf-8"
        )
        for cmd in REQUIRED_COMMANDS:
            self.assertIn(cmd, install)
        tokens = set(re.findall(r"`(gate-[a-z0-9-]+|verify-[a-z0-9-]+|version)`", install))
        for token in tokens:
            self.assertIn(token, names)

    def test_rd05_no_claims(self):
        for name in ("STATUS.md", "INSTALLATION-UND-BETRIEB.md"):
            text = (self.root / "docs/v1" / name).read_text(encoding="utf-8").lower()
            for word in FORBIDDEN:
                self.assertNotIn(word, text)

    def test_rd07_status_limits_assumed(self):
        status = (self.root / "docs/v1/STATUS.md").read_text(encoding="utf-8")
        impl = status.split("## Implementiert und lokal getestet", 1)[1].split("##", 1)[0]
        self.assertIn("Annahme", impl)
        self.assertIn("Pilotwoche", impl)
        self.assertIn("neu festlegen", impl)
        self.assertNotIn("Startwerte für Pilot 1 offen", impl)
        self.assertNotIn("noch nicht gesetzt", impl)
        nicht = status.split("## Nicht enthalten", 1)[1]
        self.assertNotIn("Startwerte Kostenlimit", nicht)
        for token in ("Senden", "Computer Use", "build_appliance_package"):
            self.assertIn(token, nicht)
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Startwerte offen", readme)

    def test_rd08_kostenlimit_doc(self):
        path = self.root / "docs/v0.5/171-kostenlimit.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        for token in ("E48-1", "Annahme", "3600", "160", "40", "80", "gate_calls"):
            self.assertIn(token, text)
        v05 = (self.root / "docs/v0.5/README.md").read_text(encoding="utf-8")
        self.assertIn("171-kostenlimit.md", v05)
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("58dccbe", changelog)
        self.assertIn("160", changelog)

    def test_rd06_links(self):
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("docs/v1/STATUS.md", readme)
        self.assertIn("docs/v1/INSTALLATION-UND-BETRIEB.md", readme)
        self.assertIn("79dd30b", changelog)
        self.assertIn("docs/v1/STATUS.md", changelog)


if __name__ == "__main__":
    unittest.main()