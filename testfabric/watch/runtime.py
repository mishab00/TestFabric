from __future__ import annotations

import glob
import hashlib
import os
import shlex
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request

from testfabric.core.events import Event, EventSink


_BUILTIN_ACTIONS = {"fail", "success", "report", "notify", "send", "script", "stop", "continue"}
_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")


class WatchAbort(RuntimeError):
    pass


@dataclass(frozen=True)
class _Action:
    name: str
    type: str
    value: str | None = None
    message: str | None = None
    command: list[str] = field(default_factory=list)
    level: str = "info"
    stop_all: bool = False
    once: bool = False


@dataclass(frozen=True)
class _Source:
    name: str
    type: str
    phases: tuple[str, ...] = ("run",)
    path: str | None = None
    url: str | None = None
    cmd: tuple[str, ...] = ()
    bash: str | None = None
    shell: str | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = None
    key_path: str | None = None
    known_hosts: str | None = None
    via: str | None = None
    stream: str = "both"
    mode: str = "tail"
    interval_seconds: int = 1
    target: str | None = None


@dataclass(frozen=True)
class _Watcher:
    name: str
    source: str | None
    sources: tuple[str, ...]
    phases: tuple[str, ...]
    match: str | None
    regex: str | None
    body_match: str | None
    sequence: tuple[str, ...]
    status: tuple[int, ...]
    gt: float | None
    gte: float | None
    lt: float | None
    lte: float | None
    once: bool
    dedupe: bool
    missing: str
    action: str | _Action


@dataclass
class _SequenceState:
    sequence: tuple[str, ...]
    index: int = 0
    buffer: str = ""
    cursor: int = 0
    matched: list[str] = field(default_factory=list)
    complete: bool = False
    emitted: bool = False


def _as_dict(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))  # type: ignore[no-any-return]
    if isinstance(value, dict):
        return dict(value)
    return {}


def _merge_sections(watch: dict[str, Any] | None) -> dict[str, Any]:
    root = dict(watch or {})
    if any(key in root for key in ("sources", "watchers", "actions")):
        return root
    merged: dict[str, Any] = {}
    for key in ("run", "stage"):
        section = root.get(key)
        if isinstance(section, dict):
            for sub_key in ("phases", "sources", "actions", "watchers"):
                current = merged.setdefault(sub_key, [])
                if isinstance(section.get(sub_key), list):
                    current.extend(section.get(sub_key) or [])
    return merged


def _normalize_action(raw: dict[str, Any] | str) -> _Action:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            raise ValueError("watch action reference cannot be empty")
        if name not in _BUILTIN_ACTIONS:
            return _Action(name=name, type=name)
        return _Action(name=name, type="report" if name == "notify" else name)
    name = str(raw.get("name") or "").strip()
    if not name:
        name = str(raw.get("type") or "report").strip() or "report"
    action_type = str(raw.get("type") or "report").strip() or "report"
    if action_type == "notify":
        action_type = "report"
    command = [str(x) for x in (raw.get("command") or []) if str(x).strip()]
    return _Action(
        name=name,
        type=action_type,
        value=(str(raw.get("value") or "").strip() or None),
        message=(str(raw.get("message") or "").strip() or None),
        command=command,
        level=str(raw.get("level") or "info").strip() or "info",
        stop_all=bool(raw.get("stop_all", False)),
        once=bool(raw.get("once", False)),
    )


def _normalize_then(raw: object) -> _Action:
    payload = _as_dict(raw)
    if not payload:
        return _Action(name="report", type="report")

    report = payload.get("report")
    if report is not None:
        report_payload = _as_dict(report)
        message = report_payload.get("message")
        if message is None and not report_payload and not isinstance(report, dict):
            message = report
        return _Action(
            name=str(payload.get("name") or "report").strip() or "report",
            type="report",
            message=(str(message or "").strip() or None),
            level=str(report_payload.get("level") or payload.get("level") or "info").strip() or "info",
        )

    shell = payload.get("shell")
    if shell is not None:
        text = str(shell).strip()
        return _Action(
            name=str(payload.get("name") or "shell").strip() or "shell",
            type="script",
            command=["sh", "-lc", text],
            message=(str(payload.get("message") or "").strip() or None),
        )

    bash = payload.get("bash")
    if bash is not None:
        text = str(bash).strip()
        return _Action(
            name=str(payload.get("name") or "bash").strip() or "bash",
            type="script",
            command=["bash", "-lc", text],
            message=(str(payload.get("message") or "").strip() or None),
        )

    command = payload.get("command")
    if command is not None:
        items = [str(x) for x in (command if isinstance(command, list) else [command]) if str(x).strip()]
        return _Action(
            name=str(payload.get("name") or "command").strip() or "command",
            type="script",
            command=items,
            message=(str(payload.get("message") or "").strip() or None),
        )

    send = payload.get("send")
    if send is not None:
        return _Action(
            name=str(payload.get("name") or "send").strip() or "send",
            type="send",
            value=(str(send).strip() or None),
            message=(str(payload.get("message") or "").strip() or None),
        )

    fail = payload.get("fail")
    if fail is not None:
        return _Action(
            name=str(payload.get("name") or "fail").strip() or "fail",
            type="fail",
            stop_all=True,
            message=(str(payload.get("message") or "").strip() or None),
        )

    success = payload.get("success")
    if success is not None:
        return _Action(
            name=str(payload.get("name") or "success").strip() or "success",
            type="success",
            message=(str(payload.get("message") or "").strip() or None),
        )

    name = str(payload.get("name") or payload.get("type") or "report").strip() or "report"
    action_type = str(payload.get("type") or "report").strip() or "report"
    return _Action(name=name, type=action_type)


def _normalize_source(raw: dict[str, Any]) -> _Source:
    cmd = tuple(str(x) for x in (raw.get("cmd") or []) if str(x).strip())
    return _Source(
        name=str(raw.get("name") or "").strip(),
        type=str(raw.get("type") or "").strip(),
        phases=tuple(str(x).strip() for x in (raw.get("phases") or ["run"]) if str(x).strip()) or ("run",),
        path=(str(raw.get("path") or "").strip() or None),
        url=(str(raw.get("url") or "").strip() or None),
        cmd=cmd,
        bash=(str(raw.get("bash") or "").strip() or None),
        shell=(str(raw.get("shell") or "").strip() or None),
        host=(str(raw.get("host") or "").strip() or None),
        user=(str(raw.get("user") or "").strip() or None),
        port=(int(raw.get("port")) if raw.get("port") not in (None, "", False) else None),
        key_path=(str(raw.get("key_path") or "").strip() or None),
        known_hosts=(str(raw.get("known_hosts") or "").strip() or None),
        via=(str(raw.get("via") or "").strip() or None),
        stream=str(raw.get("stream") or "both").strip() or "both",
        mode=str(raw.get("mode") or "tail").strip() or "tail",
        interval_seconds=int(raw.get("interval_seconds") or 1),
        target=(str(raw.get("target") or "").strip() or None),
    )


def _normalize_watcher(raw: dict[str, Any]) -> _Watcher:
    when = _as_dict(raw.get("when"))
    then = raw.get("then")
    action = raw.get("action")
    if then is not None:
        normalized_action: str | _Action = _normalize_then(then)
    elif isinstance(action, dict):
        normalized_action = _normalize_action(action)
    else:
        normalized_action = str(action or "").strip()
    status_values = when.get("status") if when else raw.get("status")
    if isinstance(status_values, list):
        status = tuple(int(x) for x in status_values if str(x).strip())
    elif status_values in (None, "", False):
        status = ()
    else:
        status = (int(status_values),)
    match = when.get("match") if when else raw.get("match")
    regex = when.get("regex") if when else raw.get("regex")
    body_match = when.get("body_match") if when else raw.get("body_match")
    sequence_raw = when.get("sequence") if when else raw.get("sequence")
    sequence: list[str] = []
    if isinstance(sequence_raw, list):
        sequence = [str(item).strip() for item in sequence_raw if str(item).strip()]
    elif sequence_raw not in (None, "", False):
        text = str(sequence_raw).strip()
        if text:
            sequence = [text]
    gt = when.get("gt") if when else raw.get("gt")
    gte = when.get("gte") if when else raw.get("gte")
    lt = when.get("lt") if when else raw.get("lt")
    lte = when.get("lte") if when else raw.get("lte")
    once = bool(raw.get("once", False) or (when.get("once", False) if when else False))
    dedupe_raw = raw.get("dedupe", True)
    dedupe = bool(dedupe_raw if dedupe_raw is not None else True)
    missing = str(raw.get("missing") or "ignore").strip().lower() or "ignore"
    return _Watcher(
        name=str(raw.get("name") or "").strip(),
        source=(str(raw.get("source") or "").strip() or None),
        sources=tuple(str(x).strip() for x in (raw.get("sources") or []) if str(x).strip()),
        phases=tuple(str(x).strip() for x in (raw.get("phases") or ["run"]) if str(x).strip()) or ("run",),
        match=(str(match or "").strip() or None),
        regex=(str(regex or "").strip() or None),
        body_match=(str(body_match or "").strip() or None),
        sequence=tuple(sequence),
        status=status,
        gt=(float(gt) if gt not in (None, "", False) else None),
        gte=(float(gte) if gte not in (None, "", False) else None),
        lt=(float(lt) if lt not in (None, "", False) else None),
        lte=(float(lte) if lte not in (None, "", False) else None),
        once=once,
        dedupe=dedupe,
        missing=missing,
        action=normalized_action,
    )


class WatchRuntime:
    def __init__(
        self,
        watch: dict[str, Any] | None,
        *,
        events: EventSink,
        run_scope: dict[str, Any] | None = None,
        base_dir: str | Path | None = None,
        redact_text: Callable[[str], str] | None = None,
        send_hook: Callable[[str, dict[str, Any]], None] | None = None,
        script_hook: Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.events = events
        self.base_dir = Path(base_dir).expanduser().resolve() if base_dir else None
        self.redact_text = redact_text or (lambda text: text)
        self.send_hook = send_hook
        self.script_hook = script_hook
        self.run_scope = dict(run_scope or {})

        merged = _merge_sections(watch)
        self.phases: tuple[str, ...] = tuple(str(x).strip() for x in (merged.get("phases") or ["run"]) if str(x).strip()) or ("run",)
        self.sources: list[_Source] = [
            _normalize_source(src)
            for src in (merged.get("sources") or [])
            if isinstance(src, dict) and str(src.get("name") or "").strip() and str(src.get("type") or "").strip()
        ]
        self.actions: dict[str, _Action] = {}
        for action in (merged.get("actions") or []):
            if not isinstance(action, dict):
                continue
            normalized = _normalize_action(action)
            self.actions[normalized.name] = normalized
        self.watchers: list[_Watcher] = []
        for watcher in (merged.get("watchers") or []):
            if not isinstance(watcher, dict):
                continue
            normalized = _normalize_watcher(watcher)
            if not normalized.name:
                continue
            self.watchers.append(normalized)

        self._watchers_by_source: dict[str, list[_Watcher]] = {}
        for watcher in self.watchers:
            refs = [watcher.source] if watcher.source else []
            refs.extend(list(watcher.sources or []))
            for ref in refs:
                self._watchers_by_source.setdefault(ref, []).append(watcher)

        self._source_by_name = {source.name: source for source in self.sources}
        self._action_by_name = {name: action for name, action in self.actions.items()}
        self._seen_once: set[str] = set()
        self._seen_dedupe: set[tuple[str, str]] = set()
        self._sequence_states: dict[tuple[str, str], _SequenceState] = {}
        self._offsets: dict[str, int] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._abort_requested = False
        self._failure_reason: str | None = None
        self._reports: list[dict[str, Any]] = []
        self._sends: list[str] = []
        self._successes: list[str] = []

    @property
    def abort_requested(self) -> bool:
        return self._abort_requested

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def reports(self) -> list[dict[str, Any]]:
        return list(self._reports)

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return self.reports

    @property
    def sends(self) -> list[str]:
        return list(self._sends)

    @property
    def successes(self) -> list[str]:
        return list(self._successes)

    def start(self, *, phase: str = "stage") -> None:
        if self._threads:
            return
        polling_sources = [
            source
            for source in self.sources
            if (
                (source.type in {"file", "remote_file"} and source.path)
                or (source.type == "http" and source.url)
                or (source.type == "metric" and (source.cmd or source.bash or source.shell))
            )
        ]
        for source in polling_sources:
            thread = threading.Thread(target=self._poll_loop, args=(source, phase), daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)

    def feed_output(
        self,
        text: str,
        *,
        stream: str = "stdout",
        phase: str = "job",
        source_name: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        safe_text = self.redact_text(text or "")
        if not safe_text:
            return not self._abort_requested
        ctx = self._merged_context(phase=phase, context=context)
        self._process_sources(
            safe_text,
            phase=phase,
            stream=stream,
            source_filter=source_name,
            ctx=ctx,
        )
        return not self._abort_requested

    def poll_once(self, *, phase: str = "stage") -> None:
        ctx = self._merged_context(phase=phase, context=None)
        for source in self.sources:
            if source.type == "file" and source.path:
                self._poll_source_once(source, phase=phase, ctx=ctx)
            elif source.type == "remote_file" and source.path:
                self._poll_remote_file_once(source, phase=phase, ctx=ctx)
            elif source.type == "http" and source.url:
                self._poll_http_once(source, phase=phase, ctx=ctx)
            elif source.type == "metric" and (source.cmd or source.bash or source.shell):
                self._poll_metric_once(source, phase=phase, ctx=ctx)
            else:
                continue

    def _poll_loop(self, source: _Source, phase: str) -> None:
        interval = max(1, int(source.interval_seconds or 1))
        while not self._stop.is_set():
            try:
                ctx = self._merged_context(phase=phase, context=None)
                if source.type == "file":
                    self._poll_source_once(source, phase=phase, ctx=ctx)
                elif source.type == "remote_file":
                    self._poll_remote_file_once(source, phase=phase, ctx=ctx)
                elif source.type == "http":
                    self._poll_http_once(source, phase=phase, ctx=ctx)
                elif source.type == "metric":
                    self._poll_metric_once(source, phase=phase, ctx=ctx)
            except Exception:
                pass
            self._stop.wait(timeout=float(interval))

    def _poll_source_once(self, source: _Source, *, phase: str, ctx: dict[str, Any]) -> None:
        if not source.path:
            return
        for path in self._resolve_paths(source.path):
            if self._stop.is_set():
                return
            candidate = Path(path)
            if not candidate.exists() or not candidate.is_file():
                continue
            key = str(candidate.resolve())
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            last_offset = self._offsets.get(key, 0)
            if source.mode == "snapshot":
                last_offset = 0
            if size < last_offset:
                last_offset = 0
            try:
                with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(last_offset)
                    chunk = fh.read()
            except OSError:
                continue
            if chunk:
                self._offsets[key] = size
                self._process_sources(
                    self.redact_text(chunk),
                    phase=phase,
                    stream="file",
                    source_filter=source.name,
                    ctx={**ctx, "path": str(candidate)},
                )

    def _poll_remote_file_once(self, source: _Source, *, phase: str, ctx: dict[str, Any]) -> None:
        if not source.path or not source.host:
            return

        remote_ctx = {**ctx, "host": source.host, "remote_path": source.path}
        size = self._remote_file_size(source)
        if size is None:
            return
        key = self._remote_file_key(source)
        last_offset = self._offsets.get(key, 0)
        if source.mode == "snapshot":
            last_offset = 0
        if size < last_offset:
            last_offset = 0
        chunk = self._remote_file_read(source, start_offset=last_offset)
        if not chunk:
            return
        self._offsets[key] = size
        self._process_sources(
            self.redact_text(chunk),
            phase=phase,
            stream="file",
            source_filter=source.name,
            ctx=remote_ctx,
        )

    def _remote_file_key(self, source: _Source) -> str:
        host = (source.host or "").strip()
        path = (source.path or "").strip()
        return f"remote_file:{host}:{path}"

    def _ssh_args(self, source: _Source) -> list[str]:
        host = (source.host or "").strip()
        if not host:
            return []
        user = (source.user or "root").strip() or "root"
        port = int(source.port or 22)
        args = [
            "ssh",
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if source.key_path:
            args.extend(["-i", str(Path(source.key_path).expanduser())])
            args.extend(["-o", "IdentitiesOnly=yes"])
        if source.known_hosts:
            args.extend(["-o", f"UserKnownHostsFile={str(Path(source.known_hosts).expanduser())}"])
        if source.via:
            args.extend(["-o", f"ProxyCommand=ssh -W %h:%p {source.via}"])
        args.append(f"{user}@{host}")
        return args

    def _remote_command_output(self, source: _Source, command: str) -> str:
        ssh = self._ssh_args(source)
        if not ssh:
            return ""
        try:
            proc = subprocess.run(
                ssh + [command],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(1, int(source.interval_seconds or 1)),
            )
        except Exception:
            return ""
        returncode = getattr(proc, "returncode", 1)
        if returncode is None or int(returncode) != 0:
            return ""
        return str(getattr(proc, "stdout", "") or "")

    def _remote_file_size(self, source: _Source) -> int | None:
        output = self._remote_command_output(source, f"wc -c < {shlex.quote(str(source.path or ''))}")
        text = output.strip()
        if not text:
            return None
        try:
            return int(text.split()[0])
        except Exception:
            return None

    def _remote_file_read(self, source: _Source, *, start_offset: int = 0) -> str:
        path = shlex.quote(str(source.path or ""))
        if start_offset <= 0:
            command = f"cat {path}"
        else:
            command = f"tail -c +{int(start_offset) + 1} {path}"
        return self._remote_command_output(source, command)

    def _poll_http_once(self, source: _Source, *, phase: str, ctx: dict[str, Any]) -> None:
        if not source.url:
            return
        timeout = max(1, int(source.interval_seconds or 1))
        request = urllib.request.Request(
            source.url,
            headers={
                "User-Agent": "TestFabric/WatchRuntime",
                "Accept": "*/*",
            },
        )
        status_code = 0
        body = ""
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status_code = int(getattr(response, "status", response.getcode()) or 0)
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
        except Exception:
            return

        body = self.redact_text(body)
        self._process_sources(
            body,
            phase=phase,
            stream="http",
            source_filter=source.name,
            ctx={**ctx, "url": source.url, "status_code": status_code},
            status_code=status_code,
        )

    def _poll_metric_once(self, source: _Source, *, phase: str, ctx: dict[str, Any]) -> None:
        command: list[str] = []
        if source.cmd:
            command = [str(x) for x in source.cmd if str(x).strip()]
        elif source.bash:
            command = ["bash", "-lc", source.bash]
        elif source.shell:
            command = ["sh", "-lc", source.shell]
        if not command:
            return
        timeout = max(1, int(source.interval_seconds or 1))
        try:
            if source.host:
                output = self._remote_command_output(source, shlex.join(command))
                proc_returncode = 0 if output else 1
                proc_stdout = output
            else:
                proc = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                proc_returncode = getattr(proc, "returncode", 1)
                proc_stdout = str(getattr(proc, "stdout", "") or "")
        except Exception:
            return
        if proc_returncode not in (0, None):
            return
        body = self.redact_text(proc_stdout)
        if not body:
            return
        self._process_sources(
            body,
            phase=phase,
            stream="metric",
            source_filter=source.name,
            ctx={**ctx, "command": command},
        )

    def _resolve_paths(self, pattern: str) -> list[str]:
        raw = str(pattern or "").strip()
        if not raw:
            return []
        if glob.has_magic(raw):
            if self.base_dir and not Path(raw).is_absolute():
                return [str(p) for p in self.base_dir.glob(raw)]
            return glob.glob(raw, recursive=True)
        if self.base_dir and not Path(raw).is_absolute():
            return [str((self.base_dir / raw).resolve())]
        return [raw]

    def _merged_context(self, *, phase: str, context: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(self.run_scope)
        out["phase"] = phase
        if context:
            out.update(context)
        return out

    def _process_sources(
        self,
        text: str,
        *,
        phase: str,
        stream: str,
        source_filter: str | None,
        ctx: dict[str, Any],
        status_code: int | None = None,
    ) -> None:
        for source in self.sources:
            if source_filter and source.name != source_filter:
                continue
            if not self._source_matches(source, phase=phase, stream=stream):
                continue
            if source.type == "session":
                if stream and source.stream not in {"both", stream}:
                    continue
                self._match_watchers(source, text, phase=phase, stream=stream, ctx=ctx, status_code=status_code)
            elif source.type == "file" and stream == "file":
                self._match_watchers(source, text, phase=phase, stream=stream, ctx=ctx, status_code=status_code)
            elif source.type == "remote_file" and stream == "file":
                self._match_watchers(source, text, phase=phase, stream=stream, ctx=ctx, status_code=status_code)
            elif source.type == "http" and stream == "http":
                self._match_watchers(source, text, phase=phase, stream=stream, ctx=ctx, status_code=status_code)
            elif source.type == "metric" and stream == "metric":
                self._match_watchers(source, text, phase=phase, stream=stream, ctx=ctx, status_code=status_code)

    def _source_matches(self, source: _Source, *, phase: str, stream: str) -> bool:
        phases = set(source.phases or ("run",))
        if "run" not in phases and phase not in phases:
            return False
        if source.type == "session" and source.stream not in {"both", stream}:
            return False
        return True

    def _match_watchers(
        self,
        source: _Source,
        text: str,
        *,
        phase: str,
        stream: str,
        ctx: dict[str, Any],
        status_code: int | None = None,
    ) -> None:
        for watcher in self._watchers_by_source.get(source.name, []):
            if not self._watcher_allowed(watcher, phase=phase):
                continue
            token = self._match_token(watcher, text=text, status_code=status_code)
            if token is None:
                continue
            digest = self._digest(source.name, watcher.name, token)
            if watcher.once and watcher.name in self._seen_once:
                continue
            if watcher.dedupe and (watcher.name, digest) in self._seen_dedupe:
                continue
            if watcher.once:
                self._seen_once.add(watcher.name)
            if watcher.dedupe:
                self._seen_dedupe.add((watcher.name, digest))
            self._execute_action(
                watcher,
                source=source,
                text=text,
                token=token,
                phase=phase,
                stream=stream,
                ctx=ctx,
                status_code=status_code,
            )
            if self._abort_requested:
                return

    def _watcher_allowed(self, watcher: _Watcher, *, phase: str) -> bool:
        phases = set(watcher.phases or ("run",))
        if "run" in phases:
            return True
        return phase in phases

    def _match_token(self, watcher: _Watcher, *, text: str, status_code: int | None = None) -> str | None:
        if watcher.sequence:
            return self._match_sequence(watcher, text=text)
        status_token: str | None = None
        if watcher.status:
            if status_code is None or int(status_code) not in set(watcher.status):
                return None
            status_token = str(int(status_code))
        for candidate in (watcher.match, watcher.regex, watcher.body_match):
            if candidate and re.search(candidate, text, flags=re.MULTILINE):
                token = f"{status_token}:{candidate}" if status_token else candidate
                return self._match_numeric(watcher, text=text, token=token)
        if status_token is not None:
            return self._match_numeric(watcher, text=text, token=status_token, status_code=status_code)
        if any(value is not None for value in (watcher.match, watcher.regex, watcher.body_match)):
            return None
        return self._match_numeric(watcher, text=text, token=None, status_code=status_code)

    def _match_sequence(self, watcher: _Watcher, *, text: str) -> str | None:
        if not watcher.sequence:
            return None
        key = self._sequence_key(watcher)
        state = self._sequence_states.get(key)
        if state is None:
            state = _SequenceState(sequence=tuple(watcher.sequence))
            self._sequence_states[key] = state
        if state.complete:
            return None

        state.buffer += text
        while state.index < len(state.sequence):
            pattern = state.sequence[state.index]
            try:
                matched = re.search(pattern, state.buffer[state.cursor:], flags=re.MULTILINE)
            except re.error:
                matched = None
            if matched is None:
                break
            state.cursor += int(matched.end())
            state.matched.append(pattern)
            state.index += 1
            if state.cursor > 8192:
                state.buffer = state.buffer[state.cursor:]
                state.cursor = 0
            if state.index >= len(state.sequence):
                state.complete = True
                return " -> ".join(state.matched) or "sequence"
        return None

    def _sequence_key(self, watcher: _Watcher) -> tuple[str, str]:
        source_name = watcher.source or (watcher.sources[0] if watcher.sources else "")
        return (source_name, watcher.name)

    def finalize(self, *, phase: str = "stage") -> None:
        for watcher in self.watchers:
            if not watcher.sequence:
                continue
            if not self._watcher_allowed(watcher, phase=phase):
                continue
            key = self._sequence_key(watcher)
            state = self._sequence_states.get(key)
            if state is None or state.complete:
                continue

            missing = str(getattr(watcher, "missing", "ignore") or "ignore").strip().lower()
            if missing == "ignore":
                continue

            details = self._watch_event_details(
                watcher=watcher,
                source_name=key[0],
                source_type="sequence",
                stream="sequence",
                phase=phase,
                token=" -> ".join(watcher.sequence),
                text=state.buffer,
                action_type="report" if missing == "warn" else "fail",
                status_code=None,
                extra={
                    "missing": "sequence",
                    "sequence": list(watcher.sequence),
                    "sequence_index": state.index,
                    "sequence_complete": False,
                },
                action_name="report" if missing == "warn" else "fail",
                action_message=f"{watcher.name}: missing sequence {' -> '.join(watcher.sequence)}",
                level="warn" if missing == "warn" else "error",
            )
            event_status = "warn" if missing == "warn" else "fail"
            self.events.emit(Event("watch", watcher.name, event_status, details))
            if missing == "warn":
                self._reports.append(details)
                continue

            self._reports.append(details)
            self._abort_requested = True
            self._failure_reason = self._failure_reason or (
                f"{watcher.name}: missing sequence {' -> '.join(watcher.sequence)}"
            )

    def _match_numeric(
        self,
        watcher: _Watcher,
        *,
        text: str,
        token: str | None,
        status_code: int | None = None,
    ) -> str | None:
        if all(v is None for v in (watcher.gt, watcher.gte, watcher.lt, watcher.lte)):
            return token

        match = _NUMERIC_RE.search(text or "")
        if match is None and status_code is not None:
            number = float(int(status_code))
        elif match is None:
            return None
        else:
            try:
                number = float(match.group(0))
            except Exception:
                return None

        if watcher.gt is not None and not (number > float(watcher.gt)):
            return None
        if watcher.gte is not None and not (number >= float(watcher.gte)):
            return None
        if watcher.lt is not None and not (number < float(watcher.lt)):
            return None
        if watcher.lte is not None and not (number <= float(watcher.lte)):
            return None
        return token or f"{number:g}"

    def _watch_event_details(
        self,
        *,
        watcher: _Watcher,
        source_name: str,
        source_type: str,
        stream: str,
        phase: str,
        token: str,
        text: str,
        action_type: str,
        status_code: int | None,
        extra: dict[str, Any] | None = None,
        action_name: str | None = None,
        action_message: str | None = None,
        level: str = "info",
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        details = {
            "watcher": watcher.name,
            "source": source_name,
            "source_type": source_type,
            "stream": stream,
            "phase": phase,
            "match": token,
            "text": text[:400],
            "action": action_name or watcher.name,
            "action_type": action_type,
            "action_message": action_message,
            "status_code": status_code,
            **(ctx or {}),
        }
        if extra:
            details.update(extra)
        if action_type == "report":
            details["message"] = action_message or token
            details["level"] = level
        elif action_message:
            details["message"] = action_message
        return details

    def _execute_action(
        self,
        watcher: _Watcher,
        *,
        source: _Source,
        text: str,
        token: str,
        phase: str,
        stream: str,
        ctx: dict[str, Any],
        status_code: int | None = None,
    ) -> None:
        action = watcher.action
        if isinstance(action, str):
            action = self._action_by_name.get(action) or _Action(name=action, type=action)

        action_type = str(action.type or "report").strip() or "report"
        if action_type == "notify":
            action_type = "report"

        event_status = "success"
        if action_type in {"fail", "stop"}:
            event_status = "fail"
        elif action_type == "report":
            event_status = str(action.level or "warn")

        details = self._watch_event_details(
            watcher=watcher,
            source_name=source.name,
            source_type=source.type,
            stream=stream,
            phase=phase,
            token=token,
            text=text,
            action_type=action_type,
            status_code=status_code,
            ctx=ctx,
            action_name=action.name,
            action_message=action.message,
            level=action.level,
        )

        self.events.emit(Event("watch", watcher.name, event_status, details))

        if action_type == "report":
            self._reports.append(details)
            return
        if action_type == "send":
            value = action.value or ""
            self._sends.append(value)
            if self.send_hook:
                self.send_hook(value, details)
            return
        if action_type == "script":
            command = list(action.command or [])
            if not command:
                return
            env = os.environ.copy()
            run_id = ctx.get("run_id")
            if run_id in {None, ""}:
                run_id = self.run_scope.get("run_id")
            stage_id = ctx.get("stage_id")
            if stage_id in {None, ""}:
                stage_id = self.run_scope.get("stage_id")
            job_id = ctx.get("job_id")
            if job_id in {None, ""}:
                job_id = self.run_scope.get("job_id")
            attempt = ctx.get("attempt")
            if attempt is None:
                attempt = self.run_scope.get("attempt")
            target_id = ctx.get("target_id")
            if target_id in {None, ""}:
                target_id = self.run_scope.get("target_id")
            worker_id = ctx.get("worker_id")
            if worker_id in {None, ""}:
                worker_id = self.run_scope.get("worker_id")
            env.update(
                {
                    "TESTFABRIC_RUN_ID": str(run_id or "").strip(),
                    "TESTFABRIC_STAGE_ID": str(stage_id or "").strip(),
                    "TESTFABRIC_JOB_ID": str(job_id or "").strip(),
                    "TESTFABRIC_ATTEMPT": str(attempt if attempt is not None else "").strip(),
                    "TESTFABRIC_TARGET_ID": str(target_id or "").strip(),
                    "TESTFABRIC_WORKER_ID": str(worker_id or "").strip(),
                    "TESTFABRIC_WATCHER": watcher.name,
                    "TESTFABRIC_WATCH_SOURCE": source.name,
                    "TESTFABRIC_WATCH_MATCH": token,
                    "TESTFABRIC_WATCH_TEXT": text,
                    "TESTFABRIC_WATCH_PHASE": phase,
                    "TESTFABRIC_WATCH_ACTION": action.name,
                    "TESTFABRIC_WATCH_ACTION_TYPE": action_type,
                }
            )
            if action.message:
                env["TESTFABRIC_WATCH_MESSAGE"] = action.message
            if self.script_hook:
                self.script_hook(command, env)
            else:
                subprocess.run(command, check=False, env=env)
            return
        if action_type == "success":
            self._successes.append(watcher.name)
            return
        if action_type in {"fail", "stop"} or action.stop_all:
            self._abort_requested = True
            self._failure_reason = self._failure_reason or f"{watcher.name}: {token}"
            return
        if action_type == "continue":
            return

    def _digest(self, source_name: str, watcher_name: str, token: str) -> str:
        raw = f"{source_name}|{watcher_name}|{token}".encode("utf-8", errors="ignore")
        return hashlib.sha1(raw).hexdigest()
