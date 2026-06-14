# testfabric.artifacts — Artifact collection, paths & contracts
#
# Micro-package: testfabric[core]
# Dependencies: testfabric.core only
#
# Provides:
#   - PathManager: deterministic run/stage/worker directory layout
#   - StageRef: unique stage identifier (index + title + suite)
#   - ArtifactCollector: collect stage artifacts (JUnit, HTML, logs, …)
#   - ArtifactContract: env-var contract for artifact paths inside executors
#   - ArtifactsLayout: run-level artifact directory layout
#
# Usage:
#   from testfabric.artifacts import PathManager, StageRef
#   paths = PathManager(spec, run_id="my-run-123")
#   paths.ensure_run_dirs()

from .collector import ArtifactCollector
from .contract import (
    TESTFABRIC_ARTIFACTS_DIR,
    TESTFABRIC_ATTEMPT,
    TESTFABRIC_JOB_ARTIFACTS_DIR,
    TESTFABRIC_JOB_ID,
    TESTFABRIC_RUN_ID,
    TESTFABRIC_STAGE_ID,
    ArtifactContract,
    build_artifact_contract,
    host_output_dir,
    host_stage_output_dir,
)
from .layout import ArtifactsLayout
from .paths import PathManager, StageRef

__all__ = [
    "ArtifactCollector",
    "ArtifactContract",
    "ArtifactsLayout",
    "PathManager",
    "StageRef",
    "TESTFABRIC_ARTIFACTS_DIR",
    "TESTFABRIC_ATTEMPT",
    "TESTFABRIC_JOB_ARTIFACTS_DIR",
    "TESTFABRIC_JOB_ID",
    "TESTFABRIC_RUN_ID",
    "TESTFABRIC_STAGE_ID",
    "build_artifact_contract",
    "host_output_dir",
    "host_stage_output_dir",
]
