from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol


def utc_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def prune_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = prune_none(item)
            if cleaned is not None:
                out[key] = cleaned
        return out
    if isinstance(value, list):
        out_list: list[Any] = []
        for item in value:
            cleaned = prune_none(item)
            if cleaned is not None:
                out_list.append(cleaned)
        return out_list
    if isinstance(value, tuple):
        out_tuple: list[Any] = []
        for item in value:
            cleaned = prune_none(item)
            if cleaned is not None:
                out_tuple.append(cleaned)
        return tuple(out_tuple)
    return value


@dataclass(frozen=True)
class Event:
    component: str
    action: str
    status: str
    details: Dict[str, Any] | None = None


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...
    def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None: ...


class NullEvents:
    def emit(self, event: Event) -> None:
        return

    def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
        return


class FileEvents:
    """
    Writes:
      - structured events to <run_dir>/events.jsonl
      - streamed text to <run_dir>/<filename> (relative)
    Optionally echoes stream lines to stdout.

    Thread-safe: single lock for all writes (events + streams).
    """

    def __init__(self, run_dir: Path, *, echo: bool = True, run_id: str | None = None) -> None:
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.echo = bool(echo)
        self.run_id = (run_id or "").strip() or None

        self.events_path = self.run_dir / "events.jsonl"
        self._lock = threading.Lock()
        self._seq = 0

        self.run_dir.mkdir(parents=True, exist_ok=True)
        # touch file
        self.events_path.open("a", encoding="utf-8").close()

    def emit(self, event: Event) -> None:
        details = prune_none(dict(event.details or {}))
        target_id = details.get("target_id") or details.get("worker_id")
        stage_id = details.get("stage_id") or details.get("stage_slug")
        job_id = details.get("job_id")
        attempt = details.get("attempt")
        message = details.get("message") or details.get("error") or f"{event.component}:{event.action} {event.status}"
        ev = {
            "ts": utc_ts(),
            "seq": self._seq + 1,
            "run_id": self.run_id,
            "component": event.component,
            "action": event.action,
            "status": event.status,
            "message": message,
            "details": details,
        }
        if target_id is not None:
            ev["target_id"] = target_id
        if stage_id is not None:
            ev["stage_id"] = stage_id
        if job_id is not None:
            ev["job_id"] = job_id
        if attempt is not None:
            ev["attempt"] = attempt
        line = json.dumps(ev, ensure_ascii=False)

        with self._lock:
            self._seq += 1
            ev["seq"] = self._seq
            line = json.dumps(ev, ensure_ascii=False)
            self.events_path.open("a", encoding="utf-8").write(line + "\n")

        # keep emit() quiet by default (dispatcher already streams stdout/stderr)
        # If you want, you can add optional echo here later.

    def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
        """
        Append text to <run_dir>/<filename>.

        - filename is treated as a relative path under run_dir
        - creates parent dirs automatically
        - thread-safe
        """
        rel = (filename or "").lstrip("/").strip()
        if not rel:
            rel = "logs/stream.log"

        path = (self.run_dir / rel).resolve()
        # safety: prevent writing outside run_dir
        try:
            path.relative_to(self.run_dir)
        except Exception:
            # if someone passes "../../../etc/passwd" etc.
            path = self.run_dir / "logs" / "stream.log"

        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            path.open("a", encoding="utf-8").write(text)

        if not self.echo:
            return

        if echo_prefix:
            for ln in text.splitlines(True):
                print(f"{echo_prefix}{ln.rstrip()}", flush=True)
        else:
            for ln in text.splitlines(True):
                print(ln.rstrip(), flush=True)
