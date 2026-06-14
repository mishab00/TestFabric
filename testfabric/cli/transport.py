from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from testfabric.cli.config import ContextProfile
from testfabric.artifacts.paths import PathManager
from testfabric.core.constants import (
    AUTH_BEARER_PREFIX,
    AUTH_HEADER_NAME,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_ARTIFACT_BROWSER_LIMIT,
    DEFAULT_EVENT_LIMIT,
)
from testfabric.core.contracts import ReplayRequest, RunQuery, RunRequest
from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


TransportName = Literal["direct", "http"]


@dataclass(frozen=True)
class RunInvocation:
    spec_path: Path
    spec_text: str
    spec: RunSpec
    resolved_inputs: dict[str, Any]
    mode: Literal["run", "dry-run"]
    run_id: str
    suite: str | None
    build: bool
    verbosity: Literal["quiet", "summary", "normal", "verbose"]
    profile: str | None
    context: ContextProfile
    lint_spec: bool = False


@dataclass(frozen=True)
class RunDispatchResult:
    transport: TransportName
    run_id: str
    ok: bool
    accepted: bool
    payload: dict[str, Any]
    message: str | None = None
    status_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "run_id": self.run_id,
            "ok": self.ok,
            "accepted": self.accepted,
            "message": self.message,
            "status_code": self.status_code,
            "payload": self.payload,
        }


class RunTransport(Protocol):
    def execute(self, invocation: RunInvocation) -> RunDispatchResult: ...
    def list_runs(self, query: RunQuery | None = None) -> dict[str, Any]: ...
    def dashboard(self, query: RunQuery | None = None) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any]: ...
    def get_run_detail(self, run_id: str) -> dict[str, Any]: ...
    def get_run_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_EVENT_LIMIT,
        follow: bool = False,
    ) -> dict[str, Any]: ...
    def list_run_artifacts(
        self,
        run_id: str,
        *,
        path: str | None = None,
        recursive: bool = True,
        limit: int = DEFAULT_ARTIFACT_BROWSER_LIMIT,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]: ...
    def cancel_run(self, run_id: str, *, reason: str | None = None) -> dict[str, Any]: ...
    def compare_runs(self, left_run_id: str, right_run_id: str) -> dict[str, Any]: ...
    def replay_run(self, request: ReplayRequest) -> RunDispatchResult: ...


class DirectTransport:
    def execute(self, invocation: RunInvocation) -> RunDispatchResult:
        options = RunOptions.from_spec_and_cli(
            invocation.spec,
            mode=invocation.mode,
            run_id=invocation.run_id,
            resolved_inputs=invocation.resolved_inputs,
            console_verbosity=invocation.verbosity,
        )
        result = Orchestrator(invocation.spec, options).run(suite_override=invocation.suite, build=invocation.build)
        payload = dict(result or {})
        payload.setdefault("run_id", invocation.run_id)
        return RunDispatchResult(
            transport="direct",
            run_id=str(payload.get("run_id") or invocation.run_id),
            ok=bool(payload.get("ok")),
            accepted=False,
            payload=payload,
            message=str(payload.get("error") or "").strip() or None,
        )

    def list_runs(self, query: RunQuery | None = None) -> dict[str, Any]:
        raise NotImplementedError("Run history browsing is only available through a backend context for now")

    def dashboard(self, query: RunQuery | None = None) -> dict[str, Any]:
        raise NotImplementedError("Dashboard browsing is only available through a backend context for now")

    def get_run(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError("Run history browsing is only available through a backend context for now")

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError("Run detail browsing is only available through a backend context for now")

    def get_run_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_EVENT_LIMIT,
        follow: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError("Event streaming is only available through a backend context for now")

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        path: str | None = None,
        recursive: bool = True,
        limit: int = DEFAULT_ARTIFACT_BROWSER_LIMIT,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Artifact browsing is only available through a backend context for now")

    def cancel_run(self, run_id: str, *, reason: str | None = None) -> dict[str, Any]:
        raise NotImplementedError("Run cancellation is only available through a backend context for now")

    def compare_runs(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        raise NotImplementedError("Run comparison is only available through a backend context for now")

    def replay_run(self, request: ReplayRequest) -> RunDispatchResult:
        raise NotImplementedError("Run replay is only available through a backend context for now")


class HttpTransport:
    def __init__(self, context: ContextProfile):
        self.context = context

    def execute(self, invocation: RunInvocation) -> RunDispatchResult:
        api_url = str(self.context.api_url or "").strip()
        if not api_url:
            raise ValueError(f"Remote context '{self.context.name}' is missing api_url")

        request_model = RunRequest(
            run_id=invocation.run_id,
            mode=invocation.mode,
            spec_yaml=invocation.spec_text,
            spec_path=str(invocation.spec_path),
            spec=invocation.spec.model_dump(mode="json", exclude_none=True),
            repo_url=str(getattr(invocation.spec.run, "repo_url", None) or ""),
            ref=str(getattr(invocation.spec.run, "ref", None) or ""),
            team=self.context.team,
            project=self.context.project,
            workspace=self.context.workspace,
            context=self.context.name,
            inputs=dict(invocation.resolved_inputs or {}),
            tags=[tag for tag in [
                f"context:{self.context.name}",
                f"mode:{invocation.mode}",
                f"suite:{invocation.suite}" if invocation.suite else "",
                "lint-spec" if invocation.lint_spec else "",
                "build" if invocation.build else "",
            ] if tag],
            metadata={
                "source": "cli",
                "profile": invocation.profile,
                "verbosity": invocation.verbosity,
                "suite": invocation.suite,
                "build": invocation.build,
                "lint_spec": invocation.lint_spec,
                "run_dir": str(PathManager(invocation.spec, invocation.run_id).run_dir),
            },
        )

        endpoint = urljoin(api_url.rstrip("/") + "/", "runs")
        body = json.dumps(request_model.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        req = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        if self.context.token:
            req.add_header(AUTH_HEADER_NAME, f"{AUTH_BEARER_PREFIX}{self.context.token}")

        try:
            with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or exc.reason or str(exc)
            raise RuntimeError(f"Remote submission failed ({exc.code}): {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Remote submission failed: {exc.reason}") from exc

        payload: dict[str, Any]
        if raw:
            try:
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                payload = {"message": raw}
        else:
            payload = {}

        run_id = str(payload.get("run_id") or request_model.run_id or invocation.run_id)
        accepted = bool(payload.get("accepted", status_code in {200, 201, 202}))
        ok = status_code < 400 and accepted
        message = payload.get("message")
        return RunDispatchResult(
            transport="http",
            run_id=run_id,
            ok=ok,
            accepted=accepted,
            payload=payload,
            message=str(message).strip() if message is not None else None,
            status_code=status_code,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: RunQuery | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        api_url = str(self.context.api_url or "").strip()
        if not api_url:
            raise ValueError(f"Remote context '{self.context.name}' is missing api_url")

        query_bits: dict[str, Any] = {}
        if query is not None:
            query_bits = {
                "team": query.team,
                "project": query.project,
                "workspace": query.workspace,
                "branch": query.branch,
                "ref": query.ref,
                "status": list(query.status or []),
                "tags": list(query.tags or []),
                "target_id": query.target_id,
                "target_name": query.target_name,
                "limit": query.limit,
                "offset": query.offset,
                "sort": query.sort,
            }
            query_bits = {
                key: value
                for key, value in query_bits.items()
                if value is not None and value != []
            }
        if params:
            query_bits.update({key: value for key, value in params.items() if value is not None and value != []})
        endpoint = urljoin(api_url.rstrip("/") + "/", path.lstrip("/"))
        if query_bits:
            endpoint = f"{endpoint}?{urlencode(query_bits, doseq=True)}"
        req = Request(
            endpoint,
            headers={
                "Accept": "application/json",
            },
            method=method,
        )
        if self.context.token:
            req.add_header(AUTH_HEADER_NAME, f"{AUTH_BEARER_PREFIX}{self.context.token}")

        try:
            with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or exc.reason or str(exc)
            raise RuntimeError(f"Remote request failed ({exc.code}): {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Remote request failed: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Remote request returned invalid JSON: {raw}") from exc
        if not isinstance(parsed, dict):
            return {"data": parsed}
        return parsed

    def list_runs(self, query: RunQuery | None = None) -> dict[str, Any]:
        return self._request_json("GET", "runs", query=query)

    def dashboard(self, query: RunQuery | None = None) -> dict[str, Any]:
        return self._request_json("GET", "dashboard", query=query)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"runs/{run_id}")

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"runs/{run_id}/detail")

    def get_run_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_EVENT_LIMIT,
        follow: bool = False,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"runs/{run_id}/events",
            query=RunQuery(offset=offset, limit=limit),
            params={"follow": follow},
        )

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        path: str | None = None,
        recursive: bool = True,
        limit: int = DEFAULT_ARTIFACT_BROWSER_LIMIT,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"runs/{run_id}/artifacts",
            params={
                "path": path,
                "recursive": recursive,
                "limit": limit,
                "offset": offset,
                "search": search,
            },
        )

    def cancel_run(self, run_id: str, *, reason: str | None = None) -> dict[str, Any]:
        api_url = str(self.context.api_url or "").strip()
        if not api_url:
            raise ValueError(f"Remote context '{self.context.name}' is missing api_url")

        endpoint = urljoin(api_url.rstrip("/") + "/", f"runs/{run_id}/cancel")
        body = json.dumps(
            {
                "worker_id": None,
                "reason": reason,
                "metadata": {},
            }
        ).encode("utf-8")
        req = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        if self.context.token:
            req.add_header(AUTH_HEADER_NAME, f"{AUTH_BEARER_PREFIX}{self.context.token}")

        try:
            with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or exc.reason or str(exc)
            raise RuntimeError(f"Remote cancellation failed ({exc.code}): {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Remote cancellation failed: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Remote request returned invalid JSON: {raw}") from exc
        if not isinstance(parsed, dict):
            return {"data": parsed}
        return parsed

    def compare_runs(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"compare/{left_run_id}/{right_run_id}")

    def replay_run(self, request: ReplayRequest) -> RunDispatchResult:
        api_url = str(self.context.api_url or "").strip()
        if not api_url:
            raise ValueError(f"Remote context '{self.context.name}' is missing api_url")

        endpoint = urljoin(api_url.rstrip("/") + "/", f"runs/{request.run_id}/replay")
        body = json.dumps(request.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        req = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        if self.context.token:
            req.add_header(AUTH_HEADER_NAME, f"{AUTH_BEARER_PREFIX}{self.context.token}")

        try:
            with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or exc.reason or str(exc)
            raise RuntimeError(f"Remote replay failed ({exc.code}): {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Remote replay failed: {exc.reason}") from exc

        payload: dict[str, Any]
        if raw:
            try:
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                payload = {"message": raw}
        else:
            payload = {}

        run_id = str(payload.get("run_id") or request.run_id)
        accepted = bool(payload.get("accepted", status_code in {200, 201, 202}))
        ok = status_code < 400 and accepted
        message = payload.get("message")
        return RunDispatchResult(
            transport="http",
            run_id=run_id,
            ok=ok,
            accepted=accepted,
            payload=payload,
            message=str(message).strip() if message is not None else None,
            status_code=status_code,
        )


def resolve_transport(context: ContextProfile) -> RunTransport:
    if context.mode == "local":
        return DirectTransport()
    return HttpTransport(context)
