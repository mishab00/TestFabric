# testfabric/reporting/stage_reporter.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testfabric.core.context import StageContext
from testfabric.core.serialize import to_dict_like
from testfabric.artifacts.collector import ArtifactCollector
from testfabric.orchestrator.dispatch.models import DispatchResult


class StageReporter:
    """
    Canonical stage reporting contract.

    Canonical staging location (worker tmp):
      <workers_tmp>/<run_id>/<worker_id>/stages/<stage_slug>/
        logs/
        jobs/
        reports/
        stage-summary.json

    ArtifactStore mirrors this whole folder into:
      <run_dir>/workers/<worker_id>/stages/<stage_slug>/...
    """

    def __init__(self, collector: ArtifactCollector | None = None) -> None:
        self.collector = collector or ArtifactCollector()

    def _stage_root(self, ctx: StageContext) -> Path:
        return ctx.run.paths.worker_stage_dir(ctx.worker_id, ctx.stage_ref)

    def _logs_dir(self, ctx: StageContext) -> Path:
        return ctx.run.paths.worker_stage_logs_dir(ctx.worker_id, ctx.stage_ref)

    def _jobs_dir(self, ctx: StageContext) -> Path:
        return ctx.run.paths.worker_stage_jobs_dir(ctx.worker_id, ctx.stage_ref)

    def _stage_summary_path(self, ctx: StageContext) -> Path:
        return self._stage_root(ctx) / "stage-summary.json"

    def _stage_summary_rel(self, ctx: StageContext) -> str:
        return f"workers/{ctx.worker_id}/stages/{ctx.stage_slug}/stage-summary.json"

    def _reports_rel(self, ctx: StageContext) -> str:
        return f"workers/{ctx.worker_id}/stages/{ctx.stage_slug}/reports"

    def _stage_root_rel(self, ctx: StageContext) -> str:
        return f"workers/{ctx.worker_id}/stages/{ctx.stage_slug}"

    def _target_kind(self, ctx: StageContext) -> str:
        target_kind = str(ctx.target_kind or "").strip().lower()
        if target_kind:
            return target_kind
        executor = str(ctx.executor or "").strip().lower()
        if executor in {"local", "docker", "ssh"}:
            return executor
        return executor or "unknown"

    def _target_scope(self, ctx: StageContext) -> str | None:
        group = str(ctx.target_group or "").strip()
        address = str(ctx.target_address or "").strip()
        if address:
            return address
        if group:
            return f"group:{group}"
        return None

    def _target_display(self, ctx: StageContext) -> str:
        name = str(ctx.target_name or ctx.target_id or "target").strip() or "target"
        kind = self._target_kind(ctx)
        scope = self._target_scope(ctx)
        labels = dict(ctx.target_labels or {})
        extras = []
        if scope:
            extras.append(scope)
        if labels:
            extras.append(",".join(f"{key}={value}" for key, value in sorted(labels.items())))
        if extras:
            return f"{name} [{kind}; {'; '.join(extras)}]"
        if kind and kind != name:
            return f"{name} [{kind}]"
        return name

    def _artifact_label(self, item: dict[str, Any]) -> str:
        category = str(item.get("category") or "").strip().lower()
        path = str(item.get("path") or "").strip()
        name = Path(path).name if path else ""
        if category == "junit":
            return "JUnit XML"
        if category == "html":
            return "HTML Report"
        if category == "artifact":
            return f"Artifact: {name}" if name else "Artifact"
        if category == "log":
            return f"Log: {name}" if name else "Log"
        return name or "File"

    def _collect_artifacts(self, ctx: StageContext) -> dict[str, Any]:
        manifest = self.collector.collect_stage(ctx)
        items = [to_dict_like(item) for item in list(manifest.items or [])]
        stage_root_rel = self._stage_root_rel(ctx)
        primary = [
            item["path"] if str(item.get("path") or "").startswith("workers/") else f"{stage_root_rel}/{item['path']}"
            for item in items
            if str(item.get("category") or "") != "log"
        ]
        highlights = [
            {
                "path": item["path"] if str(item.get("path") or "").startswith("workers/") else f"{stage_root_rel}/{item['path']}",
                "category": str(item.get("category") or ""),
                "source": str(item.get("source") or ""),
                "label": self._artifact_label(item),
                "job_id": item.get("job_id"),
                "attempt": item.get("attempt"),
            }
            for item in items
            if str(item.get("category") or "") != "log"
        ]
        return {
            "files_total": len(primary),
            "counts_by_category": dict(manifest.counts_by_category or {}),
            "counts_by_source": dict(manifest.counts_by_source or {}),
            "primary": primary[:50],
            "highlights": highlights[:50],
        }

    def _classify_job_failure(self, ctx: StageContext, result: dict[str, Any], *, stderr_hint: str = "") -> str | None:
        timed_out = bool(result.get("timed_out", False))
        if timed_out:
            return "timeout"

        error = str(result.get("error") or "").strip()
        exit_code = result.get("exit_code")
        try:
            exit_code_num = int(exit_code) if exit_code is not None else 0
        except (TypeError, ValueError):
            exit_code_num = 0

        if error or exit_code_num == 99:
            return "infra"

        if exit_code_num != 0:
            hint = stderr_hint.lower()
            if "command not found" in hint or "no such file or directory" in hint:
                return "infra"
            if exit_code_num == 127:
                return "infra"
            if str(ctx.runner or "").strip().lower() == "pytest":
                return "runner"
            return "execution"

        return None

    def _derive_stage_failure_type(self, payload: dict[str, Any]) -> str | None:
        failures = [
            str(job.get("failure_type") or "").strip()
            for job in list(payload.get("jobs") or [])
            if str(job.get("failure_type") or "").strip()
        ]
        if not failures:
            return None
        priority = {"infra": 0, "timeout": 1, "runner": 2, "execution": 3}
        return sorted(failures, key=lambda item: priority.get(item, 99))[0]

    def _job_payload(self, ctx: StageContext, result: dict[str, Any]) -> dict[str, Any]:
        job_id = str(result.get("job_id") or "").strip()
        attempt = int(result.get("attempt") or 0)
        job_root_rel = f"{self._stage_root_rel(ctx)}/jobs/{job_id}/attempt-{attempt}" if job_id else None
        timed_out = bool(result.get("timed_out", False))
        error = result.get("error")
        exit_code = result.get("exit_code")
        stderr_hint = ""
        if job_id:
            stderr_path = Path(ctx.stage_jobs_dir) / job_id / f"attempt-{attempt}" / "stderr.log"
            if stderr_path.exists():
                try:
                    stderr_hint = stderr_path.read_text(encoding="utf-8")[:4096]
                except Exception:
                    stderr_hint = ""
        failure_type = self._classify_job_failure(ctx, result, stderr_hint=stderr_hint)
        verdict = "TIMED_OUT" if timed_out else "FAILED" if failure_type else "PASSED"
        return {
            "job_id": job_id,
            "attempt": attempt,
            "target_id": ctx.target_id,
            "target_display": self._target_display(ctx),
            "verdict": verdict,
            "failure_type": failure_type,
            "was_started": bool(result.get("started", True)),
            "started_at": result.get("started_at"),
            "ended_at": result.get("ended_at"),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "timeout_scope": result.get("timeout_scope"),
            "error": error,
            "items_count": result.get("items_count"),
            "item_ids": list(result.get("item_ids") or []),
            "duration_seconds": result.get("duration_seconds"),
            "transport": {
                "type": ctx.transport_type or ctx.executor,
                "worker_id": ctx.worker_id,
            },
            "logs": {
                "cmd": f"{job_root_rel}/cmd.log" if job_root_rel else None,
                "stdout": f"{job_root_rel}/stdout.log" if job_root_rel else None,
                "stderr": f"{job_root_rel}/stderr.log" if job_root_rel else None,
            },
        }

    def _base_stage_payload(self, ctx: StageContext) -> dict[str, Any]:
        title = str(ctx.stage_title or "").strip() or ctx.suite_name
        return {
            "schema_version": 1,
            "stage_id": ctx.stage_slug,
            "index": ctx.stage_index,
            "title": title,
            "suite": ctx.suite_name,
            "target_id": ctx.target_id,
            "target_name": ctx.target_name,
            "target_kind": self._target_kind(ctx),
            "target_group": ctx.target_group,
            "target_address": ctx.target_address,
            "target_labels": dict(ctx.target_labels or {}),
            "target_scope": self._target_scope(ctx),
            "target_display": self._target_display(ctx),
            "workload_kind": ctx.kind,
            "executor": ctx.executor,
            "runner": ctx.runner,
            "transport": {
                "type": ctx.transport_type or ctx.executor,
                "worker_id": ctx.worker_id,
            },
            "verdict": "PASSED",
            "failure_type": None,
            "error": None,
            "started_at": None,
            "ended_at": None,
            "duration_seconds": None,
            "lifecycle": {
                "mode": ctx.lifecycle_mode,
                "when": ctx.lifecycle_when,
            },
            "execution": {
                "mode": ctx.execution_mode,
                "count": int(ctx.execution_count),
                "concurrency": int(ctx.max_workers),
            },
            "split": {
                "count": int(ctx.split_count),
                "manifest_path": ctx.split_manifest_path,
            },
            "timeout": {
                "job_seconds": ctx.job_timeout_seconds,
                "stage_seconds": ctx.stage_timeout_seconds,
            },
            "items_total": None,
            "jobs_total": None,
            "jobs_finished": None,
            "jobs_failed": None,
            "jobs_timed_out": 0,
            "artifacts": self._collect_artifacts(ctx),
            "jobs": [],
            "debug": {
                "worker_id": ctx.worker_id,
                "stage_root_path": self._stage_root_rel(ctx),
                "stage_summary_path": self._stage_summary_rel(ctx),
                "stage_reports_path": self._reports_rel(ctx),
            },
        }

    def _write_stage_summary(self, ctx: StageContext, payload: dict[str, Any]) -> None:
        path = self._stage_summary_path(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def ensure_dirs(self, ctx: StageContext) -> None:
        ctx.run.paths.ensure_worker_tmp_dirs(ctx.worker_id)
        ctx.run.paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
        ctx.run.paths.ensure_run_dirs()

    def write_collect_summary(self, ctx: StageContext, *, items_total: int, jobs_total: int) -> None:
        p = self._stage_root(ctx) / "collect-summary.json"
        payload = {
            "run_id": ctx.run.run_id,
            "worker_id": ctx.worker_id,
            "stage": {
                "index": ctx.stage_index,
                "title": ctx.stage_title,
                "suite": ctx.suite_name,
                "kind": ctx.kind,
                "slug": ctx.stage_slug,
            },
            "items_total": int(items_total),
            "jobs_total": int(jobs_total),
        }
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def write_dispatch_summary(self, ctx: StageContext, dispatch: DispatchResult) -> None:
        p = self._stage_root(ctx) / "dispatch-summary.json"
        payload = to_dict_like(dispatch)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def write_stage_summary(
        self,
        ctx: StageContext,
        *,
        items_total: int,
        jobs_total: int,
        dispatch: DispatchResult,
    ) -> dict[str, Any]:
        results = [to_dict_like(r) for r in (dispatch.results or [])]
        failed_ids = sorted(set(dispatch.failed_job_ids or []))
        timed_out_job_ids = list(dispatch.timed_out_job_ids or [])
        watch_failure_reason = str(getattr(dispatch, "watch_failure_reason", "") or "").strip() or None

        payload = self._base_stage_payload(ctx)
        payload.update(
            {
                "items_total": int(items_total),
                "jobs_total": int(jobs_total),
                "jobs_finished": int(len(results)),
                "jobs_failed": int(len(failed_ids)),
                "jobs_timed_out": int(len(timed_out_job_ids)),
                "jobs": [self._job_payload(ctx, result) for result in results],
                "watch_failure_reason": watch_failure_reason,
                "watch": {
                    "failure_reason": watch_failure_reason,
                },
            }
        )
        if watch_failure_reason:
            payload["failure_type"] = "watch"
            payload["error"] = watch_failure_reason
            payload["verdict"] = "FAILED"
        else:
            payload["failure_type"] = self._derive_stage_failure_type(payload)
            payload["verdict"] = "PASSED" if int(payload["jobs_failed"] or 0) == 0 else "FAILED"
        self._write_stage_summary(ctx, payload)
        return payload

    def ensure_stage_summary(
        self,
        ctx: StageContext,
        *,
        ok: bool,
        error: str | None,
        exec_payload: dict[str, Any] | None,
        duration_seconds: float | None,
        failure_type: str | None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        self.ensure_dirs(ctx)

        if exec_payload and "stage_id" in exec_payload:
            payload = dict(exec_payload)
        else:
            payload = self._base_stage_payload(ctx)
            if exec_payload:
                payload["items_total"] = exec_payload.get("items_total")
                payload["jobs_total"] = exec_payload.get("jobs_total")
                payload["jobs_finished"] = exec_payload.get("jobs_finished")
                payload["jobs_failed"] = exec_payload.get("jobs_failed")
                payload["jobs_timed_out"] = exec_payload.get("jobs_timed_out")
                payload["jobs"] = list(exec_payload.get("jobs") or exec_payload.get("results") or [])
                payload["watch"] = dict(exec_payload.get("watch") or {})
        payload["error"] = error
        payload["started_at"] = started_at
        payload["ended_at"] = ended_at
        if failure_type is not None:
            payload["failure_type"] = failure_type
        elif not ok:
            payload["failure_type"] = payload.get("failure_type") or self._derive_stage_failure_type(payload)
        else:
            payload["failure_type"] = None
        payload["duration_seconds"] = duration_seconds

        skipped = str(failure_type or "").strip().lower() == "skipped"
        if skipped:
            payload["verdict"] = "SKIPPED"
        elif not ok:
            payload["verdict"] = "FAILED"
        else:
            payload["verdict"] = "PASSED"

        payload["artifacts"] = self._collect_artifacts(ctx)
        payload["debug"]["stage_reports_path"] = self._reports_rel(ctx)
        self._write_stage_summary(ctx, payload)
