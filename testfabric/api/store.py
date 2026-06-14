from __future__ import annotations

import json
import os
import uuid
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from testfabric.artifacts.paths import PathManager
from testfabric.core.constants import (
    DEFAULT_ARTIFACT_BROWSER_LIMIT,
    DEFAULT_ARTIFACT_PREVIEW_BYTES,
    DEFAULT_ARTIFACT_PREVIEW_LINES,
    DEFAULT_DB_FILENAME,
    DEFAULT_EVENTS_FILENAME,
    DEFAULT_EVENT_FOLLOW_POLL_INTERVAL_SECONDS,
    DEFAULT_EVENT_FOLLOW_TIMEOUT_SECONDS,
    DEFAULT_EVENT_LIMIT,
    DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_QUEUE_POLICY,
    RUN_STATE_CANCELED,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_DRY_RUN,
    RUN_STATE_QUEUED,
    RUN_STATE_RUNNING,
    WORKER_STATE_HEALTHY,
    WORKER_STATE_STALE,
    WORKER_STATE_UNHEALTHY,
)
from testfabric.core.contracts import (
    RunLease,
    RunQuery,
    RunRequest,
    RunResponse,
    RunResult,
    RunStatus,
    ReplayRequest,
    WorkerRegistration,
    WorkerStatus,
)
from testfabric.spec.schema import RunSpec


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    accepted: Mapped[bool] = mapped_column(default=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerRecord(Base):
    __tablename__ = "workers"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), default="remote", nullable=False)
    host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    labels_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_leases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    health_state: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False, index=True)
    health_reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _default_database_url() -> str:
    configured = os.environ.get("TESTFABRIC_DATABASE_URL")
    if configured:
        return configured
    default_path = Path.cwd() / ".testfabric" / DEFAULT_DB_FILENAME
    default_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{default_path}"


def _default_worker_heartbeat_timeout_seconds() -> float:
    configured = os.environ.get("TESTFABRIC_WORKER_HEARTBEAT_TIMEOUT_SECONDS")
    if configured is None or not str(configured).strip():
        return DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS
    try:
        timeout = float(configured)
    except Exception:
        return DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS


def _build_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database not in {":memory:", ""}:
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, future=True, connect_args=connect_args)


def _response_from_record(record: RunRecord) -> RunResponse:
    response = RunResponse.model_validate(record.response_json)
    if record.status_json:
        response = response.model_copy(update={"status": RunStatus.model_validate(record.status_json)})
    return response


def _request_from_record(record: RunRecord) -> RunRequest:
    return RunRequest.model_validate(record.request_json)


def _metadata_from_request(request: RunRequest) -> dict[str, Any]:
    metadata = {
        "team": request.team,
        "project": request.project,
        "workspace": request.workspace,
        "context": request.context,
        "priority": request.priority,
        "routing_labels": list(request.routing_labels or []),
    }
    metadata.update(dict(request.metadata or {}))
    return metadata


def _run_dir_from_request(run_id: str, request: RunRequest) -> Path | None:
    spec_payload = request.spec
    if isinstance(spec_payload, dict):
        try:
            spec = RunSpec.model_validate(spec_payload)
            return PathManager(spec, run_id).run_dir
        except Exception:
            pass
    metadata = dict(request.metadata or {})
    run_dir = str(metadata.get("run_dir") or "").strip()
    if run_dir:
        return Path(run_dir).expanduser().resolve()
    return None


def _response_for_run(run_id: str, *, status: RunStatus, message: str) -> RunResponse:
    return RunResponse(
        run_id=run_id,
        accepted=True,
        status=status,
        message=message,
        links={
            "self": f"/runs/{run_id}",
            "events": f"/runs/{run_id}/events",
            "events_stream": f"/runs/{run_id}/events?follow=true",
            "heartbeat": f"/runs/{run_id}/heartbeat",
            "cancel": f"/runs/{run_id}/cancel",
        },
    )


def _lease_from_record(record: RunRecord, *, worker_id: str | None = None, claimed_at: datetime | None = None) -> RunLease:
    response = _response_from_record(record)
    status = response.status or RunStatus(run_id=record.run_id)
    lease_metadata = dict(status.metadata)
    if worker_id:
        lease_metadata["worker_id"] = worker_id
    if claimed_at is not None:
        lease_metadata["claimed_at"] = claimed_at.isoformat()
    return RunLease(
        run_id=record.run_id,
        request=_request_from_record(record),
        response=response,
        claimed_by=worker_id,
        claimed_at=claimed_at.isoformat() if claimed_at is not None else None,
        metadata=lease_metadata,
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _duration_seconds(status: RunStatus | None) -> float | None:
    if status is None:
        return None
    started = _parse_iso_datetime(status.started_at)
    ended = _parse_iso_datetime(status.ended_at)
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started).total_seconds())


def _snapshot_from_record(record: RunRecord) -> dict[str, Any]:
    request = _request_from_record(record)
    response = _response_from_record(record)
    status = response.status
    result = dict(record.result_json or {})
    request_metadata = {
        "team": request.team,
        "project": request.project,
        "workspace": request.workspace,
        "context": request.context,
    }
    request_metadata.update(dict(request.metadata or {}))
    status_metadata = dict((status.metadata if status is not None else {}) or {})
    merged_metadata = dict(request_metadata)
    merged_metadata.update(status_metadata)
    return {
        "run_id": record.run_id,
        "accepted": record.accepted,
        "state": record.state,
        "claimed_by": record.claimed_by,
        "queued_at": record.queued_at.isoformat(),
        "claimed_at": record.claimed_at.isoformat() if record.claimed_at is not None else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "request": request.model_dump(mode="json", exclude_none=True),
        "response": response.model_dump(mode="json", exclude_none=True),
        "status": status.model_dump(mode="json", exclude_none=True) if status is not None else None,
        "result": result or None,
        "duration_seconds": _duration_seconds(status),
        "metadata": merged_metadata,
        "team": merged_metadata.get("team"),
        "project": merged_metadata.get("project"),
        "workspace": merged_metadata.get("workspace"),
        "context": merged_metadata.get("context"),
        "branch": merged_metadata.get("branch"),
        "ref": request.ref or merged_metadata.get("ref"),
        "tags": list(request.tags or []),
        "priority": _request_priority(request),
        "routing_labels": _request_routing_labels(request),
        "inputs": dict(request.inputs or {}),
        "request_metadata": request_metadata,
        "status_metadata": status_metadata,
    }


def _detail_from_record(record: RunRecord) -> dict[str, Any]:
    snapshot = _snapshot_from_record(record)
    response = dict(snapshot.get("response") or {})
    status = dict(snapshot.get("status") or {})
    result = dict(snapshot.get("result") or {})
    request = dict(snapshot.get("request") or {})
    summary = dict(result.get("summary") or {})
    links = dict(response.get("links") or {})
    links.setdefault("self", f"/runs/{record.run_id}")
    links.setdefault("events", f"/runs/{record.run_id}/events")
    links.setdefault("events_stream", f"/runs/{record.run_id}/events?follow=true")
    links.setdefault("detail", f"/runs/{record.run_id}/detail")
    return {
        "run_id": record.run_id,
        "accepted": snapshot.get("accepted"),
        "state": snapshot.get("state"),
        "verdict": status.get("verdict"),
        "message": status.get("message") or response.get("message") or result.get("error"),
        "duration_seconds": snapshot.get("duration_seconds"),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "queued_at": snapshot.get("queued_at"),
        "claimed_at": snapshot.get("claimed_at"),
        "claimed_by": snapshot.get("claimed_by"),
        "team": snapshot.get("team"),
        "project": snapshot.get("project"),
        "workspace": snapshot.get("workspace"),
        "context": snapshot.get("context"),
        "branch": snapshot.get("branch"),
        "ref": snapshot.get("ref"),
        "tags": snapshot.get("tags"),
        "inputs": snapshot.get("inputs"),
        "request": request,
        "response": response,
        "status": status,
        "result": result or None,
        "summary": summary or None,
        "metadata": snapshot.get("metadata") or {},
        "links": links,
    }


def _field_diff(name: str, left: Any, right: Any) -> dict[str, Any]:
    return {
        "field": name,
        "left": left,
        "right": right,
        "same": left == right,
    }


def _query_tags(query: RunQuery) -> list[str]:
    return [tag for tag in (query.tags or []) if str(tag or "").strip()]


def _normalized_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item:
            normalized.append(item)
    return list(dict.fromkeys(normalized))


def _request_routing_labels(request: RunRequest) -> list[str]:
    metadata = dict(request.metadata or {})
    labels = metadata.get("routing_labels")
    if isinstance(labels, list):
        return _normalized_strings(labels)
    return _normalized_strings(getattr(request, "routing_labels", []))


def _request_priority(request: RunRequest) -> int:
    metadata = dict(request.metadata or {})
    for candidate in (metadata.get("priority"), getattr(request, "priority", 0)):
        if candidate is None or candidate == "":
            continue
        try:
            return int(candidate)
        except Exception:
            continue
    return 0


def _worker_labels_from_registration(request: WorkerRegistration) -> list[str]:
    return _normalized_strings(request.labels)


def _worker_snapshot(
    record: WorkerRecord,
    *,
    freshness_timeout_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    snapshot = {
        "worker_id": record.worker_id,
        "mode": record.mode,
        "host": record.host,
        "user": record.user,
        "labels": list(record.labels_json or []),
        "capacity": int(record.capacity or 0),
        "active_leases": int(record.active_leases or 0),
        "health_state": record.health_state,
        "health_reasons": list(record.health_reasons_json or []),
        "last_heartbeat_at": _aware_datetime(record.last_heartbeat_at).isoformat() if record.last_heartbeat_at is not None else None,
        "created_at": _aware_datetime(record.created_at).isoformat(),
        "updated_at": _aware_datetime(record.updated_at).isoformat(),
        "metadata": dict(record.metadata_json or {}),
    }
    timeout = None if freshness_timeout_seconds is None else float(freshness_timeout_seconds)
    if timeout is not None and timeout > 0:
        reference = _aware_datetime(record.last_heartbeat_at) or _aware_datetime(record.created_at)
        current = _aware_datetime(now or datetime.now(timezone.utc))
        if reference is not None:
            age_seconds = max(0.0, (current - reference).total_seconds()) if current is not None else 0.0
            if age_seconds > timeout:
                health_state = str(snapshot.get("health_state") or "").strip().lower()
                if health_state not in {WORKER_STATE_UNHEALTHY, "offline"}:
                    snapshot["health_state"] = WORKER_STATE_STALE
                reasons = _normalized_strings(snapshot.get("health_reasons") or [])
                if "heartbeat_stale" not in reasons:
                    reasons.append("heartbeat_stale")
                snapshot["health_reasons"] = reasons
                metadata = dict(snapshot.get("metadata") or {})
                metadata["heartbeat_age_seconds"] = round(age_seconds, 3)
                metadata["heartbeat_stale_after_seconds"] = timeout
                snapshot["metadata"] = metadata
    return snapshot


def _worker_status_from_record(
    record: WorkerRecord,
    *,
    freshness_timeout_seconds: float | None = None,
    now: datetime | None = None,
) -> WorkerStatus:
    snapshot = _worker_snapshot(record, freshness_timeout_seconds=freshness_timeout_seconds, now=now)
    return WorkerStatus.model_validate(snapshot)


def _worker_is_available(
    record: WorkerRecord,
    *,
    freshness_timeout_seconds: float | None = None,
    now: datetime | None = None,
) -> bool:
    snapshot = _worker_snapshot(record, freshness_timeout_seconds=freshness_timeout_seconds, now=now)
    health_state = str(snapshot.get("health_state") or "").strip().lower()
    return health_state not in {WORKER_STATE_UNHEALTHY, WORKER_STATE_STALE} and int(snapshot.get("active_leases") or 0) < int(snapshot.get("capacity") or 0)


def _claim_worker_record(
    session: Session,
    *,
    worker_id: str,
    labels: list[str] | None = None,
    mode: str | None = None,
    host: str | None = None,
    user: str | None = None,
    capacity: int | None = None,
    health_state: str | None = None,
    health_reasons: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkerRecord:
    now = datetime.now(timezone.utc)
    record = session.get(WorkerRecord, worker_id)
    if record is None:
        record = WorkerRecord(
            worker_id=worker_id,
            created_at=now,
            labels_json=[],
            health_reasons_json=[],
            metadata_json={},
            active_leases=0,
            capacity=1,
            health_state="healthy",
            updated_at=now,
        )
    if mode is not None:
        record.mode = mode
    if host is not None:
        record.host = host
    if user is not None:
        record.user = user
    if labels is not None:
        record.labels_json = _normalized_strings(labels)
    if capacity is not None:
        record.capacity = max(1, int(capacity))
    if health_state is not None:
        record.health_state = str(health_state or "healthy").strip() or "healthy"
    if health_reasons is not None:
        record.health_reasons_json = _normalized_strings(health_reasons)
    if metadata is not None:
        record.metadata_json = dict(metadata)
    record.updated_at = now
    session.add(record)
    return record


def _release_worker_lease(record: WorkerRecord, *, reason: str | None = None) -> None:
    record.active_leases = max(0, int(record.active_leases or 0) - 1)
    metadata = dict(record.metadata_json or {})
    if reason:
        metadata["last_release_reason"] = reason
    metadata["active_leases"] = record.active_leases
    record.metadata_json = metadata
    record.updated_at = datetime.now(timezone.utc)


def _claim_policy_sort_key(
    snapshot: dict[str, Any],
    *,
    policy: str,
    worker_labels: list[str],
) -> tuple[Any, ...]:
    queued_dt = _parse_iso_datetime(str(snapshot.get("queued_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)
    created_dt = _parse_iso_datetime(str(snapshot.get("created_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)
    run_id = str(snapshot.get("run_id") or "")
    priority = int(snapshot.get("priority") or 0)
    routing_labels = _normalized_strings(snapshot.get("routing_labels") or [])
    label_score = len(set(routing_labels).intersection(worker_labels)) if worker_labels else len(routing_labels)

    if policy == "lifo":
        return (-queued_dt.timestamp(), -created_dt.timestamp(), run_id)
    if policy == "priority":
        return (-priority, queued_dt.timestamp(), created_dt.timestamp(), run_id)
    if policy == "affinity":
        return (-label_score, -priority, queued_dt.timestamp(), created_dt.timestamp(), run_id)
    return (queued_dt.timestamp(), created_dt.timestamp(), run_id)


def _is_eligible_for_worker(snapshot: dict[str, Any], worker_labels: list[str]) -> bool:
    if not worker_labels:
        return True
    request = dict(snapshot.get("request") or {})
    required_labels = set(_normalized_strings(request.get("routing_labels") or []))
    if not required_labels:
        return True
    return required_labels.issubset(set(worker_labels))


def _matches_run_query(snapshot: dict[str, Any], query: RunQuery) -> bool:
    if query.team and snapshot.get("team") != query.team:
        return False
    if query.project and snapshot.get("project") != query.project:
        return False
    if query.workspace and snapshot.get("workspace") != query.workspace:
        return False
    if query.branch and snapshot.get("branch") != query.branch:
        return False
    if query.ref and snapshot.get("ref") != query.ref:
        return False
    if query.target_id or query.target_name:
        return False

    if query.status:
        allowed = {str(item).strip() for item in query.status if str(item).strip()}
        state = str(snapshot.get("state") or "").strip()
        verdict = str(snapshot.get("verdict") or "").strip()
        if state not in allowed and verdict not in allowed:
            return False

    tags = list(snapshot.get("tags") or [])
    wanted_tags = _query_tags(query)
    if wanted_tags and not all(tag in tags for tag in wanted_tags):
        return False
    return True


def _filter_records(
    records: list[RunRecord],
    query: RunQuery,
) -> list[tuple[RunRecord, dict[str, Any]]]:
    snapshots = [_snapshot_from_record(record) for record in records]
    return [
        (record, snapshot)
        for record, snapshot in zip(records, snapshots, strict=False)
        if _matches_run_query(snapshot, query)
    ]


def _count_by(values: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip()
        if not key:
            key = "-"
        counts[key] = counts.get(key, 0) + 1
    return counts


class SqliteRunStore:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        worker_heartbeat_timeout_seconds: float | None = None,
    ) -> None:
        self.database_url = database_url or _default_database_url()
        self.worker_heartbeat_timeout_seconds = (
            _default_worker_heartbeat_timeout_seconds()
            if worker_heartbeat_timeout_seconds is None
            else float(worker_heartbeat_timeout_seconds)
        )
        self._engine = _build_engine(self.database_url)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def _session(self) -> Session:
        return self._session_factory()

    def create_run(self, request: RunRequest) -> RunResponse:
        run_id = str(request.run_id or "").strip() or f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        run_dir = _run_dir_from_request(run_id, request)
        status = RunStatus(
            run_id=run_id,
            state="queued",
            message="accepted for execution",
            progress={"queued": True},
            metadata={
                **_metadata_from_request(request),
                **({"run_dir": str(run_dir)} if run_dir is not None else {}),
            },
        )
        response = _response_for_run(run_id, status=status, message="accepted for execution")
        request_json = request.model_dump(mode="json", exclude_none=True)
        response_json = response.model_dump(mode="json", exclude_none=True)
        status_json = status.model_dump(mode="json", exclude_none=True)

        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                record = RunRecord(run_id=run_id, created_at=now, queued_at=now)
            record.accepted = True
            record.state = "queued"
            record.claimed_by = None
            record.queued_at = now
            record.claimed_at = None
            record.request_json = request_json
            record.response_json = response_json
            record.status_json = status_json
            record.result_json = None
            record.updated_at = now
            session.add(record)
            session.commit()

        return response

    def register_worker(self, request: WorkerRegistration) -> WorkerStatus:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            record = _claim_worker_record(
                session,
                worker_id=request.worker_id,
                labels=_worker_labels_from_registration(request),
                mode=request.mode,
                host=request.host,
                user=request.user,
                capacity=request.capacity,
                health_state=request.health_state,
                health_reasons=request.health_reasons,
                metadata=request.metadata,
            )
            record.updated_at = now
            session.commit()
            return _worker_status_from_record(record, freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds, now=now)

    def heartbeat_worker(self, worker_id: str, *, metadata: dict[str, Any] | None = None) -> WorkerStatus | None:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(WorkerRecord, worker_id)
            if record is None:
                return None
            merged_metadata = dict(record.metadata_json or {})
            merged_metadata.update(dict(metadata or {}))
            record.metadata_json = merged_metadata
            record.last_heartbeat_at = now
            record.updated_at = now
            session.add(record)
            session.commit()
            return _worker_status_from_record(record, freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds, now=now)

    def get_worker(self, worker_id: str) -> WorkerStatus | None:
        with self._session() as session:
            record = session.get(WorkerRecord, worker_id)
            if record is None:
                return None
            return _worker_status_from_record(record, freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds)

    def list_workers(self) -> list[WorkerStatus]:
        with self._session() as session:
            stmt = select(WorkerRecord).order_by(WorkerRecord.created_at.asc(), WorkerRecord.worker_id.asc())
            records = session.scalars(stmt).all()
            return [_worker_status_from_record(record, freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds) for record in records]

    def claim_next_run(
        self,
        worker_id: str | None = None,
        *,
        worker_labels: list[str] | None = None,
        policy: str = DEFAULT_QUEUE_POLICY,
    ) -> RunLease | None:
        now = datetime.now(timezone.utc)
        normalized_worker_labels = _normalized_strings(worker_labels or [])
        with self._session() as session:
            worker_record: WorkerRecord | None = None
            if worker_id:
                worker_record = _claim_worker_record(
                    session,
                    worker_id=worker_id,
                    labels=normalized_worker_labels or None,
                )
                if not _worker_is_available(
                    worker_record,
                    freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds,
                    now=now,
                ):
                    raise RuntimeError(f"Worker unavailable: {worker_id}")

            stmt = (
                select(RunRecord)
                .where(RunRecord.state == "queued")
                .order_by(RunRecord.queued_at.asc(), RunRecord.created_at.asc(), RunRecord.run_id.asc())
            )
            records = session.scalars(stmt).all()
            if not records:
                return None

            eligible: list[tuple[RunRecord, dict[str, Any]]] = []
            for record in records:
                snapshot = _snapshot_from_record(record)
                if _is_eligible_for_worker(snapshot, normalized_worker_labels):
                    eligible.append((record, snapshot))

            if not eligible:
                return None

            policy_name = str(policy or DEFAULT_QUEUE_POLICY).strip().lower()
            selected_record, _selected_snapshot = sorted(
                eligible,
                key=lambda item: _claim_policy_sort_key(item[1], policy=policy_name, worker_labels=normalized_worker_labels),
            )[0]

            response = _response_from_record(selected_record)
            status = response.status or RunStatus(run_id=selected_record.run_id)
            metadata = dict(status.metadata)
            if worker_id:
                metadata["worker_id"] = worker_id
                metadata["claimed_by"] = worker_id
            metadata["claimed_at"] = now.isoformat()
            if normalized_worker_labels:
                metadata["worker_labels"] = normalized_worker_labels
            metadata["scheduler_policy"] = policy_name

            status = status.model_copy(
                update={
                    "state": "running",
                    "message": f"claimed for execution by {worker_id}" if worker_id else "claimed for execution",
                    "started_at": status.started_at or now.isoformat(),
                    "heartbeat_at": now.isoformat(),
                    "metadata": metadata,
                    "progress": {**status.progress, "queued": False, "running": True},
                }
            )
            response = _response_for_run(
                selected_record.run_id,
                status=status,
                message=status.message or "claimed for execution",
            )
            selected_record.state = "running"
            selected_record.claimed_by = worker_id
            selected_record.claimed_at = now
            selected_record.response_json = response.model_dump(mode="json", exclude_none=True)
            selected_record.status_json = status.model_dump(mode="json", exclude_none=True)
            selected_record.updated_at = now
            if worker_record is not None:
                worker_record.active_leases = max(0, int(worker_record.active_leases or 0)) + 1
                worker_record.last_heartbeat_at = now
                worker_metadata = dict(worker_record.metadata_json or {})
                worker_metadata.update(
                    {
                        "last_claimed_run_id": selected_record.run_id,
                        "last_claim_policy": policy_name,
                        "last_claimed_at": now.isoformat(),
                    }
                )
                if normalized_worker_labels:
                    worker_metadata["worker_labels"] = normalized_worker_labels
                worker_record.metadata_json = worker_metadata
                worker_record.updated_at = now
            session.add(selected_record)
            if worker_record is not None:
                session.add(worker_record)
            session.commit()

            lease = _lease_from_record(selected_record, worker_id=worker_id, claimed_at=now)
            if normalized_worker_labels:
                lease.metadata["worker_labels"] = normalized_worker_labels
            lease.metadata["scheduler_policy"] = policy_name
            if worker_record is not None:
                worker_status = _worker_status_from_record(
                    worker_record,
                    freshness_timeout_seconds=self.worker_heartbeat_timeout_seconds,
                    now=now,
                )
                lease.metadata["worker_capacity"] = worker_status.capacity
                lease.metadata["worker_active_leases"] = worker_status.active_leases
                lease.metadata["worker_health_state"] = worker_status.health_state
            return lease

    def heartbeat_run(self, run_id: str, *, worker_id: str | None = None) -> RunResponse | None:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None

            response = _response_from_record(record)
            status = response.status or RunStatus(run_id=run_id)
            if status.state not in {"queued", "running"}:
                return response

            metadata = dict(status.metadata)
            if worker_id:
                metadata["worker_id"] = worker_id
            metadata["last_heartbeat_at"] = now.isoformat()
            status = status.model_copy(
                update={
                    "heartbeat_at": now.isoformat(),
                    "metadata": metadata,
                    "progress": {**status.progress, "heartbeat": True},
                }
            )
            response = _response_for_run(
                run_id,
                status=status,
                message=status.message or "heartbeat received",
            )
            record.response_json = response.model_dump(mode="json", exclude_none=True)
            record.status_json = status.model_dump(mode="json", exclude_none=True)
            record.updated_at = now
            if worker_id:
                worker_record = session.get(WorkerRecord, worker_id)
                if worker_record is not None:
                    worker_record.last_heartbeat_at = now
                    worker_metadata = dict(worker_record.metadata_json or {})
                    worker_metadata["last_run_heartbeat_at"] = now.isoformat()
                    worker_record.metadata_json = worker_metadata
                    worker_record.updated_at = now
                    session.add(worker_record)
            session.add(record)
            session.commit()
            return response

    def cancel_run(self, run_id: str, *, reason: str | None = None, worker_id: str | None = None) -> RunResponse | None:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None

            response = _response_from_record(record)
            status = response.status or RunStatus(run_id=run_id)
            if status.state == "canceled":
                return response
            if status.state == "completed":
                return response

            metadata = dict(status.metadata)
            if worker_id:
                metadata["worker_id"] = worker_id
            if reason:
                metadata["canceled_reason"] = reason
            metadata["canceled_at"] = now.isoformat()
            status = status.model_copy(
                update={
                    "state": "canceled",
                    "verdict": status.verdict or "CANCELED",
                    "message": reason or status.message or "canceled",
                    "ended_at": now.isoformat(),
                    "heartbeat_at": now.isoformat(),
                    "metadata": metadata,
                    "progress": {**status.progress, "canceled": True, "running": False, "queued": False},
                }
            )
            response = _response_for_run(
                run_id,
                status=status,
                message=status.message or "canceled",
            )
            record.state = "canceled"
            record.accepted = True
            record.response_json = response.model_dump(mode="json", exclude_none=True)
            record.status_json = status.model_dump(mode="json", exclude_none=True)
            record.updated_at = now
            if record.claimed_by:
                worker_record = session.get(WorkerRecord, record.claimed_by)
                if worker_record is not None:
                    _release_worker_lease(worker_record, reason="run canceled")
                    session.add(worker_record)
            session.add(record)
            session.commit()
            return response

    def complete_run(self, run_id: str, result: RunResult) -> RunResponse | None:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            if record.state in {"canceled", "completed", "failed"}:
                return _response_from_record(record)

            current_response = _response_from_record(record)
            current_status = current_response.status or RunStatus(run_id=run_id)
            status = result.status or RunStatus(run_id=run_id)
            metadata = dict(current_status.metadata)
            metadata.update(dict(status.metadata))
            metadata.update(dict(result.metadata))
            if record.claimed_by:
                metadata.setdefault("worker_id", record.claimed_by)
            metadata["completed_at"] = now.isoformat()
            metadata["result_ok"] = bool(result.ok)
            status = current_status.model_copy(
                update={
                    "state": result.state,
                    "verdict": result.verdict or status.verdict or current_status.verdict,
                    "message": result.error or status.message or current_status.message or ("completed" if result.ok else "failed"),
                    "started_at": current_status.started_at or status.started_at,
                    "ended_at": status.ended_at or current_status.ended_at or now.isoformat(),
                    "metadata": metadata,
                    "progress": {
                        **current_status.progress,
                        **status.progress,
                        "completed": bool(result.ok),
                        "running": False,
                        "queued": False,
                    },
                }
            )
            response = _response_for_run(
                run_id,
                status=status,
                message=status.message or ("completed" if result.ok else "failed"),
            )
            record.state = result.state
            record.accepted = True
            record.response_json = response.model_dump(mode="json", exclude_none=True)
            record.status_json = status.model_dump(mode="json", exclude_none=True)
            record.result_json = result.model_dump(mode="json", exclude_none=True)
            record.updated_at = now
            if record.claimed_by:
                worker_record = session.get(WorkerRecord, record.claimed_by)
                if worker_record is not None:
                    _release_worker_lease(worker_record, reason=f"run {result.state}")
                    session.add(worker_record)
            session.add(record)
            session.commit()

            return response

    def compare_runs(self, left_run_id: str, right_run_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            left = session.get(RunRecord, left_run_id)
            right = session.get(RunRecord, right_run_id)
            if left is None or right is None:
                return None

            left_snapshot = _snapshot_from_record(left)
            right_snapshot = _snapshot_from_record(right)
            left_status = left_snapshot.get("status") or {}
            right_status = right_snapshot.get("status") or {}
            left_metadata = dict(left_snapshot.get("metadata") or {})
            right_metadata = dict(right_snapshot.get("metadata") or {})

            fields = [
                _field_diff("state", left_snapshot.get("state"), right_snapshot.get("state")),
                _field_diff("verdict", left_status.get("verdict"), right_status.get("verdict")),
                _field_diff("team", left_metadata.get("team"), right_metadata.get("team")),
                _field_diff("project", left_metadata.get("project"), right_metadata.get("project")),
                _field_diff("context", left_metadata.get("context"), right_metadata.get("context")),
                _field_diff("tags", left_snapshot.get("tags"), right_snapshot.get("tags")),
                _field_diff("inputs", left_snapshot.get("inputs"), right_snapshot.get("inputs")),
                _field_diff("duration_seconds", left_snapshot.get("duration_seconds"), right_snapshot.get("duration_seconds")),
            ]
            summary = {
                "same_state": left_snapshot.get("state") == right_snapshot.get("state"),
                "same_verdict": left_status.get("verdict") == right_status.get("verdict"),
                "same_team": left_metadata.get("team") == right_metadata.get("team"),
                "same_project": left_metadata.get("project") == right_metadata.get("project"),
                "duration_delta_seconds": None,
            }
            left_duration = left_snapshot.get("duration_seconds")
            right_duration = right_snapshot.get("duration_seconds")
            if isinstance(left_duration, (int, float)) and isinstance(right_duration, (int, float)):
                summary["duration_delta_seconds"] = float(right_duration) - float(left_duration)

            return {
                "left_run_id": left_run_id,
                "right_run_id": right_run_id,
                "left": left_snapshot,
                "right": right_snapshot,
                "fields": fields,
                "summary": summary,
            }

    def replay_run(self, request: ReplayRequest) -> RunResponse | None:
        with self._session() as session:
            source = session.get(RunRecord, request.run_id)
            if source is None:
                return None
            source_request = _request_from_record(source)

        replayed_request = source_request.model_copy(deep=True)
        overrides = dict(request.overrides or {})
        replay_run_id: str | None = None
        if "run_id" in overrides:
            replay_run_id = str(overrides.pop("run_id") or "").strip() or None
        if "mode" in overrides:
            replayed_request.mode = str(overrides.pop("mode") or replayed_request.mode)
        for field in ("spec_yaml", "spec_path", "spec", "repo_url", "ref", "team", "project", "workspace", "context"):
            if field in overrides:
                setattr(replayed_request, field, overrides.pop(field))
        if "inputs" in overrides and isinstance(overrides["inputs"], dict):
            merged_inputs = dict(replayed_request.inputs or {})
            merged_inputs.update(overrides.pop("inputs"))
            replayed_request.inputs = merged_inputs
        if "tags" in overrides and isinstance(overrides["tags"], list):
            merged_tags = list(replayed_request.tags or [])
            merged_tags.extend(str(tag) for tag in overrides.pop("tags"))
            replayed_request.tags = list(dict.fromkeys(merged_tags))
        if "metadata" in overrides and isinstance(overrides["metadata"], dict):
            merged_metadata = dict(replayed_request.metadata or {})
            merged_metadata.update(overrides.pop("metadata"))
            replayed_request.metadata = merged_metadata

        replay_metadata = dict(replayed_request.metadata or {})
        replay_metadata.update(
            {
                "replayed_from": request.run_id,
                "replay_mode": request.mode,
                "replay_stage_id": request.stage_id,
                "replay_job_id": request.job_id,
                "replay_local": bool(request.local),
                "replay_overrides": overrides,
            }
        )
        replayed_request.metadata = replay_metadata
        if request.local:
            replayed_request.context = "local"
        replayed_request.run_id = replay_run_id
        return self.create_run(replayed_request)

    def list_queued_runs(self) -> list[RunResponse]:
        with self._session() as session:
            stmt = (
                select(RunRecord)
                .where(RunRecord.state == "queued")
                .order_by(RunRecord.queued_at.asc(), RunRecord.created_at.asc(), RunRecord.run_id.asc())
            )
            records = session.scalars(stmt).all()
            return [_response_from_record(record) for record in records]

    def get_run(self, run_id: str) -> RunResponse | None:
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            return _response_from_record(record)

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            return _detail_from_record(record)

    def _load_run_events_snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            request = _request_from_record(record)
            run_dir = _run_dir_from_request(run_id, request)
            if run_dir is None:
                status = _response_from_record(record).status
                if status is not None:
                    run_dir = Path(str((status.metadata or {}).get("run_dir") or "")).expanduser()
            if run_dir is None:
                return {
                    "run_id": run_id,
                    "state": record.state,
                    "events_path": None,
                    "items": [],
                    "total": 0,
                }

        events_path = Path(run_dir) / DEFAULT_EVENTS_FILENAME
        items: list[dict[str, Any]] = []
        if events_path.exists():
            try:
                raw_lines = events_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                raw_lines = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if isinstance(event, dict):
                    items.append(event)

        with self._session() as session:
            record = session.get(RunRecord, run_id)
            state = record.state if record is not None else RUN_STATE_RUNNING

        return {
            "run_id": run_id,
            "state": state,
            "events_path": events_path,
            "items": items,
            "total": len(items),
        }

    def get_run_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_EVENT_LIMIT,
        follow: bool = False,
        wait_timeout_seconds: float = DEFAULT_EVENT_FOLLOW_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_EVENT_FOLLOW_POLL_INTERVAL_SECONDS,
    ) -> dict[str, Any] | None:
        start = max(0, int(offset or 0))
        size = max(0, int(limit or 0))
        deadline = time.monotonic() + max(0.0, float(wait_timeout_seconds or 0.0))

        while True:
            snapshot = self._load_run_events_snapshot(run_id)
            if snapshot is None:
                return None

            items = list(snapshot.get("items") or [])
            state = str(snapshot.get("state") or "").strip()
            terminal = state in {RUN_STATE_COMPLETED, RUN_STATE_FAILED, RUN_STATE_CANCELED, RUN_STATE_DRY_RUN}
            has_new_items = len(items) > start
            if has_new_items or not follow or terminal or time.monotonic() >= deadline:
                sliced = items[start:]
                if size:
                    sliced = sliced[:size]
                next_offset = start + len(sliced)
                payload = {
                    "run_id": run_id,
                    "path": str(snapshot.get("events_path") or Path(DEFAULT_EVENTS_FILENAME)),
                    "total": len(items),
                    "items": sliced,
                    "next_offset": next_offset,
                    "state": state,
                }
                if follow:
                    payload["follow"] = True
                return payload

            time.sleep(max(0.0, float(poll_interval_seconds or 0.0)))

    def _artifact_root_for_run(self, run_id: str) -> Path | None:
        with self._session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            request = _request_from_record(record)
            run_dir = _run_dir_from_request(run_id, request)
            if run_dir is None:
                status = _response_from_record(record).status
                if status is not None:
                    run_dir = Path(str((status.metadata or {}).get("run_dir") or "")).expanduser()
            if run_dir is None:
                return None
            return Path(run_dir).expanduser().resolve()

    def _resolve_artifact_target(self, run_dir: Path, path: str | None) -> Path | None:
        requested = str(path or "").strip()
        if not requested or requested == ".":
            return run_dir
        candidate = Path(requested).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=False)
        else:
            resolved = (run_dir / candidate).resolve(strict=False)
        try:
            resolved.relative_to(run_dir)
        except Exception:
            return None
        return resolved

    def _artifact_file_payload(self, run_id: str, file_path: Path, *, root: Path) -> dict[str, Any]:
        resolved = file_path.resolve(strict=False)
        rel_path = resolved.relative_to(root).as_posix()
        stat = resolved.stat()
        preview_bytes = resolved.read_bytes()[:DEFAULT_ARTIFACT_PREVIEW_BYTES]
        is_text = b"\x00" not in preview_bytes
        text = ""
        if is_text:
            try:
                text = preview_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = preview_bytes.decode("utf-8", errors="replace")
        preview_lines = text.splitlines()
        mime_type, _ = mimetypes.guess_type(resolved.name)
        return {
            "run_id": run_id,
            "path": rel_path,
            "kind": "file",
            "name": resolved.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "mime_type": mime_type,
            "is_text": is_text,
            "truncated": stat.st_size > DEFAULT_ARTIFACT_PREVIEW_BYTES,
            "preview": preview_lines[:DEFAULT_ARTIFACT_PREVIEW_LINES],
            "content": text if is_text and stat.st_size <= DEFAULT_ARTIFACT_PREVIEW_BYTES else None,
        }

    def _artifact_search_matches(self, file_path: Path, *, root: Path, query: str) -> list[dict[str, Any]]:
        pattern = str(query or "").strip()
        if not pattern:
            return []

        resolved = file_path.resolve(strict=False)
        try:
            raw = resolved.read_text(encoding="utf-8")
        except Exception:
            return []

        rel_path = resolved.relative_to(root).as_posix()
        stat = resolved.stat()
        matches: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if pattern.lower() not in line.lower():
                continue
            matches.append(
                {
                    "path": rel_path,
                    "name": resolved.name,
                    "kind": "match",
                    "line_number": line_number,
                    "line": line,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "size_bytes": stat.st_size,
                }
            )
        return matches

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        path: str | None = None,
        recursive: bool = True,
        limit: int = DEFAULT_ARTIFACT_BROWSER_LIMIT,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any] | None:
        root = self._artifact_root_for_run(run_id)
        if root is None:
            return None
        target = self._resolve_artifact_target(root, path)
        if target is None or not target.exists():
            return None
        if target.is_file():
            if search:
                matches = self._artifact_search_matches(target, root=root, query=search)
                start = max(0, int(offset or 0))
                size = max(0, int(limit or 0))
                sliced = matches[start:]
                if size:
                    sliced = sliced[:size]
                next_offset = start + len(sliced)
                return {
                    "run_id": run_id,
                    "path": target.relative_to(root).as_posix(),
                    "kind": "search",
                    "query": search,
                    "total": len(matches),
                    "items": sliced,
                    "next_offset": next_offset,
                    "truncated": next_offset < len(matches),
                }
            return self._artifact_file_payload(run_id, target, root=root)

        base = target
        if search:
            matches: list[dict[str, Any]] = []
            iterator = base.rglob("*") if recursive else base.iterdir()
            for item in sorted(iterator):
                if item == base or not item.exists() or not item.is_file():
                    continue
                matches.extend(self._artifact_search_matches(item, root=root, query=search))
            start = max(0, int(offset or 0))
            size = max(0, int(limit or 0))
            sliced = matches[start:]
            if size:
                sliced = sliced[:size]
            next_offset = start + len(sliced)
            return {
                "run_id": run_id,
                "path": "." if base == root else base.relative_to(root).as_posix(),
                "kind": "search",
                "query": search,
                "total": len(matches),
                "items": sliced,
                "next_offset": next_offset,
                "truncated": next_offset < len(matches),
            }
        entries: list[dict[str, Any]] = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for item in sorted(iterator):
            if item == base:
                continue
            if not item.exists():
                continue
            rel = item.relative_to(root)
            depth = max(len(rel.parts) - 1, 0)
            stat = item.stat()
            entry = {
                "path": rel.as_posix(),
                "name": item.name,
                "kind": "dir" if item.is_dir() else "file",
                "depth": depth,
                "size_bytes": None if item.is_dir() else stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
            entries.append(entry)

        start = max(0, int(offset or 0))
        size = max(0, int(limit or 0))
        sliced = entries[start:]
        if size:
            sliced = sliced[:size]
        next_offset = start + len(sliced)
        return {
            "run_id": run_id,
            "path": "." if base == root else base.relative_to(root).as_posix(),
            "kind": "tree",
            "total": len(entries),
            "items": sliced,
            "next_offset": next_offset,
            "truncated": next_offset < len(entries),
        }

    def list_runs(self) -> list[RunResponse]:
        with self._session() as session:
            stmt = select(RunRecord).order_by(RunRecord.created_at.asc(), RunRecord.run_id.asc())
            records = session.scalars(stmt).all()
            return [_response_from_record(record) for record in records]

    def query_runs(self, query: RunQuery | None = None) -> dict[str, Any]:
        query = query or RunQuery()
        with self._session() as session:
            stmt = select(RunRecord).order_by(RunRecord.created_at.asc(), RunRecord.run_id.asc())
            records = session.scalars(stmt).all()
        matched = _filter_records(records, query)
        filtered = list(matched)

        offset = max(0, int(query.offset or 0))
        limit = max(0, int(query.limit or 0))
        if offset:
            filtered = filtered[offset:]
        if limit:
            filtered = filtered[:limit]

        payload_items = [_response_from_record(record).model_dump(exclude_none=True) for record, _ in filtered]
        return {
            "total": len(matched),
            "items": payload_items,
        }

    def as_payload(self) -> dict[str, Any]:
        return self.query_runs()

    def query_queue(self, query: RunQuery | None = None) -> dict[str, Any]:
        query = query or RunQuery(status=["queued"])
        with self._session() as session:
            stmt = (
                select(RunRecord)
                .where(RunRecord.state == "queued")
                .order_by(RunRecord.queued_at.asc(), RunRecord.created_at.asc(), RunRecord.run_id.asc())
            )
            records = session.scalars(stmt).all()
        matched = _filter_records(records, query)
        filtered = list(matched)
        payload_items = [_response_from_record(record).model_dump(exclude_none=True) for record, _ in filtered]
        return {
            "total": len(matched),
            "items": payload_items,
        }

    def queue_payload(self) -> dict[str, Any]:
        return self.query_queue()

    def dashboard(self, query: RunQuery | None = None) -> dict[str, Any]:
        query = query or RunQuery()
        with self._session() as session:
            stmt = select(RunRecord).order_by(RunRecord.created_at.asc(), RunRecord.run_id.asc())
            records = session.scalars(stmt).all()
        matched = _filter_records(records, query)
        snapshots = [snapshot for _, snapshot in matched]
        recent_limit = max(0, int(query.limit or 0))
        recent = list(reversed(matched[-recent_limit:])) if recent_limit else list(reversed(matched))

        recent_runs: list[dict[str, Any]] = []
        for record, snapshot in recent:
            status = dict(snapshot.get("status") or {})
            recent_runs.append(
                {
                    "run_id": record.run_id,
                    "state": snapshot.get("state"),
                    "verdict": status.get("verdict"),
                    "message": status.get("message") or snapshot.get("response", {}).get("message"),
                    "team": snapshot.get("team"),
                    "project": snapshot.get("project"),
                    "workspace": snapshot.get("workspace"),
                    "duration_seconds": snapshot.get("duration_seconds"),
                    "created_at": snapshot.get("created_at"),
                    "claimed_by": snapshot.get("claimed_by"),
                }
            )

        return {
            "total": len(matched),
            "counts_by_state": _count_by(snapshot.get("state") for snapshot in snapshots),
            "counts_by_verdict": _count_by((dict(snapshot.get("status") or {}).get("verdict") for snapshot in snapshots)),
            "counts_by_team": _count_by(snapshot.get("team") for snapshot in snapshots),
            "counts_by_project": _count_by(snapshot.get("project") for snapshot in snapshots),
            "recent_runs": recent_runs,
        }
