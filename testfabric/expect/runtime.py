from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from testfabric.core.events import Event, EventSink


def _as_dict(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))  # type: ignore[no-any-return]
    if isinstance(value, dict):
        return dict(value)
    return {}


@dataclass(frozen=True)
class _Prompt:
    name: str
    match: str | None
    regex: str | None
    stream: str
    send: str


class ExpectRuntime:
    """
    Minimal interactive session engine.

    It watches stdout/stderr text from a running process and returns strings
    that the executor should write to stdin when a prompt matches.
    """

    def __init__(
        self,
        steps: list[object] | None,
        *,
        events: EventSink,
        run_scope: dict[str, Any] | None = None,
        redact_text: Callable[[str], str] | None = None,
    ) -> None:
        self.events = events
        self.run_scope = dict(run_scope or {})
        self.redact_text = redact_text or (lambda text: text)
        self.steps: list[_Prompt] = []
        for idx, raw in enumerate(steps or []):
            step = _as_dict(raw)
            when = _as_dict(step.get("when"))
            then = _as_dict(step.get("then"))
            name = str(step.get("name") or f"step-{idx + 1}").strip() or f"step-{idx + 1}"
            send = str(then.get("send") or "").strip()
            if not send:
                continue
            self.steps.append(
                _Prompt(
                    name=name,
                    match=(str(when.get("match") or "").strip() or None),
                    regex=(str(when.get("regex") or "").strip() or None),
                    stream=str(when.get("stream") or "stdout").strip() or "stdout",
                    send=send,
                )
            )
        self._index = 0
        self._sent: list[str] = []

    @property
    def sent(self) -> list[str]:
        return list(self._sent)

    @property
    def complete(self) -> bool:
        return self._index >= len(self.steps)

    def feed_output(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        safe_text = self.redact_text(text or "")
        if not safe_text or self.complete:
            return None

        ctx = dict(context or {})
        stream = str(ctx.pop("stream", "stdout") or "stdout")
        phase = str(ctx.pop("phase", "job") or "job")

        current = self.steps[self._index]
        if current.stream not in {"both", stream}:
            return None

        matched = False
        if current.regex:
            matched = bool(re.search(current.regex, safe_text, flags=re.MULTILINE))
        elif current.match:
            matched = current.match in safe_text

        if not matched:
            return None

        value = current.send
        if value and not value.endswith("\n"):
            value = f"{value}\n"
        self._sent.append(value)
        self._index += 1
        details = {
            "step": current.name,
            "stream": stream,
            "phase": phase,
            "match": current.regex or current.match,
            "text": safe_text[:400],
            "send": self.redact_text(value),
            **self.run_scope,
            **ctx,
        }
        self.events.emit(Event("expect", current.name, "success", details))
        return value
