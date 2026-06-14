# testfabric/plan/planner.py
from dataclasses import dataclass

from testfabric.execution.suites.base import JobPlan, TestItem
from testfabric.orchestrator.plan.split import SplitSpec
from testfabric.orchestrator.plan.strategies import split_items


@dataclass(frozen=True)
class PlanResult:
    jobs: list[JobPlan]
    items_total: int
    chunk_size: int


class Planner:
    """
    Turns collected TestItems into JobPlans.

    MVP:
      - fixed chunking by chunk_size
      - deterministic job ids: job-0001, job-0002, ...
      - simple stage splitting by count

    Future:
      - smarter chunking (balance by duration history)
      - grouping strategies (by file, marker, suite sections)
    """

    def plan(
        self,
        items: list[TestItem],
        *,
        chunk_size: int,
        execution_mode: str = "once",
        execution_count: int = 1,
        split_count: int = 1,
        split_manifest_path: str | None = None,
        split_manifest_groups: list[list[str]] | None = None,
        item_weights: dict[str, float] | None = None,
    ) -> PlanResult:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if execution_count <= 0:
            raise ValueError(f"execution_count must be >= 1, got {execution_count}")
        if split_count <= 0:
            raise ValueError(f"split_count must be >= 1, got {split_count}")

        jobs: list[JobPlan] = []
        total = len(items)
        split = SplitSpec(count=int(split_count), manifest_path=split_manifest_path)

        if execution_mode != "once":
            if execution_mode != "repeat":
                raise ValueError(f"Unsupported execution_mode: {execution_mode}")

        if split.enabled:
            groups = split_items(
                items,
                split=split,
                manifest_groups=split_manifest_groups,
                item_weights=item_weights,
            )
            repeats = int(execution_count) if execution_mode == "repeat" else 1
            for _ in range(repeats):
                for group in groups:
                    if not group:
                        continue
                    job_id = f"job-{len(jobs)+1:04d}"
                    jobs.append(JobPlan(job_id=job_id, items=list(group)))
            effective_chunk = max((len(job.items) for job in jobs), default=chunk_size)
            return PlanResult(jobs=jobs, items_total=total * repeats, chunk_size=effective_chunk)

        if execution_mode == "repeat":
            for _ in range(int(execution_count)):
                for item in items:
                    job_id = f"job-{len(jobs)+1:04d}"
                    jobs.append(JobPlan(job_id=job_id, items=[item]))
            return PlanResult(jobs=jobs, items_total=total * int(execution_count), chunk_size=1)

        for i in range(0, total, chunk_size):
            job_id = f"job-{len(jobs)+1:04d}"
            jobs.append(JobPlan(job_id=job_id, items=items[i : i + chunk_size]))

        return PlanResult(jobs=jobs, items_total=total, chunk_size=chunk_size)
