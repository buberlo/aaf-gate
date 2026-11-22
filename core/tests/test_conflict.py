import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework.errors import CommandError
from framework.service import Service

CREATE = {
    "operation": "create_run",
    "idempotency_key": "cf-1",
    "expected_state_version": 0,
    "payload": {"rules_version": "material-prep-1"},
}


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = str(Path(self.folder) / "c.sqlite")
        self.service = Service.open(self.path)
        self.service.store.add_principal("op-a", "demo-a", "human")
        self.service.store.bind_role("op-a", "operator")

    def test_cf01_integrity_error_maps_to_conflict(self):
        with patch.object(self.service, "_op_create_run", side_effect=sqlite3.IntegrityError("synthetic")):
            with self.assertRaises(CommandError) as ctx:
                self.service.command("op-a", CREATE)
            self.assertEqual(ctx.exception.code, "CONFLICT")
        conn = sqlite3.connect(self.path)
        audit = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE actor_id='op-a' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        self.assertEqual(audit, ("rejected", "CONFLICT"))
        self.assertEqual(count, 0)

    def test_cf02_operational_error_stays_storage_unavailable(self):
        with patch.object(self.service, "_op_create_run", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(CommandError) as ctx:
                self.service.command("op-a", CREATE)
            self.assertEqual(ctx.exception.code, "STORAGE_UNAVAILABLE")

    def test_cf03_rolled_back_attempt_allows_retry(self):
        with patch.object(self.service, "_op_create_run", side_effect=sqlite3.IntegrityError("synthetic")):
            with self.assertRaises(CommandError):
                self.service.command("op-a", CREATE)
        created = self.service.command("op-a", CREATE)
        self.assertIn("run_id", created)


if __name__ == "__main__":
    unittest.main()