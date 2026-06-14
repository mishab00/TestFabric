from __future__ import annotations

import unittest

from testfabric.health.models import HealthCheckResult
from testfabric.health.policy import evaluate_health


class HealthPolicyTests(unittest.TestCase):
    def test_required_failure_marks_snapshot_unhealthy(self) -> None:
        snapshot = evaluate_health(
            [
                HealthCheckResult(name="disk", ok=False, severity="required", message="disk too low"),
                HealthCheckResult(name="docker", ok=True, severity="required"),
            ]
        )

        self.assertEqual(snapshot.state, "unhealthy")
        self.assertEqual(snapshot.reasons, ["disk too low"])

    def test_warning_failure_marks_snapshot_degraded(self) -> None:
        snapshot = evaluate_health(
            [
                HealthCheckResult(name="memory", ok=False, severity="warning", message="memory unknown"),
            ]
        )

        self.assertEqual(snapshot.state, "degraded")
        self.assertEqual(snapshot.reasons, ["memory unknown"])

    def test_all_green_marks_snapshot_healthy(self) -> None:
        snapshot = evaluate_health(
            [
                HealthCheckResult(name="disk", ok=True, severity="required"),
                HealthCheckResult(name="docker", ok=True, severity="required"),
            ]
        )

        self.assertEqual(snapshot.state, "healthy")
        self.assertEqual(snapshot.reasons, [])


if __name__ == "__main__":
    unittest.main()
