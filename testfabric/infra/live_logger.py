from __future__ import annotations

from pathlib import Path
import json
import time
import threading
from typing import Optional, Any

from testfabric.core.events import Event, EventSink, prune_none
from testfabric.core.redaction import redact_data, redact_text

ConsoleVerbosity = str

def _utc_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LiveLogger(EventSink):
    """
    Event sink that:
      1) writes structured events to <run_dir>/events.jsonl
      2) prints human-friendly progress to stdout (echo=True)
      3) supports streaming text logs to files + optional echo

    Contract:
      - events.jsonl always exists once first event/stream is emitted
    """

    def __init__(
        self,
        run_dir: Path,
        echo: bool = True,
        *,
        console_verbosity: ConsoleVerbosity = "normal",
        _lock: threading.RLock | None = None,
        _seq_box: dict[str, int] | None = None,
        run_id: str | None = None,
        redaction_values: tuple[str, ...] = (),
    ):
        self.run_dir = Path(run_dir)
        self.echo = bool(echo)
        self.console_verbosity = str(console_verbosity or "normal").strip().lower() or "normal"
        self.run_id = (run_id or "").strip() or None
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.events_path = self.run_dir / "events.jsonl"
        self._lock = _lock or threading.RLock()
        self._seq_box = _seq_box or {"value": 0}
        self._redaction_values = tuple(redaction_values or ())
        self.events_path.open("a", encoding="utf-8").close()

    def child(self, subdir: Path) -> "LiveLogger":
        """
        Create a logger rooted at a subdirectory (keeps same echo behavior).
        Useful for stage/job scoped logs.
        """
        return LiveLogger(
            run_dir=subdir,
            echo=self.echo,
            console_verbosity=self.console_verbosity,
            _lock=self._lock,
            _seq_box=self._seq_box,
            run_id=self.run_id,
            redaction_values=self._redaction_values,
        )

    # -----------------------
    # EventSink interface
    # -----------------------

    def emit(self, event: Event) -> None:
        self.event(
            component=event.component,
            action=event.action,
            status=event.status,
            details=event.details or {},
        )

    def stream(self, filename: str, text: str, echo_prefix: Optional[str] = None) -> None:
        """
        Append text to run_dir/filename. Optionally echo line-by-line with prefix.
        """
        safe_text = redact_text(text, self._redaction_values)
        self.append_file(filename, safe_text)
        if not self.echo or self.console_verbosity != "verbose":
            return

        if echo_prefix:
            for ln in safe_text.splitlines(True):
                self.line(f"{echo_prefix}{ln.rstrip()}")
        else:
            for ln in safe_text.splitlines(True):
                self.line(ln.rstrip())

    # -----------------------
    # Existing API (kept)
    # -----------------------

    def event(self, component: str, action: str, status: str, details: Optional[dict] = None) -> None:
        clean_details = prune_none(redact_data(details or {}, self._redaction_values))
        target_id = clean_details.get("target_id") or clean_details.get("worker_id")
        stage_id = clean_details.get("stage_id") or clean_details.get("stage_slug")
        job_id = clean_details.get("job_id")
        attempt = clean_details.get("attempt")
        message = clean_details.get("message") or clean_details.get("error") or f"{component}:{action} {status}"
        ev: dict[str, Any] = {
            "ts": _utc_ts(),
            "run_id": self.run_id,
            "component": component,
            "action": action,
            "status": status,
            "message": message,
            "details": clean_details,
        }
        if target_id is not None:
            ev["target_id"] = target_id
        if stage_id is not None:
            ev["stage_id"] = stage_id
        if job_id is not None:
            ev["job_id"] = job_id
        if attempt is not None:
            ev["attempt"] = attempt
        with self._lock:
            self._seq_box["value"] += 1
            ev["seq"] = self._seq_box["value"]
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev) + "\n")

            if self.echo and self._should_echo_event(component=component, action=action, status=status):
                msg = f"[{ev['ts']}] {component}:{action} {status}"
                d = ev["details"] or {}
                for k in ("image", "repo_path", "sha", "count", "jobs", "error", "log", "path", "job_id", "attempt"):
                    if k in d:
                        msg += f" | {k}={d[k]}"
                print(msg, flush=True)

    def line(self, text: str) -> None:
        if self.echo and self.console_verbosity == "verbose":
            with self._lock:
                print(redact_text(text, self._redaction_values).rstrip("\n"), flush=True)

    def _should_echo_event(self, *, component: str, action: str, status: str) -> bool:
        verbosity = self.console_verbosity
        if verbosity == "quiet":
            return False
        if verbosity in {"normal", "verbose"}:
            return True
        if verbosity == "summary":
            if status in {"fail", "skip"}:
                return True
            return component in {"run", "health", "stage", "dispatcher", "docker"}
        return True

    def append_file(self, filename: str, text: str) -> None:
        rel = (filename or "").lstrip("/").strip()
        path = (self.run_dir / rel).resolve() if rel else (self.run_dir / "logs" / "stream.log").resolve()
        try:
            path.relative_to(self.run_dir.resolve())
        except Exception:
            path = (self.run_dir / "logs" / "stream.log").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_text = redact_text(text, self._redaction_values)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(safe_text)

    # Convenience helpers (optional)
    def step_start(self, name: str, details: Optional[dict] = None) -> None:
        self.event("pipeline", name, "start", details)

    def step_ok(self, name: str, details: Optional[dict] = None) -> None:
        self.event("pipeline", name, "success", details)

    def step_fail(self, name: str, error: str, details: Optional[dict] = None) -> None:
        d = dict(details or {})
        d["error"] = error
        self.event("pipeline", name, "fail", d)
