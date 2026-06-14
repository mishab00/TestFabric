# testfabric.health — Health check probes & policy
#
# Micro-package: testfabric[core]
# Dependencies: testfabric.core, docker (optional for DockerHealthProbeSuite)
#
# Provides:
#   - HealthPolicy: configurable health requirements (docker, disk, memory)
#   - evaluate_health: aggregate probe results into a WorkerHealthSnapshot
#   - LocalHealthProbeSuite: disk space, writable paths, memory checks
#   - DockerHealthProbeSuite: Docker daemon connectivity & version checks
#
# Usage:
#   from testfabric.health import HealthPolicy, evaluate_health, LocalHealthProbeSuite
#   policy = HealthPolicy(require_docker=False, min_disk_gb=2)
#   checks = LocalHealthProbeSuite(policy).probe(writable_paths=[Path(".")])
#   snapshot = evaluate_health(checks)
#   print(snapshot.state)  # "healthy" | "degraded" | "unhealthy"

from .models import HealthCheckResult, HealthSeverity, HealthState, WorkerHealthSnapshot
from .policy import HealthPolicy, evaluate_health
from .probes_docker import DockerHealthProbeSuite
from .probes_local import LocalHealthProbeSuite

__all__ = [
    "DockerHealthProbeSuite",
    "HealthCheckResult",
    "HealthPolicy",
    "HealthSeverity",
    "HealthState",
    "LocalHealthProbeSuite",
    "WorkerHealthSnapshot",
    "evaluate_health",
]
