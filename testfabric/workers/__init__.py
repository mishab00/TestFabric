# testfabric.workers — Worker pool & target management
#
# Micro-package: testfabric[parallel]
# Dependencies: testfabric.core only (stdlib otherwise)
#
# Provides:
#   - Worker dataclass (execution slot)
#   - WorkerPool (allocate, acquire, release workers)
#   - TargetNode & target file loading (Ansible-style YAML inventories)
#   - Target selection (all, group:*, host:*)
#
# Usage:
#   from testfabric.workers import Worker, WorkerPool
#   pool = WorkerPool(mode="local", capacity=4)
#   workers = pool.allocate(2)
#
#   from testfabric.workers.targets import load_targets_file, select_targets
#   targets = load_targets_file("targets.yaml")
#   selected = select_targets(targets, "group:web")

from testfabric.workers.models import Worker, WorkerMode, WorkerHealthState
from testfabric.workers.pool import WorkerPool

__all__ = ["Worker", "WorkerHealthState", "WorkerMode", "WorkerPool"]
