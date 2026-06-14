# testfabric/run/execution_context.py
from __future__ import annotations
from dataclasses import dataclass
from testfabric.artifacts import PathManager, StageRef
from testfabric.spec.schema import SuiteSection, RunSpec
from testfabric.orchestrator.plan.models import StagePlan


@dataclass(frozen=True)
class ExecutionContext:
    """
    Single bundle passed through the stage execution pipeline.

    Design goals:
      - no hidden globals / no spec mutation
      - everything needed by adapters (runner/executor/planner/reporting) is here
      - stable, testable interface

    NOTE:
      This is "controller-side context". Later we can introduce WorkerContext
      for remote backends.
    """
    stage: StagePlan
    spec: RunSpec
    paths: PathManager

    stage_ref: StageRef

    suite: SuiteSection

    run_id: str
    worker_id: str

    # resolved workspace
    repo_path: str
    commit_sha: str

    # resolved effective parallelism (no None)
    max_workers: int
    chunk_size: int
    max_retries: int

    # -----------------------
    # Convenience accessors
    # -----------------------

    @property
    def run_dir(self) -> str:
        return str(self.paths.run_dir)

    @property
    def worker_tmp_dir(self) -> str:
        return str(self.paths.worker_tmp_dir(self.worker_id))

    @property
    def worker_mirror_dir(self) -> str:
        return str(self.paths.mirror_worker_dir(self.worker_id))

    @property
    def stage_tmp_dir(self) -> str:
        return str(self.paths.worker_stage_dir(self.worker_id, self.stage_ref))

    @property
    def stage_logs_dir(self) -> str:
        return str(self.paths.worker_stage_logs_dir(self.worker_id, self.stage_ref))

    @property
    def stage_jobs_dir(self) -> str:
        return str(self.paths.worker_stage_jobs_dir(self.worker_id, self.stage_ref))

    @property
    def stage_reports_dir(self) -> str:
        # where test outputs (junit/html) should be written before finalize
        return str(self.paths.worker_stage_reports_dir(self.worker_id, self.stage_ref))

    @property
    def repo_root_in_env(self) -> str:
        """
        Path inside the execution environment that represents the repo root.
        - docker: repo mounted at spec.docker.repo_mount
        - local: executor resolves '.' relative to ctx.repo_path
        """
        if (self.stage.executor or "").strip().lower() == "docker":
            docker = getattr(self.spec, "docker", None)
            return (getattr(docker, "repo_mount", None) or "/work").strip()
        return "."
