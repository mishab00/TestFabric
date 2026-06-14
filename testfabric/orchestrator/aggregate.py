# testfabric/orchestrator/aggregate.py
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from testfabric.core.serialize import to_dict_like


class RunAggregator:
    def _target_scope_from_parts(
        self,
        *,
        target_group: Any,
        target_address: Any,
    ) -> str | None:
        address = str(target_address or "").strip()
        group = str(target_group or "").strip()
        if address:
            return address
        if group:
            return f"group:{group}"
        return None

    def _target_display_from_parts(
        self,
        *,
        target_name: Any,
        target_id: Any,
        target_kind: Any,
        target_group: Any,
        target_address: Any,
        target_labels: dict[str, Any] | None,
    ) -> str:
        name = str(target_name or target_id or "target").strip() or "target"
        kind = str(target_kind or "").strip() or "unknown"
        scope = self._target_scope_from_parts(target_group=target_group, target_address=target_address)
        labels = dict(target_labels or {})
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

    def _load_events(self, run_dir: Path) -> list[dict[str, Any]]:
        events_path = run_dir / "events.jsonl"
        if not events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if isinstance(event, dict):
                    rows.append(event)
        except Exception:
            return []
        return rows

    def _load_stage_summary(self, path: str | None) -> dict[str, Any]:
        raw = str(path or "").strip()
        if not raw:
            return {}
        candidate = Path(raw)
        if not candidate.exists():
            return {}
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_run_timing(self, run_dir: Path) -> tuple[str | None, str | None]:
        events = self._load_events(run_dir)
        if not events:
            return None, None
        started_at = None
        ended_at = None
        try:
            for event in events:
                if str(event.get("component") or "") != "run":
                    continue
                action = str(event.get("action") or "")
                ts = event.get("ts")
                if action == "start" and started_at is None:
                    started_at = ts
                if action == "end":
                    ended_at = ts
        except Exception:
            return None, None
        return started_at, ended_at

    def _fallback_stage_payload(self, stage_result: Any) -> dict[str, Any]:
        data = to_dict_like(stage_result)
        worker_id = str(data.get("worker_id") or "").strip() or "unknown"
        stage_slug = str(data.get("stage_slug") or "").strip()
        verdict = "SKIPPED" if bool(data.get("skipped")) else "FAILED" if not bool(data.get("ok", False)) else "PASSED"
        target_id = str(data.get("target_id") or "").strip() or "local"
        target_name = str(data.get("target_name") or "").strip() or target_id
        target_kind = str(data.get("target_kind") or "").strip() or "local"
        return {
            "schema_version": 1,
            "stage_id": stage_slug,
            "index": data.get("stage_index"),
            "title": str(data.get("stage_title") or data.get("suite") or stage_slug or "stage"),
            "suite": data.get("suite"),
            "target_id": target_id,
            "target_name": target_name,
            "target_kind": target_kind,
            "target_group": data.get("target_group"),
            "target_address": data.get("target_address"),
            "target_labels": dict(data.get("target_labels") or {}),
            "target_scope": self._target_scope_from_parts(
                target_group=data.get("target_group"),
                target_address=data.get("target_address"),
            ),
            "target_display": self._target_display_from_parts(
                target_name=target_name,
                target_id=target_id,
                target_kind=target_kind,
                target_group=data.get("target_group"),
                target_address=data.get("target_address"),
                target_labels=dict(data.get("target_labels") or {}),
            ),
            "workload_kind": data.get("kind"),
            "executor": data.get("executor"),
            "runner": data.get("runner"),
            "transport": {
                "type": data.get("transport_type") or data.get("executor"),
                "worker_id": worker_id,
            },
            "verdict": verdict,
            "failure_type": data.get("failure_type"),
            "error": data.get("error"),
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "duration_seconds": data.get("duration_seconds"),
            "lifecycle": {
                "mode": data.get("lifecycle_mode"),
                "when": data.get("lifecycle_when"),
            },
            "execution": {"mode": None, "count": None, "concurrency": None},
            "split": {"count": None, "manifest_path": None},
            "timeout": {"job_seconds": None, "stage_seconds": None},
            "items_total": None,
            "jobs_total": None,
            "jobs_finished": None,
            "jobs_failed": None,
            "jobs_timed_out": 0,
            "artifacts": {
                "files_total": 0,
                "counts_by_category": {},
                "counts_by_source": {},
                "primary": [],
                "highlights": [],
            },
            "jobs": [],
            "debug": {
                "worker_id": worker_id,
                "stage_root_path": f"workers/{worker_id}/stages/{stage_slug}" if worker_id and stage_slug else None,
                "stage_summary_path": data.get("stage_summary_rel"),
                "stage_reports_path": f"workers/{worker_id}/stages/{stage_slug}/reports" if worker_id and stage_slug else None,
            },
        }

    def _normalize_stage_payloads(self, stage_results: list[Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for stage_result in stage_results:
            data = to_dict_like(stage_result)
            payload = self._load_stage_summary(data.get("stage_summary_path"))
            if not payload:
                payload = self._fallback_stage_payload(stage_result)
            debug = dict(payload.get("debug") or {})
            stage_root_path = str(debug.get("stage_root_path") or "").strip()
            artifacts = dict(payload.get("artifacts") or {})
            normalized_primary = []
            for raw in list(artifacts.get("primary") or []):
                path = str(raw or "").strip()
                if not path:
                    continue
                if path.startswith("workers/") or not stage_root_path:
                    normalized_primary.append(path)
                else:
                    normalized_primary.append(f"{stage_root_path}/{path}")
            if artifacts:
                artifacts["primary"] = normalized_primary
                normalized_highlights = []
                for raw in list(artifacts.get("highlights") or []):
                    item = dict(raw or {})
                    path = str(item.get("path") or "").strip()
                    if not path:
                        continue
                    if path.startswith("workers/") or not stage_root_path:
                        item["path"] = path
                    else:
                        item["path"] = f"{stage_root_path}/{path}"
                    normalized_highlights.append(item)
                artifacts["highlights"] = normalized_highlights
                payload["artifacts"] = artifacts
            payloads.append(payload)
        return payloads

    def _build_targets(self, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for stage in stages:
            target_id = str(stage.get("target_id") or "unknown")
            bucket = buckets.setdefault(
                target_id,
                {
                    "target_id": target_id,
                    "name": str(stage.get("target_name") or target_id),
                    "kind": str(stage.get("target_kind") or "unknown"),
                    "group": None,
                    "labels": dict(stage.get("target_labels") or {}),
                    "address": stage.get("target_address"),
                    "scope": stage.get("target_scope"),
                    "display_name": stage.get("target_display")
                    or self._target_display_from_parts(
                        target_name=stage.get("target_name"),
                        target_id=target_id,
                        target_kind=stage.get("target_kind"),
                        target_group=stage.get("target_group"),
                        target_address=stage.get("target_address"),
                        target_labels=dict(stage.get("target_labels") or {}),
                    ),
                    "executor": str(stage.get("executor") or ""),
                    "runner": str(stage.get("runner") or ""),
                    "verdict": "PASSED",
                    "stages_total": 0,
                    "stages_failed": 0,
                    "stages_skipped": 0,
                    "jobs_total": 0,
                    "jobs_failed": 0,
                    "duration_seconds": 0.0,
                },
            )
            if stage.get("target_group") is not None:
                bucket["group"] = stage.get("target_group")
            if stage.get("target_address") is not None:
                bucket["address"] = stage.get("target_address")
            if stage.get("target_scope") is not None:
                bucket["scope"] = stage.get("target_scope")
            if stage.get("target_labels"):
                bucket["labels"] = dict(stage.get("target_labels") or {})
            bucket["display_name"] = stage.get("target_display") or bucket["display_name"]

            bucket["stages_total"] += 1
            verdict = str(stage.get("verdict") or "").upper()
            if verdict == "FAILED":
                bucket["stages_failed"] += 1
                bucket["verdict"] = "FAILED"
            elif verdict == "SKIPPED":
                bucket["stages_skipped"] += 1
                if bucket["verdict"] != "FAILED":
                    bucket["verdict"] = "WARNING"

            jobs_total = stage.get("jobs_total")
            jobs_failed = stage.get("jobs_failed")
            duration_seconds = stage.get("duration_seconds")
            if isinstance(jobs_total, int):
                bucket["jobs_total"] += jobs_total
            if isinstance(jobs_failed, int):
                bucket["jobs_failed"] += jobs_failed
                if jobs_failed and bucket["verdict"] != "FAILED":
                    bucket["verdict"] = "FAILED"
            if isinstance(duration_seconds, (int, float)):
                bucket["duration_seconds"] += float(duration_seconds)

        return sorted(buckets.values(), key=lambda item: str(item.get("target_id") or ""))

    def _build_failures(self, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for stage in stages:
            target_id = str(stage.get("target_id") or "unknown")
            target_name = str(stage.get("target_name") or target_id)
            target_display = (
                stage.get("target_display")
                or self._target_display_from_parts(
                    target_name=target_name,
                    target_id=target_id,
                    target_kind=stage.get("target_kind"),
                    target_group=stage.get("target_group"),
                    target_address=stage.get("target_address"),
                    target_labels=dict(stage.get("target_labels") or {}),
                )
            )
            stage_id = str(stage.get("stage_id") or "")
            stage_title = str(stage.get("title") or stage_id)
            jobs = list(stage.get("jobs") or [])
            for job in jobs:
                verdict = str(job.get("verdict") or "").upper()
                if verdict not in {"FAILED", "TIMED_OUT"}:
                    continue
                failures.append(
                    {
                        "scope": "job",
                        "target_id": target_id,
                        "target_name": target_name,
                        "target_display": target_display,
                        "stage_id": stage_id,
                        "stage_title": stage_title,
                        "job_id": job.get("job_id"),
                        "attempt": job.get("attempt"),
                        "verdict": verdict,
                        "failure_type": job.get("failure_type"),
                        "runner": stage.get("runner"),
                        "executor": stage.get("executor"),
                        "transport_type": (job.get("transport") or {}).get("type"),
                        "message": job.get("error") or f"exit_code={job.get('exit_code')}",
                        "job_started": job.get("was_started"),
                        "artifacts": [p for p in list((job.get("logs") or {}).values()) if p],
                    }
                )

            if str(stage.get("verdict") or "").upper() == "FAILED" and not any(
                failure["stage_id"] == stage_id for failure in failures
            ):
                failures.append(
                    {
                        "scope": "stage",
                        "target_id": target_id,
                        "target_name": target_name,
                        "target_display": target_display,
                        "stage_id": stage_id,
                        "stage_title": stage_title,
                        "job_id": None,
                        "attempt": None,
                        "verdict": "FAILED",
                        "failure_type": stage.get("failure_type"),
                        "runner": stage.get("runner"),
                        "executor": stage.get("executor"),
                        "transport_type": (stage.get("transport") or {}).get("type"),
                        "message": stage.get("error"),
                        "job_started": None,
                        "artifacts": [p for p in list((stage.get("artifacts") or {}).get("primary") or [])[:5]],
                    }
                )
        return failures

    def _first_failure_excerpt(self, run_dir: Path, failure: dict[str, Any]) -> str | None:
        artifacts = [str(path or "").strip() for path in list(failure.get("artifacts") or []) if str(path or "").strip()]
        stderr_candidates = [path for path in artifacts if path.endswith("stderr.log")]
        candidates = stderr_candidates + artifacts
        for rel in candidates:
            candidate = run_dir / rel
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in text.splitlines():
                value = line.strip()
                if value:
                    return value
        message = str(failure.get("message") or "").strip()
        return message or None

    def _failure_headline(self, run_dir: Path, failure: dict[str, Any]) -> str:
        scope = str(failure.get("scope") or "").strip().lower()
        stage_title = str(failure.get("stage_title") or failure.get("stage_id") or "stage").strip() or "stage"
        job_id = str(failure.get("job_id") or "").strip()
        detail = self._first_failure_excerpt(run_dir, failure)
        if not detail:
            detail = str(failure.get("message") or "failed").strip() or "failed"
        prefix = f"{stage_title} / {job_id}" if scope == "job" and job_id else stage_title
        headline = f"{prefix}: {detail}"
        if len(headline) > 180:
            return headline[:177].rstrip() + "..."
        return headline

    def _run_failure(self, run_dir: Path, failures: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not failures:
            return None
        failure = dict(failures[0] or {})
        excerpt = self._first_failure_excerpt(run_dir, failure)
        first_failed_job = str(failure.get("job_id") or "").strip() or None
        return {
            "headline": self._failure_headline(run_dir, failure),
            "root_cause": excerpt,
            "scope": failure.get("scope"),
            "target_display": failure.get("target_display"),
            "target_id": failure.get("target_id"),
            "target_name": failure.get("target_name"),
            "stage_id": failure.get("stage_id"),
            "stage_title": failure.get("stage_title"),
            "job_id": first_failed_job,
            "first_failed_job": first_failed_job,
            "attempt": failure.get("attempt"),
            "failure_type": failure.get("failure_type"),
            "message": failure.get("message"),
            "artifacts": list(failure.get("artifacts") or [])[:5],
        }

    def _build_watch(self, run_dir: Path) -> dict[str, Any]:
        raw_events = self._load_events(run_dir)
        counts_by_watcher: dict[str, int] = defaultdict(int)
        counts_by_source: dict[str, int] = defaultdict(int)
        counts_by_action: dict[str, int] = defaultdict(int)
        counts_by_status: dict[str, int] = defaultdict(int)
        first_trigger: dict[str, Any] | None = None

        for event in raw_events:
            if str(event.get("component") or "").strip() != "watch":
                continue
            details = dict(event.get("details") or {})
            watcher = str(event.get("action") or details.get("watcher") or "").strip()
            source = str(details.get("source") or "").strip()
            action_type = str(details.get("action_type") or "").strip()
            status = str(event.get("status") or "").strip()
            if watcher:
                counts_by_watcher[watcher] += 1
            if source:
                counts_by_source[source] += 1
            if action_type:
                counts_by_action[action_type] += 1
            if status:
                counts_by_status[status] += 1
            if first_trigger is None:
                first_trigger = {
                    "watcher": watcher or None,
                    "source": source or None,
                    "status": status or None,
                    "action": action_type or None,
                    "message": str(event.get("message") or details.get("message") or "").strip() or None,
                    "target_id": details.get("target_id"),
                    "stage_id": details.get("stage_id"),
                    "job_id": details.get("job_id"),
                    "attempt": details.get("attempt"),
                    "phase": details.get("phase"),
                }

        payload = {
            "total": sum(counts_by_watcher.values()),
            "counts_by_watcher": dict(sorted(counts_by_watcher.items())),
            "counts_by_source": dict(sorted(counts_by_source.items())),
            "counts_by_action": dict(sorted(counts_by_action.items())),
            "counts_by_status": dict(sorted(counts_by_status.items())),
            "first_trigger": first_trigger,
        }
        if payload["total"] <= 0:
            payload["first_trigger"] = None
        return payload

    def _build_totals(self, stages: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any]:
        stages_total = len(stages)
        stages_failed = sum(1 for stage in stages if str(stage.get("verdict") or "").upper() == "FAILED")
        stages_skipped = sum(1 for stage in stages if str(stage.get("verdict") or "").upper() == "SKIPPED")
        stages_passed = sum(1 for stage in stages if str(stage.get("verdict") or "").upper() == "PASSED")
        jobs_total = sum(int(stage.get("jobs_total") or 0) for stage in stages)
        jobs_failed = sum(int(stage.get("jobs_failed") or 0) for stage in stages)
        jobs_timed_out = sum(int(stage.get("jobs_timed_out") or 0) for stage in stages)
        artifacts_total = sum(int((stage.get("artifacts") or {}).get("files_total") or 0) for stage in stages)
        jobs_by_failure_type: dict[str, int] = defaultdict(int)
        stages_by_failure_type: dict[str, int] = defaultdict(int)
        for stage in stages:
            stage_failure = str(stage.get("failure_type") or "").strip()
            if stage_failure:
                stages_by_failure_type[stage_failure] += 1
            for job in list(stage.get("jobs") or []):
                job_failure = str(job.get("failure_type") or "").strip()
                if job_failure:
                    jobs_by_failure_type[job_failure] += 1
        return {
            "targets_total": len(targets),
            "stages_total": stages_total,
            "stages_passed": stages_passed,
            "stages_failed": stages_failed,
            "stages_skipped": stages_skipped,
            "stages_by_failure_type": dict(sorted(stages_by_failure_type.items())),
            "jobs_total": jobs_total,
            "jobs_passed": max(jobs_total - jobs_failed - jobs_timed_out, 0),
            "jobs_failed": jobs_failed,
            "jobs_timed_out": jobs_timed_out,
            "jobs_by_failure_type": dict(sorted(jobs_by_failure_type.items())),
            "artifacts_total": artifacts_total,
        }

    def _build_artifacts(self, run_dir: Path, stages: list[dict[str, Any]]) -> dict[str, Any]:
        stage_rows = []
        primary = []
        counts_by_category: dict[str, int] = defaultdict(int)
        counts_by_source: dict[str, int] = defaultdict(int)
        for stage in stages:
            artifacts = dict(stage.get("artifacts") or {})
            stage_id = stage.get("stage_id")
            stage_title = stage.get("title")
            stage_target = stage.get("target_name") or stage.get("target_id")
            row = {
                "stage_id": stage_id,
                "title": stage_title,
                "files_total": artifacts.get("files_total"),
                "counts_by_category": dict(artifacts.get("counts_by_category") or {}),
                "counts_by_source": dict(artifacts.get("counts_by_source") or {}),
                "primary": list(artifacts.get("primary") or []),
                "highlights": list(artifacts.get("highlights") or [])[:10],
            }
            stage_rows.append(row)
            for key, value in dict(artifacts.get("counts_by_category") or {}).items():
                try:
                    counts_by_category[str(key)] += int(value)
                except Exception:
                    continue
            for key, value in dict(artifacts.get("counts_by_source") or {}).items():
                try:
                    counts_by_source[str(key)] += int(value)
                except Exception:
                    continue
            highlights = list(artifacts.get("highlights") or [])[:10]
            if highlights:
                for item in highlights:
                    primary.append(
                        {
                            "stage_id": stage_id,
                            "stage_title": stage_title,
                            "target_name": stage_target,
                            "path": item.get("path"),
                            "category": item.get("category"),
                            "source": item.get("source"),
                            "label": item.get("label"),
                        }
                    )
            else:
                for path in list(artifacts.get("primary") or [])[:10]:
                    primary.append(
                        {
                            "stage_id": stage_id,
                            "stage_title": stage_title,
                            "target_name": stage_target,
                            "path": path,
                            "category": "artifact",
                            "source": "reports",
                            "label": Path(str(path)).name or "Artifact",
                        }
                    )
        return {
            "files_total": sum(int((stage.get("artifacts") or {}).get("files_total") or 0) for stage in stages),
            "counts_by_category": dict(sorted(counts_by_category.items())),
            "counts_by_source": dict(sorted(counts_by_source.items())),
            "primary": primary,
            "stages": stage_rows,
        }

    def _run_section(
        self,
        *,
        spec: Any,
        run_id: str,
        run_dir: Path,
        ok: bool,
        error: str | None,
        verdict: str,
        health: dict[str, Any] | None,
        failures: list[dict[str, Any]],
        stage_results: list[Any],
    ) -> dict[str, Any]:
        started_at, ended_at = self._load_run_timing(run_dir)
        commit_sha = None
        for stage_result in stage_results:
            data = to_dict_like(stage_result)
            commit_sha = data.get("commit_sha") or commit_sha
        run_cfg = getattr(spec, "run", None)
        return {
            "run_id": run_id,
            "name": str(getattr(run_cfg, "name", "") or ""),
            "verdict": verdict,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": sum(
                float(to_dict_like(result).get("duration_seconds") or 0.0) for result in stage_results
            ) or None,
            "mode": "run",
            "profile": None,
            "repo": {
                "url": str(getattr(run_cfg, "repo_url", "") or ""),
                "ref": str(getattr(run_cfg, "ref", "") or ""),
                "sha": commit_sha,
            },
            "failure": self._run_failure(run_dir, failures),
            "health": {
                "state": str((health or {}).get("state") or "unknown"),
                "reasons": list((health or {}).get("reasons") or []),
            },
            "error": error,
        }

    def _build_events(self, run_dir: Path) -> dict[str, Any]:
        raw_events = self._load_events(run_dir)
        counts_by_component: dict[str, int] = defaultdict(int)
        counts_by_status: dict[str, int] = defaultdict(int)
        counts_by_action: dict[str, int] = defaultdict(int)
        rows: list[dict[str, Any]] = []
        for event in raw_events:
            component = str(event.get("component") or "").strip()
            action = str(event.get("action") or "").strip()
            status = str(event.get("status") or "").strip()
            if component:
                counts_by_component[component] += 1
            if status:
                counts_by_status[status] += 1
            if component or action:
                counts_by_action[f"{component}:{action}".strip(":")] += 1
            rows.append(
                {
                    "seq": event.get("seq"),
                    "ts": event.get("ts"),
                    "target_id": event.get("target_id"),
                    "stage_id": event.get("stage_id"),
                    "job_id": event.get("job_id"),
                    "attempt": event.get("attempt"),
                    "component": component,
                    "action": action,
                    "status": status,
                    "message": event.get("message"),
                }
            )
        return {
            "path": "events.jsonl",
            "total": len(rows),
            "counts_by_component": dict(sorted(counts_by_component.items())),
            "counts_by_status": dict(sorted(counts_by_status.items())),
            "counts_by_action": dict(sorted(counts_by_action.items())),
            "rows": rows,
        }

    def _run_verdict(self, *, ok: bool, stages: list[dict[str, Any]], health: dict[str, Any] | None) -> str:
        if not ok:
            return "FAILED"
        if str((health or {}).get("state") or "").strip().lower() == "degraded":
            return "WARNING"
        if any(str(stage.get("verdict") or "").upper() == "SKIPPED" for stage in stages):
            return "WARNING"
        return "PASSED"

    def write_run_summary(
        self,
        *,
        spec: Any,
        paths: Any,
        run_id: str,
        ok: bool,
        stage_results: list[Any],
        error: str | None = None,
        health: dict[str, Any] | None = None,
    ) -> None:
        run_dir: Path = paths.run_dir
        stages = self._normalize_stage_payloads(stage_results)
        targets = self._build_targets(stages)
        verdict = self._run_verdict(ok=ok, stages=stages, health=health)
        totals = self._build_totals(stages, targets)
        failures = self._build_failures(stages)
        summary_payload = {
            "schema_version": 1,
            "run": self._run_section(
                spec=spec,
                run_id=run_id,
                run_dir=run_dir,
                ok=ok,
                error=error,
                verdict=verdict,
                health=health,
                failures=failures,
                stage_results=stage_results,
            ),
            "totals": totals,
            "targets": targets,
            "stages": stages,
            "failures": failures,
            "watch": self._build_watch(run_dir),
            "artifacts": self._build_artifacts(run_dir, stages),
            "events": self._build_events(run_dir),
            "debug": {
                "events_path": "events.jsonl",
                "run_root": str(run_dir),
                "stage_summary_paths": [
                    str((stage.get("debug") or {}).get("stage_summary_path") or "")
                    for stage in stages
                    if str((stage.get("debug") or {}).get("stage_summary_path") or "").strip()
                ],
            },
        }
        paths.run_summary_path.write_text(
            json.dumps(summary_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
