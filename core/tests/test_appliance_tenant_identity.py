import json
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package, dry_run_lines
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import package_identity
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body


class ApplianceTenantIdentityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            self.store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("mira", "werkstatt-tenant", "human", ("author",)),
            ),
        )
        body = template_body()
        self.plane.register_template("konrad", body)
        self.plane.register_template("mira", body)
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-k")
        self.plane.release("mira", "cfg-werk", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-m")
        self.document_k = self.plane.release_record("konrad", "cfg-1")["document"]
        self.document_m = self.plane.release_record("mira", "cfg-werk")["document"]

    def test_ti01_provenance_tenant_and_config_ids(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["tenant_id"], self.document_k["tenant_id"])
        self.assertEqual(provenance["config_id"], self.document_k["config_id"])

    def test_ti02_dry_run_lines_identity_fields(self):
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        lines = dry_run_lines(plain)
        tenant = self.document_k["tenant_id"]
        config = self.document_k["config_id"]
        mode_idx = next(i for i, line in enumerate(lines) if line.startswith("device_mode "))
        self.assertEqual(lines[mode_idx + 1], f"tenant_id {tenant}")
        self.assertEqual(lines[mode_idx + 2], f"config_id {config}")
        self.assertEqual(lines[mode_idx + 3], "customer_profile -")
        named = Path(self.folder) / "named"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            named,
            customer_profile="client",
        )
        lines_named = dry_run_lines(named)
        mode_named = next(i for i, line in enumerate(lines_named) if line.startswith("device_mode "))
        self.assertEqual(lines_named[mode_named + 3], "customer_profile client")

    def test_ti03_package_identity_fields(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, customer_profile="client")
        identity = package_identity(target)
        self.assertEqual(identity["tenant_id"], self.document_k["tenant_id"])
        self.assertEqual(identity["config_id"], self.document_k["config_id"])
        self.assertEqual(identity["customer_profile"], "client")

    def test_ti04_package_identity_invalid_provenance_values(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        data = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        data["tenant_id"] = 5
        if "config_id" in data:
            del data["config_id"]
        (target / "provenance.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        identity = package_identity(target)
        self.assertIsNone(identity["tenant_id"])
        self.assertIsNone(identity["config_id"])
        self.assertIn("package_hash", identity)

    def test_ti05_two_tenants_differ(self):
        pkg_k = Path(self.folder) / "k"
        pkg_m = Path(self.folder) / "m"
        built_k = build_appliance_package(self.plane, "konrad", "cfg-1", pkg_k)
        built_m = build_appliance_package(self.plane, "mira", "cfg-werk", pkg_m)
        prov_k = json.loads((pkg_k / "provenance.json").read_text(encoding="utf-8"))
        prov_m = json.loads((pkg_m / "provenance.json").read_text(encoding="utf-8"))
        self.assertNotEqual(prov_k["tenant_id"], prov_m["tenant_id"])
        self.assertNotEqual(prov_k["config_id"], prov_m["config_id"])
        self.assertNotEqual(built_k["package_hash"], built_m["package_hash"])


if __name__ == "__main__":
    unittest.main()