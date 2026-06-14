from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from testfabric.evidence import EvidenceRecorder
from testfabric.runtime import ArtifactLayout, RunContext


def make_run_id(prefix: str = "pytest") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def make_artifact_layout(
    run_id: str | None = None,
    *,
    artifact_dir: str | Path | None = None,
    env_prefix: str = "TESTFABRIC",
) -> ArtifactLayout:
    return ArtifactLayout.for_run(
        run_id or make_run_id(),
        artifact_dir=artifact_dir,
        env_prefix=env_prefix,
    ).ensure()


def make_evidence(
    *,
    run_id: str | None = None,
    artifact_dir: str | Path | None = None,
    env_prefix: str = "TESTFABRIC",
    project: str | None = None,
    suite: str | None = None,
    redaction_values: tuple[str, ...] = (),
    options: dict[str, Any] | None = None,
) -> EvidenceRecorder:
    layout = make_artifact_layout(run_id, artifact_dir=artifact_dir, env_prefix=env_prefix)
    context = RunContext(
        run_id=layout.run_id,
        layout=layout,
        project=project,
        suite=suite,
        options=dict(options or {}),
    )
    if redaction_values:
        context = RunContext(
            run_id=context.run_id,
            layout=context.layout,
            project=context.project,
            suite=context.suite,
            options=context.options,
            redactor=context.redactor.with_values(redaction_values),
        )
    return EvidenceRecorder(context)


def pytest_failure_summary(report: Any) -> dict[str, Any]:
    return {
        "nodeid": getattr(report, "nodeid", None),
        "outcome": getattr(report, "outcome", None),
        "duration": getattr(report, "duration", None),
        "longrepr": str(getattr(report, "longrepr", "") or ""),
    }


__all__ = [
    "make_artifact_layout",
    "make_evidence",
    "make_run_id",
    "pytest_failure_summary",
]
