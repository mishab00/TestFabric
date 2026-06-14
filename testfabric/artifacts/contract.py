from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import posixpath


TESTFABRIC_ARTIFACTS_DIR = "TESTFABRIC_ARTIFACTS_DIR"
TESTFABRIC_JOB_ARTIFACTS_DIR = "TESTFABRIC_JOB_ARTIFACTS_DIR"
TESTFABRIC_RUN_ID = "TESTFABRIC_RUN_ID"
TESTFABRIC_STAGE_ID = "TESTFABRIC_STAGE_ID"
TESTFABRIC_JOB_ID = "TESTFABRIC_JOB_ID"
TESTFABRIC_ATTEMPT = "TESTFABRIC_ATTEMPT"


def _join_env_path(base: str, *parts: str) -> str:
    root = (base or "").strip() or "/artifacts"
    out = root
    for part in parts:
        token = str(part or "").strip().strip("/")
        if token:
            out = posixpath.join(out, token)
    return out


def host_stage_output_dir(artifacts_dir: str | Path) -> Path:
    return Path(artifacts_dir).expanduser().resolve() / "artifacts"


def host_output_dir(artifacts_dir: str | Path, job_id: str, attempt: int) -> Path:
    return host_stage_output_dir(artifacts_dir) / "jobs" / str(job_id) / f"attempt-{attempt}"


@dataclass(frozen=True)
class ArtifactContract:
    artifacts_dir: str
    job_artifacts_dir: str
    run_id: str
    stage_id: str
    job_id: str
    attempt: int

    def as_env(self) -> dict[str, str]:
        return {
            TESTFABRIC_ARTIFACTS_DIR: str(self.artifacts_dir),
            TESTFABRIC_JOB_ARTIFACTS_DIR: str(self.job_artifacts_dir),
            TESTFABRIC_RUN_ID: str(self.run_id),
            TESTFABRIC_STAGE_ID: str(self.stage_id),
            TESTFABRIC_JOB_ID: str(self.job_id),
            TESTFABRIC_ATTEMPT: str(self.attempt),
        }


def build_artifact_contract(
    *,
    artifacts_root: str,
    run_id: str,
    stage_id: str,
    job_id: str,
    attempt: int,
) -> ArtifactContract:
    return ArtifactContract(
        artifacts_dir=str(artifacts_root),
        job_artifacts_dir=_join_env_path(str(artifacts_root), "artifacts", "jobs", str(job_id), f"attempt-{attempt}"),
        run_id=str(run_id),
        stage_id=str(stage_id),
        job_id=str(job_id),
        attempt=int(attempt),
    )
