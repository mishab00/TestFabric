from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from testfabric.core.constants import AUTH_BEARER_PREFIX, AUTH_HEADER_NAME, DEFAULT_API_TIMEOUT_SECONDS, DEFAULT_QUEUE_POLICY
from testfabric.core.contracts import RunLease, RunResponse, RunResult, WorkerRegistration, WorkerStatus


Requester = Callable[[str, str, dict[str, Any] | None, dict[str, str], float], tuple[int, str]]


@dataclass(slots=True)
class WorkerClient:
    api_url: str
    token: str | None = None
    timeout: float = DEFAULT_API_TIMEOUT_SECONDS
    requester: Requester | None = None

    def _endpoint(self, path: str) -> str:
        base = str(self.api_url or "").strip()
        if not base:
            raise ValueError("Worker client is missing api_url")
        return urljoin(base.rstrip("/") + "/", path.lstrip("/"))

    def _default_requester(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, headers=headers, method=method)
        if self.token:
            request.add_header(AUTH_HEADER_NAME, f"{AUTH_BEARER_PREFIX}{self.token}")
        try:
            with urlopen(request, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 200))
                return status_code, response.read().decode("utf-8").strip()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or exc.reason or str(exc)
            raise RuntimeError(f"Worker API request failed ({exc.code}): {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Worker API request failed: {exc.reason}") from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        allow_empty: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        requester = self.requester or self._default_requester
        status_code, raw = requester(method, self._endpoint(path), body, headers, float(self.timeout or 0.0))
        if status_code >= 400:
            raise RuntimeError(f"Worker API request failed ({status_code}): {raw or 'empty response'}")
        if allow_empty and status_code == 204:
            return {}
        if not raw:
            return {}
        parsed = json.loads(raw)
        return parsed

    def register_worker(self, request: WorkerRegistration) -> WorkerStatus:
        payload = self._request_json("POST", "workers", body=request.model_dump(mode="json", exclude_none=True))
        return WorkerStatus.model_validate(payload)

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        health_state: str | None = None,
        health_reasons: list[str] | None = None,
    ) -> WorkerStatus:
        payload = self._request_json(
            "POST",
            f"workers/{worker_id}/heartbeat",
            body={
                "metadata": dict(metadata or {}),
                "health_state": health_state,
                "health_reasons": list(health_reasons or []),
            },
        )
        return WorkerStatus.model_validate(payload)

    def list_workers(self) -> list[WorkerStatus]:
        payload = self._request_json("GET", "workers")
        if not isinstance(payload, list):
            raise RuntimeError("Worker list returned an invalid response")
        return [WorkerStatus.model_validate(item) for item in payload if isinstance(item, dict)]

    def get_worker(self, worker_id: str) -> WorkerStatus:
        payload = self._request_json("GET", f"workers/{worker_id}")
        return WorkerStatus.model_validate(payload)

    def claim_next_run(
        self,
        *,
        worker_id: str | None = None,
        worker_labels: list[str] | None = None,
        policy: str = DEFAULT_QUEUE_POLICY,
        metadata: dict[str, Any] | None = None,
    ) -> RunLease | None:
        headers = {"Accept": "application/json"}
        body = {
            "worker_id": worker_id,
            "worker_labels": list(worker_labels or []),
            "policy": policy,
            "metadata": dict(metadata or {}),
        }
        requester = self.requester or self._default_requester
        status_code, raw = requester("POST", self._endpoint("queue/claim"), body, headers, float(self.timeout or 0.0))
        if status_code == 204:
            return None
        if status_code >= 400:
            raise RuntimeError(f"Worker claim failed ({status_code}): {raw or 'empty response'}")
        if not raw:
            raise RuntimeError("Worker claim returned an empty response")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("Worker claim returned an invalid response")
        return RunLease.model_validate(payload)

    def heartbeat_run(self, run_id: str, *, worker_id: str | None = None) -> RunResponse:
        payload = self._request_json(
            "POST",
            f"runs/{run_id}/heartbeat",
            body={"worker_id": worker_id, "metadata": {}},
        )
        return RunResponse.model_validate(payload)

    def cancel_run(self, run_id: str, *, reason: str | None = None, worker_id: str | None = None) -> RunResponse:
        payload = self._request_json(
            "POST",
            f"runs/{run_id}/cancel",
            body={"worker_id": worker_id, "reason": reason, "metadata": {}},
        )
        return RunResponse.model_validate(payload)

    def complete_run(self, run_id: str, result: RunResult) -> RunResponse:
        payload = self._request_json(
            "POST",
            f"runs/{run_id}/complete",
            body=result.model_dump(mode="json", exclude_none=True),
        )
        return RunResponse.model_validate(payload)
