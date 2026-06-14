from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CommandResult:
    command: list[str] | str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int | None = None
    host: str | None = None
    transport: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def command_text(self) -> str:
        if isinstance(self.command, str):
            return self.command
        return " ".join(shlex.quote(str(part)) for part in self.command)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["command"] = self.command_text
        data["started_at"] = format_timestamp(self.started_at)
        data["ended_at"] = format_timestamp(self.ended_at)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


__all__ = ["CommandResult", "format_timestamp", "utc_now"]
