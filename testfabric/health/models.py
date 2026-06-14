from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


HealthState = Literal["healthy", "degraded", "unhealthy"]
HealthSeverity = Literal["required", "warning"]


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    ok: bool
    severity: HealthSeverity = "required"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": bool(self.ok),
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class WorkerHealthSnapshot:
    state: HealthState
    checks: list[HealthCheckResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reasons": list(self.reasons or []),
            "checks": [c.as_dict() for c in list(self.checks or [])],
        }
