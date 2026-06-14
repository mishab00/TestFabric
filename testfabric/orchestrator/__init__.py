# testfabric.orchestrator — Full run orchestration
#
# Micro-package: testfabric[orchestration]
# Dependencies: testfabric[core], pydantic, pyyaml, typer, gitpython
#
# Provides:
#   - Orchestrator: end-to-end run execution (plan → health → stages → report)
#   - RunOptions: configuration for an orchestrated run
#   - StageCoordinator: per-stage lifecycle management
#   - StageBuilder & StageExecutor: build and execute stage pipelines
#   - LocalDispatcher: threaded parallel job dispatcher with retry
#   - Planner & compile_run_plan: job splitting, repeat modes, manifest-based splits
#   - LifecycleState: setup/teardown/conditional stage gating
#   - RunAggregator: final run-level summary builder
#
# Usage:
#   from testfabric.orchestrator import Orchestrator, RunOptions
#   from testfabric.spec import RunSpec
#
#   spec = RunSpec.load("run.yaml")
#   opts = RunOptions.from_spec_and_cli(spec, mode="run")
#   result = Orchestrator(spec, opts).run()

# NOTE: Orchestrator and RunOptions are imported lazily to avoid circular
# imports with testfabric.reporting (which depends on dispatch.models).
from testfabric.orchestrator.aggregate import RunAggregator
from testfabric.orchestrator.dispatch.threaded import LocalDispatcher
from testfabric.orchestrator.dispatch.models import DispatchResult, DispatchJobResult


def __getattr__(name: str):
    if name == "Orchestrator":
        from testfabric.orchestrator.orchestrator import Orchestrator

        return Orchestrator
    if name == "RunOptions":
        from testfabric.orchestrator.orchestrator import RunOptions

        return RunOptions
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DispatchJobResult",
    "DispatchResult",
    "LocalDispatcher",
    "Orchestrator",
    "RunAggregator",
    "RunOptions",
]
