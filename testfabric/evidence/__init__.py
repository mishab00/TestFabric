# testfabric.evidence — Structured evidence recording
#
# Micro-package: testfabric (no extra deps)
# Dependencies: testfabric.commands, testfabric.redaction, testfabric.runtime
#
# Provides:
#   - EvidenceRecorder: structured event/attachment recording into run directories
#   - EvidenceSink protocol: pluggable output (Allure, custom)
#   - AllureEvidenceSink: Allure attachment integration
#
# Usage:
#   from testfabric.evidence import EvidenceRecorder
#   from testfabric.runtime import RunContext, LibraryRunRequest
#
#   ctx = RunContext.from_request(LibraryRunRequest(project="my-proj"))
#   recorder = EvidenceRecorder(ctx)
#   with recorder.step("deploy"):
#       recorder.attach_json("config", {"env": "prod"})
#   recorder.write_summary({"verdict": "PASSED"})

from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol

from testfabric.commands import CommandResult, format_timestamp, utc_now
from testfabric.redaction import SecretRedactor
from testfabric.runtime import ArtifactLayout, RunContext


class EvidenceSink(Protocol):
    def on_event(self, event: dict[str, Any]) -> None: ...
    def on_attachment(self, name: str, path: Path, media_type: str | None = None) -> None: ...


class AllureEvidenceSink:
    def on_event(self, event: dict[str, Any]) -> None:
        return

    def on_attachment(self, name: str, path: Path, media_type: str | None = None) -> None:
        try:
            import allure  # type: ignore
        except Exception:
            return
        attachment_type = None
        if media_type == "application/json":
            attachment_type = getattr(allure.attachment_type, "JSON", None)
        elif media_type == "text/plain":
            attachment_type = getattr(allure.attachment_type, "TEXT", None)
        allure.attach.file(str(path), name=name, attachment_type=attachment_type)


class EvidenceRecorder:
    def __init__(
        self,
        context_or_layout: RunContext | ArtifactLayout,
        *,
        redactor: SecretRedactor | None = None,
        sinks: list[EvidenceSink] | None = None,
    ) -> None:
        if isinstance(context_or_layout, RunContext):
            self.context = context_or_layout
            self.layout = context_or_layout.layout
            self.redactor = redactor or context_or_layout.redactor
        else:
            self.context = None
            self.layout = context_or_layout
            self.redactor = redactor or SecretRedactor()
        self.layout.ensure()
        self.sinks = list(sinks or [])
        self._lock = threading.RLock()
        self._seq = 0
        self._step_stack: list[str] = []

    @contextmanager
    def step(self, name: str, **details: Any) -> Iterator[None]:
        step_name = str(name or "").strip() or "step"
        self._step_stack.append(step_name)
        self.emit("step", "start", {"step": step_name, **details})
        try:
            yield
        except Exception as exc:
            self.emit("step", "fail", {"step": step_name, "error": str(exc)})
            raise
        else:
            self.emit("step", "success", {"step": step_name})
        finally:
            self._step_stack.pop()

    def emit(self, kind: str, status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "ts": format_timestamp(utc_now()),
            "run_id": self.layout.run_id,
            "seq": 0,
            "kind": kind,
            "status": status,
            "step": self.current_step,
            "details": self.redactor.redact_data(details or {}),
        }
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            with self.layout.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        for sink in self.sinks:
            sink.on_event(event)
        return event

    @property
    def current_step(self) -> str | None:
        return self._step_stack[-1] if self._step_stack else None

    def attach_json(self, name: str, data: Any) -> Path:
        path = self._attachment_path(name, ".json")
        payload = self.redactor.redact_data(data)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return self._record_attachment(name, path, "application/json")

    def attach_text(self, name: str, text: Any) -> Path:
        path = self._attachment_path(name, ".txt")
        path.write_text(self.redactor.redact_text(text), encoding="utf-8")
        return self._record_attachment(name, path, "text/plain")

    def attach_file(self, name: str, path: str | Path) -> Path:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(str(source))
        suffix = source.suffix or ".bin"
        target = self._attachment_path(name, suffix)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return self._record_attachment(name, target, None)

    def record_command(self, name: str, result: CommandResult) -> Path:
        base = self._safe_name(name or "command")
        target_dir = self.layout.commands_dir / base
        target_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = target_dir / "result.json"
        stdout_path = target_dir / "stdout.txt"
        stderr_path = target_dir / "stderr.txt"
        metadata_path.write_text(
            json.dumps(self.redactor.redact_data(result.to_dict()), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        stdout_path.write_text(self.redactor.redact_text(result.stdout), encoding="utf-8")
        stderr_path.write_text(self.redactor.redact_text(result.stderr), encoding="utf-8")
        self.emit(
            "command",
            "success" if result.ok else "fail",
            {
                "name": name,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "host": result.host,
                "transport": result.transport,
                "result": str(metadata_path.relative_to(self.layout.run_dir)),
            },
        )
        return metadata_path

    def write_summary(self, summary: dict[str, Any]) -> Path:
        self.layout.summary_path.write_text(
            json.dumps(self.redactor.redact_data(summary), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.emit("summary", "written", {"path": str(self.layout.summary_path)})
        return self.layout.summary_path

    def _record_attachment(self, name: str, path: Path, media_type: str | None) -> Path:
        self.emit(
            "attachment",
            "written",
            {
                "name": name,
                "path": str(path.relative_to(self.layout.run_dir)),
                "media_type": media_type,
            },
        )
        for sink in self.sinks:
            sink.on_attachment(name, path, media_type)
        return path

    def _attachment_path(self, name: str, suffix: str) -> Path:
        step = self._safe_name(self.current_step or "run")
        base = self._safe_name(name)
        path = self.layout.checks_dir / step / f"{base}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._dedupe(path)

    def _dedupe(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for idx in range(2, 10_000):
            candidate = path.with_name(f"{stem}-{idx}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not create unique attachment path for {path}")

    @staticmethod
    def _safe_name(value: str) -> str:
        out = []
        last_dash = False
        for ch in str(value or "").strip().lower():
            if ch.isalnum() or ch in {"-", "_", "."}:
                out.append(ch)
                last_dash = False
            elif not last_dash:
                out.append("-")
                last_dash = True
        return "".join(out).strip("-") or "artifact"


__all__ = [
    "AllureEvidenceSink",
    "EvidenceRecorder",
    "EvidenceSink",
]
