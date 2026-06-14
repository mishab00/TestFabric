# testfabric/artifacts/paths.py
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from testfabric.core.constants import (
    DEFAULT_ARTIFACTS_DIR_NAME,
    DEFAULT_RUNS_SUBDIR_NAME,
    DEFAULT_WORKER_ID,
    DEFAULT_WORKERS_TMP_SUBDIR_NAME,
)


def _slugify(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    out = []
    last_dash = False
    for ch in s:
        ok = ch.isalnum() or ch in ("-", "_")
        if ok:
            out.append(ch.lower())
            last_dash = False
        else:
            if not last_dash:
                out.append("-")
                last_dash = True
    slug = "".join(out).strip("-")
    return slug or ""


@dataclass(frozen=True)
class StageRef:
    index: int
    title: str
    suite: str

    @property
    def slug(self) -> str:
        base = _slugify(self.title) or _slugify(self.suite) or "stage"
        return f"{self.index:02d}-{base}"


class PathManager:
    """
    Canonical artifacts contract for runs/stages/workers.

    - run_dir:   <artifacts_dir>/<runs_subdir>/<run_id>/
    - tmp worker: <workers_tmp>/<run_id>/<worker_id>/
    - mirrored worker: <run_dir>/workers/<worker_id>/
    """

    def __init__(self, spec: Any, run_id: str):
        self.spec = spec
        self.run_id = (run_id or "").strip()
        if not self.run_id:
            raise ValueError("run_id is required for PathManager")

        run = getattr(spec, "run", None)
        if run is None:
            raise ValueError("spec.run is required")

        # ✅ schema-aligned fields
        artifacts_dir = (getattr(run, "artifacts_dir", None) or DEFAULT_ARTIFACTS_DIR_NAME).strip()
        runs_subdir = (getattr(run, "runs_subdir", None) or DEFAULT_RUNS_SUBDIR_NAME).strip() or DEFAULT_RUNS_SUBDIR_NAME
        workers_tmp = getattr(run, "workers_tmp", None)

        self._artifacts_root = Path(artifacts_dir).expanduser().resolve()
        self._runs_subdir = runs_subdir
        if workers_tmp is None:
            self._workers_tmp = (self._artifacts_root / DEFAULT_WORKERS_TMP_SUBDIR_NAME).resolve()
        else:
            self._workers_tmp = Path(str(workers_tmp).strip()).expanduser().resolve()

        self._run_dir = (self._artifacts_root / self._runs_subdir / self.run_id).resolve()

    # ------------------
    # Run-level artifacts
    # ------------------

    @property
    def artifacts_root(self) -> Path:
        return self._artifacts_root

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def runs_root(self) -> Path:
        return (self._artifacts_root / self._runs_subdir).resolve()

    @property
    def run_logs_dir(self) -> Path:
        return self.run_dir / "logs"

    @property
    def run_workers_dir(self) -> Path:
        return self.run_dir / "workers"

    @property
    def run_summary_path(self) -> Path:
        return self.run_dir / "summary.json"

    # ------------------
    # Worker staging (tmp)
    # ------------------

    @property
    def workers_tmp_root(self) -> Path:
        return self._workers_tmp

    def worker_tmp_dir(self, worker_id: str) -> Path:
        wid = (worker_id or "").strip() or DEFAULT_WORKER_ID
        return (self.workers_tmp_root / self.run_id / wid).resolve()

    def worker_stage_dir(self, worker_id: str, stage: StageRef) -> Path:
        return self.worker_tmp_dir(worker_id) / "stages" / stage.slug

    def worker_stage_reports_dir(self, worker_id: str, stage: StageRef) -> Path:
        return self.worker_stage_dir(worker_id, stage) / "reports"

    def worker_stage_output_dir(self, worker_id: str, stage: StageRef) -> Path:
        return self.worker_stage_reports_dir(worker_id, stage) / "artifacts"

    def worker_stage_job_attempt_output_dir(self, worker_id: str, stage: StageRef, job_id: str, attempt: int) -> Path:
        jid = (job_id or "").strip() or "job"
        return self.worker_stage_output_dir(worker_id, stage) / "jobs" / jid / f"attempt-{int(attempt)}"

    def worker_stage_logs_dir(self, worker_id: str, stage: StageRef) -> Path:
        return self.worker_stage_dir(worker_id, stage) / "logs"

    def worker_stage_jobs_dir(self, worker_id: str, stage: StageRef) -> Path:
        return self.worker_stage_dir(worker_id, stage) / "jobs"

    # ------------------
    # Mirrored worker under run_dir
    # ------------------

    def mirror_worker_dir(self, worker_id: str) -> Path:
        wid = (worker_id or "").strip() or DEFAULT_WORKER_ID
        return self.run_workers_dir / wid

    # ------------------
    # Creation helpers
    # ------------------

    def ensure_run_dirs(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_workers_dir.mkdir(parents=True, exist_ok=True)

    def ensure_worker_tmp_dirs(self, worker_id: str) -> None:
        self.worker_tmp_dir(worker_id).mkdir(parents=True, exist_ok=True)

    def ensure_worker_stage_dirs(self, worker_id: str, stage: StageRef) -> None:
        self.worker_stage_logs_dir(worker_id, stage).mkdir(parents=True, exist_ok=True)
        self.worker_stage_jobs_dir(worker_id, stage).mkdir(parents=True, exist_ok=True)
        self.worker_stage_reports_dir(worker_id, stage).mkdir(parents=True, exist_ok=True)
        self.worker_stage_output_dir(worker_id, stage).mkdir(parents=True, exist_ok=True)
