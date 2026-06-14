from __future__ import annotations

import os
import uuid
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testfabric.redaction import SecretRedactor


@dataclass(frozen=True)
class ArtifactLayout:
    run_id: str
    artifact_dir: Path = Path("artifacts")
    runs_subdir: str = "runs"
    env_prefix: str = "TESTFABRIC"

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        artifact_dir: str | Path | None = None,
        env_prefix: str = "TESTFABRIC",
        runs_subdir: str = "runs",
    ) -> "ArtifactLayout":
        prefix = _normalize_env_prefix(env_prefix)
        configured_dir = os.environ.get(f"{prefix}_ARTIFACT_DIR")
        configured_runs_subdir = os.environ.get(f"{prefix}_RUNS_SUBDIR")
        return cls(
            run_id=(run_id or "").strip(),
            artifact_dir=Path(artifact_dir or configured_dir or "artifacts").expanduser().resolve(),
            runs_subdir=(configured_runs_subdir or runs_subdir or "runs").strip() or "runs",
            env_prefix=prefix,
        )

    @property
    def runs_root(self) -> Path:
        return (self.artifact_dir / self.runs_subdir).resolve()

    @property
    def run_dir(self) -> Path:
        if not self.run_id:
            raise ValueError("run_id is required")
        return (self.runs_root / self.run_id).resolve()

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.run_dir / "summary.json"

    @property
    def run_request_path(self) -> Path:
        return self.run_dir / "run_request.json"

    @property
    def runtime_config_path(self) -> Path:
        return self.run_dir / "runtime_config.json"

    @property
    def commands_dir(self) -> Path:
        return self.run_dir / "commands"

    @property
    def checks_dir(self) -> Path:
        return self.run_dir / "checks"

    @property
    def logs_dir(self) -> Path:
        return self.run_dir / "logs"

    @property
    def traffic_dir(self) -> Path:
        return self.run_dir / "traffic"

    @property
    def allure_results_dir(self) -> Path:
        return self.run_dir / "allure-results"

    def ensure(self) -> "ArtifactLayout":
        for path in (
            self.run_dir,
            self.commands_dir,
            self.checks_dir,
            self.logs_dir,
            self.traffic_dir,
            self.allure_results_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        return self


@dataclass(frozen=True)
class LibraryRunRequest:
    run_id: str | None = None
    project: str | None = None
    suite: str | None = None
    artifact_dir: str | Path | None = None
    options: dict[str, Any] = field(default_factory=dict)
    redaction_values: tuple[str, ...] = field(default_factory=tuple)

    def resolved_run_id(self) -> str:
        return (self.run_id or "").strip() or f"run-{uuid.uuid4().hex[:12]}"


LocalRunRequest = LibraryRunRequest


@dataclass(frozen=True)
class RunContext:
    run_id: str
    layout: ArtifactLayout
    project: str | None = None
    suite: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    redactor: SecretRedactor = field(default_factory=SecretRedactor)

    @classmethod
    def from_request(
        cls,
        request: LibraryRunRequest,
        *,
        env_prefix: str = "TESTFABRIC",
        redactor: SecretRedactor | None = None,
    ) -> "RunContext":
        run_id = request.resolved_run_id()
        layout = ArtifactLayout.for_run(
            run_id,
            artifact_dir=request.artifact_dir,
            env_prefix=env_prefix,
        ).ensure()
        resolved_redactor = redactor or SecretRedactor.from_values(request.redaction_values)
        context = cls(
            run_id=run_id,
            layout=layout,
            project=request.project,
            suite=request.suite,
            options=dict(request.options or {}),
            redactor=resolved_redactor,
        )
        context.write_runtime_files(request)
        return context

    @property
    def run_dir(self) -> Path:
        return self.layout.run_dir

    def redact(self, text: Any) -> str:
        return self.redactor.redact_text(text)

    def redact_data(self, data: Any) -> Any:
        return self.redactor.redact_data(data)

    def write_runtime_files(self, request: LibraryRunRequest | None = None) -> None:
        if request is not None:
            request_payload = {
                "run_id": self.run_id,
                "project": request.project,
                "suite": request.suite,
                "artifact_dir": str(request.artifact_dir) if request.artifact_dir is not None else None,
                "options": dict(request.options or {}),
                "redaction_values": list(request.redaction_values or ()),
            }
            self.layout.run_request_path.write_text(
                json.dumps(self.redact_data(request_payload), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        runtime_payload = {
            "run_id": self.run_id,
            "project": self.project,
            "suite": self.suite,
            "artifact_dir": str(self.layout.artifact_dir),
            "run_dir": str(self.layout.run_dir),
            "env_prefix": self.layout.env_prefix,
            "options": dict(self.options or {}),
        }
        self.layout.runtime_config_path.write_text(
            json.dumps(self.redact_data(runtime_payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _normalize_env_prefix(value: str) -> str:
    prefix = (value or "TESTFABRIC").strip().upper().rstrip("_")
    return prefix or "TESTFABRIC"


__all__ = [
    "ArtifactLayout",
    "LibraryRunRequest",
    "LocalRunRequest",
    "RunContext",
]
