import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store

from seed_config import seed_config
from test_install_check import initial_values, template_body


class ConfigPlaneConflictTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = str(Path(self.folder) / "c.sqlite")
        self.store = Store(self.path)
        seed_config(self.store, (("konrad", "synth-tenant", "human", ("author",)),))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-14T15:20:00Z"))
        self.plane.register_template("konrad", template_body())

    def test_cc01_integrity_maps_to_conflict(self):
        with patch.object(self.plane, "_release_new", side_effect=sqlite3.IntegrityError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
            self.assertEqual(ctx.exception.code, "CONFLICT")
        conn = sqlite3.connect(self.path)
        rows = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE outcome='rejected'"
        ).fetchall()
        count = conn.execute("SELECT COUNT(*) FROM config_releases").fetchone()[0]
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ("rejected", "CONFLICT"))
        self.assertEqual(count, 0)

    def test_cc02_operational_maps_to_storage_without_audit(self):
        before = sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        with patch.object(self.plane, "_release_new", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")
        conn = sqlite3.connect(self.path)
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE outcome='rejected'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(before, after)
        self.assertEqual(rejected, 0)

    def test_cc03_rolled_back_release_can_succeed(self):
        with patch.object(self.plane, "_release_new", side_effect=sqlite3.IntegrityError("synthetic")):
            with self.assertRaises(ConfigError):
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        conn = sqlite3.connect(self.path)
        count = conn.execute("SELECT COUNT(*) FROM config_releases").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_cc04_connect_failure_storage_unavailable(self):
        with patch.object(self.store, "connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")

    def test_su01_release_connect_failure_storage_unavailable(self):
        with patch.object(self.store, "connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-su-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")

    def test_su02_active_connect_failure_storage_unavailable(self):
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        with patch.object(self.store, "connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.active("konrad", "cfg-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")

    def test_su03_release_record_connect_failure_storage_unavailable(self):
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        with patch.object(self.store, "connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release_record("konrad", "cfg-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")

    def test_su04_resolve_failure_storage_unavailable(self):
        with patch("framework.configplane.resolve", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(ConfigError) as ctx:
                self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()