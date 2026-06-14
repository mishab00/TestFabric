from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from testfabric.core.constants import (
    APP_NAME,
    APP_VERSION,
    AUTH_BEARER_PREFIX,
    AUTH_HEADER_NAME,
    DEFAULT_ARTIFACT_BROWSER_LIMIT,
    DEFAULT_EVENT_LIMIT,
    DEFAULT_QUEUE_POLICY,
)
from testfabric.core.contracts import (
    ReplayRequest,
    RunLease,
    RunQuery,
    RunRequest,
    RunResponse,
    RunResult,
    WorkerRegistration,
    WorkerStatus,
)

from .store import SqliteRunStore


class QueueClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    worker_labels: list[str] = Field(default_factory=list)
    policy: str = DEFAULT_QUEUE_POLICY
    metadata: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    health_state: str | None = None
    health_reasons: list[str] = Field(default_factory=list)


def get_store(request: Request) -> SqliteRunStore:
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, SqliteRunStore):
        raise RuntimeError("TestFabric API store is not configured")
    return store


def create_app(
    store: SqliteRunStore | None = None,
    *,
    database_url: str | None = None,
    api_token: str | None = None,
    worker_heartbeat_timeout_seconds: float | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "store", None) is None:
            factory = getattr(app.state, "store_factory", None)
            if callable(factory):
                app.state.store = factory()
                app.state.owns_store = True
        try:
            yield
        finally:
            if getattr(app.state, "owns_store", False):
                store_obj = getattr(app.state, "store", None)
                if isinstance(store_obj, SqliteRunStore):
                    store_obj.close()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.store = store
    app.state.store_factory = (
        lambda: SqliteRunStore(
            database_url=database_url,
            worker_heartbeat_timeout_seconds=worker_heartbeat_timeout_seconds,
        )
    ) if store is None else None
    app.state.owns_store = store is None
    resolved_token = str(api_token or os.environ.get("TESTFABRIC_API_TOKEN") or "").strip() or None
    app.state.api_token = resolved_token

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        token = getattr(request.app.state, "api_token", None)
        if token:
                path = request.url.path
                if not (path == "/health" or path.startswith("/docs") or path.startswith("/redoc") or path == "/openapi.json"):
                    authorization = request.headers.get(AUTH_HEADER_NAME, "").strip()
                    if authorization != f"{AUTH_BEARER_PREFIX}{token}":
                        return JSONResponse(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            content={"detail": "Unauthorized"},
                            headers={"WWW-Authenticate": AUTH_BEARER_PREFIX.strip()},
                        )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
    def submit_run(
        request: RunRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunResponse:
        return store.create_run(request)

    @app.get("/runs")
    def list_runs(
        team: str | None = None,
        project: str | None = None,
        workspace: str | None = None,
        branch: str | None = None,
        ref: str | None = None,
        status_filter: list[str] = Query(default_factory=list, alias="status"),
        tags: list[str] = Query(default_factory=list),
        target_id: str | None = None,
        target_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str | None = None,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        query = RunQuery(
            team=team,
            project=project,
            workspace=workspace,
            branch=branch,
            ref=ref,
            status=status_filter,
            tags=tags,
            target_id=target_id,
            target_name=target_name,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        return store.query_runs(query)

    @app.get("/dashboard")
    def dashboard(
        team: str | None = None,
        project: str | None = None,
        workspace: str | None = None,
        branch: str | None = None,
        ref: str | None = None,
        status_filter: list[str] = Query(default_factory=list, alias="status"),
        tags: list[str] = Query(default_factory=list),
        target_id: str | None = None,
        target_name: str | None = None,
        limit: int = 5,
        offset: int = 0,
        sort: str | None = None,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        query = RunQuery(
            team=team,
            project=project,
            workspace=workspace,
            branch=branch,
            ref=ref,
            status=status_filter,
            tags=tags,
            target_id=target_id,
            target_name=target_name,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        return store.dashboard(query)

    @app.get("/runs/{run_id}", response_model=RunResponse)
    def get_run(run_id: str, store: SqliteRunStore = Depends(get_store)) -> RunResponse:
        response = store.get_run(run_id)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.get("/runs/{run_id}/detail")
    def get_run_detail(run_id: str, store: SqliteRunStore = Depends(get_store)) -> dict[str, Any]:
        response = store.get_run_detail(run_id)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.get("/runs/{run_id}/events")
    def get_run_events(
        run_id: str,
        offset: int = 0,
        limit: int = DEFAULT_EVENT_LIMIT,
        follow: bool = False,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        response = store.get_run_events(run_id, offset=offset, limit=limit, follow=follow)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.get("/runs/{run_id}/artifacts")
    def get_run_artifacts(
        run_id: str,
        path: str | None = None,
        recursive: bool = True,
        limit: int = DEFAULT_ARTIFACT_BROWSER_LIMIT,
        offset: int = 0,
        search: str | None = None,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        response = store.list_run_artifacts(
            run_id,
            path=path,
            recursive=recursive,
            limit=limit,
            offset=offset,
            search=search,
        )
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.get("/queue")
    def list_queue(
        team: str | None = None,
        project: str | None = None,
        workspace: str | None = None,
        branch: str | None = None,
        ref: str | None = None,
        status_filter: list[str] = Query(default_factory=list, alias="status"),
        tags: list[str] = Query(default_factory=list),
        target_id: str | None = None,
        target_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str | None = None,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        query = RunQuery(
            team=team,
            project=project,
            workspace=workspace,
            branch=branch,
            ref=ref,
            status=status_filter,
            tags=tags,
            target_id=target_id,
            target_name=target_name,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        return store.query_queue(query)

    @app.post("/queue/claim", response_model=RunLease)
    def claim_queue(
        request: QueueClaimRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunLease | Response:
        try:
            response = store.claim_next_run(
                worker_id=request.worker_id,
                worker_labels=list(request.worker_labels or []),
                policy=request.policy,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if response is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return response

    @app.post("/workers", response_model=WorkerStatus, status_code=status.HTTP_201_CREATED)
    def register_worker(
        request: WorkerRegistration,
        store: SqliteRunStore = Depends(get_store),
    ) -> WorkerStatus:
        return store.register_worker(request)

    @app.get("/workers", response_model=list[WorkerStatus])
    def list_workers(store: SqliteRunStore = Depends(get_store)) -> list[WorkerStatus]:
        return store.list_workers()

    @app.get("/workers/{worker_id}", response_model=WorkerStatus)
    def get_worker(worker_id: str, store: SqliteRunStore = Depends(get_store)) -> WorkerStatus:
        response = store.get_worker(worker_id)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Worker not found: {worker_id}")
        return response

    @app.post("/workers/{worker_id}/heartbeat", response_model=WorkerStatus)
    def heartbeat_worker(
        worker_id: str,
        request: WorkerHeartbeatRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> WorkerStatus:
        response = store.heartbeat_worker(worker_id, metadata=request.metadata)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Worker not found: {worker_id}")
        if request.health_state is not None:
            registered = store.register_worker(
                WorkerRegistration(
                    worker_id=worker_id,
                    mode=response.mode,
                    host=response.host,
                    user=response.user,
                    labels=list(response.labels or []),
                    capacity=response.capacity,
                    health_state=request.health_state,
                    health_reasons=list(request.health_reasons or []),
                    metadata=dict(response.metadata or {}),
                )
            )
            return registered
        return response

    @app.post("/runs/{run_id}/heartbeat", response_model=RunResponse)
    def heartbeat_run(
        run_id: str,
        request: HeartbeatRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunResponse:
        response = store.heartbeat_run(run_id, worker_id=request.worker_id)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.post("/runs/{run_id}/cancel", response_model=RunResponse)
    def cancel_run(
        run_id: str,
        request: CancelRunRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunResponse:
        response = store.cancel_run(run_id, reason=request.reason, worker_id=request.worker_id)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.post("/runs/{run_id}/complete", response_model=RunResponse)
    def complete_run(
        run_id: str,
        request: RunResult,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunResponse:
        response = store.complete_run(run_id, request)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    @app.get("/compare/{left_run_id}/{right_run_id}")
    def compare_runs(
        left_run_id: str,
        right_run_id: str,
        store: SqliteRunStore = Depends(get_store),
    ) -> dict[str, Any]:
        response = store.compare_runs(left_run_id, right_run_id)
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run comparison failed: {left_run_id} vs {right_run_id}",
            )
        return response

    @app.post("/runs/{run_id}/replay", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
    def replay_run(
        run_id: str,
        request: ReplayRequest,
        store: SqliteRunStore = Depends(get_store),
    ) -> RunResponse:
        if request.run_id != run_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Replay body run_id must match the path")
        response = store.replay_run(request)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        return response

    return app


app = create_app()
