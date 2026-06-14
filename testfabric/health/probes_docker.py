from __future__ import annotations

import json
from typing import Any

from testfabric.execution.executors.dockerops import docker_client
from testfabric.health.models import HealthCheckResult


class DockerHealthProbeSuite:
    def _probe_client(self, *, name: str, endpoint: dict[str, Any] | None = None) -> HealthCheckResult:
        try:
            with docker_client(endpoint) as client:
                client.ping()
                info = client.info()
            return HealthCheckResult(
                name=name,
                ok=True,
                severity="required",
                details={
                    "server_version": info.get("ServerVersion"),
                    "driver": info.get("Driver"),
                    "endpoint": dict(endpoint or {}),
                },
            )
        except Exception as e:
            return HealthCheckResult(
                name=name,
                ok=False,
                severity="required",
                message=f"Docker preflight failed: {e}",
                details={"endpoint": dict(endpoint or {})},
            )

    def probe(
        self,
        *,
        required: bool,
        endpoints: list[dict[str, Any]] | None = None,
    ) -> list[HealthCheckResult]:
        checks: list[HealthCheckResult] = []

        if required:
            checks.append(self._probe_client(name="docker"))

        seen: set[str] = set()
        for endpoint in list(endpoints or []):
            raw = dict(endpoint or {})
            key = json.dumps(raw, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            mode = str(raw.get("mode") or "").strip().lower()
            base_url = str(raw.get("base_url") or "").strip()
            if not base_url and mode in {"", "local"}:
                continue
            display = base_url or str(raw.get("host") or "docker-endpoint")
            checks.append(self._probe_client(name=f"docker:{display}", endpoint=raw))

        return checks
