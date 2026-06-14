from __future__ import annotations

from dataclasses import dataclass

from testfabric.health.models import HealthCheckResult, WorkerHealthSnapshot


@dataclass(frozen=True)
class HealthPolicy:
    require_docker: bool = False
    min_disk_gb: int = 1
    min_mem_gb: int | None = None


def evaluate_health(checks: list[HealthCheckResult]) -> WorkerHealthSnapshot:
    required_failures = [c.message or c.name for c in checks if not c.ok and c.severity == "required"]
    warning_failures = [c.message or c.name for c in checks if not c.ok and c.severity == "warning"]

    if required_failures:
        return WorkerHealthSnapshot(state="unhealthy", checks=list(checks), reasons=required_failures)
    if warning_failures:
        return WorkerHealthSnapshot(state="degraded", checks=list(checks), reasons=warning_failures)
    return WorkerHealthSnapshot(state="healthy", checks=list(checks), reasons=[])
