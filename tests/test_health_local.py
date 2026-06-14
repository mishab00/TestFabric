from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from testfabric.health.policy import HealthPolicy
from testfabric.health.probes_local import LocalHealthProbeSuite


class LocalHealthProbeTests(unittest.TestCase):
    def test_probe_reports_healthy_for_writable_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy = HealthPolicy(min_disk_gb=0, min_mem_gb=None)
            checks = LocalHealthProbeSuite(policy).probe(writable_paths=[root / "artifacts", root / "tmp"])

        self.assertTrue(any(c.name == "disk" and c.ok for c in checks))
        self.assertTrue(all(c.ok for c in checks if c.name.startswith("writable:")))

    def test_probe_reports_disk_failure_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy = HealthPolicy(min_disk_gb=10, min_mem_gb=None)
            fake_usage = SimpleNamespace(total=20 * 1024 ** 3, used=19 * 1024 ** 3, free=1 * 1024 ** 3)

            with patch("testfabric.health.probes_local.shutil.disk_usage", return_value=fake_usage):
                checks = LocalHealthProbeSuite(policy).probe(writable_paths=[root / "artifacts"])

        disk = next(c for c in checks if c.name == "disk")
        self.assertFalse(disk.ok)
        self.assertIn("below minimum", disk.message)


if __name__ == "__main__":
    unittest.main()
