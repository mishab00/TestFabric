# testfabric.reporting — Stage & run reporting
#
# Micro-package: testfabric[reporting]
# Dependencies: testfabric.core, testfabric.artifacts
#
# Provides:
#   - StageReporter: canonical stage-level reporting (summaries, artifacts, verdicts)
#   - RunAggregator: run-level summary aggregation across stages/targets
#
# Usage:
#   from testfabric.reporting import StageReporter
#   reporter = StageReporter()
#   reporter.ensure_dirs(ctx)
#   payload = reporter.write_stage_summary(ctx, items_total=10, jobs_total=2, dispatch=result)

from testfabric.orchestrator.aggregate import RunAggregator


def __getattr__(name: str):
    if name == "StageReporter":
        from testfabric.reporting.stage_reporter import StageReporter

        return StageReporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["RunAggregator", "StageReporter"]
