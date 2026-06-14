# testfabric/cli/main.py
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, List, Any

import typer
import yaml

from testfabric.cli.config import (
    CLIConfig,
    ContextProfile,
    context_rows,
    default_config_path,
    get_context,
    load_config,
    save_config,
    set_active_context,
    upsert_context,
)
from testfabric.cli.transport import (
    RunDispatchResult,
    RunInvocation,
    resolve_transport,
)
from testfabric.core.constants import (
    DEFAULT_ARTIFACT_BROWSER_LIMIT,
    DEFAULT_QUEUE_POLICY,
    DEFAULT_WORKER_CAPACITY,
    DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_WORKER_IDLE_SLEEP_SECONDS,
)
from testfabric.core.contracts import ReplayRequest, RunQuery, WorkerRegistration, WorkerStatus
from testfabric.spec.schema import RunSpec
from testfabric.spec.parametrize import parse_assignment_list
from testfabric.spec.lint import lint_raw_spec
from testfabric.orchestrator.orchestrator import RunOptions
from testfabric.artifacts.paths import PathManager
from testfabric.worker import RemoteWorkerRuntime, WorkerServeSummary
from testfabric.worker.client import WorkerClient

app = typer.Typer(add_completion=False, help="TestFabric runner")
config_app = typer.Typer(add_completion=False, help="Manage CLI contexts and backend configuration")
worker_app = typer.Typer(add_completion=False, help="Manage worker processes")

Mode = Literal["run", "dry-run"]
ConsoleVerbosity = Literal["quiet", "summary", "normal", "verbose"]


def _load_spec(
    path: str,
    *,
    profile: str | None = None,
    inputs_map: dict[str, str] | None = None,
    lint: bool = False,
) -> tuple[RunSpec, dict[str, object]]:
    p = Path(path).expanduser()
    if not p.exists():
        raise typer.BadParameter(f"Spec file not found: {p}")
    try:
        if lint:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise typer.BadParameter("Spec YAML must parse to a mapping/dict at top-level.")
            for issue in lint_raw_spec(raw):
                label = issue.level.upper()
                typer.echo(f"[{label}] {issue.path}: {issue.message}")
        spec, resolved_inputs = RunSpec.load_resolved(
            str(p),
            profile=profile,
            inputs_map=inputs_map,
        )
        return spec, dict(resolved_inputs.values)
    except Exception as e:
        raise typer.BadParameter(f"Failed to load spec: {e}") from e


def _fmt_ts(ts: float) -> str:
    # local time with timezone
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _fmt_dur(seconds: float) -> str:
    s = int(max(0.0, seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _fmt_count_map(values: dict[str, object] | None) -> str:
    items = []
    for key, value in sorted((values or {}).items()):
        try:
            numeric = int(value)
        except Exception:
            continue
        if numeric <= 0:
            continue
        items.append(f"{key}={numeric}")
    return ", ".join(items) if items else "none"


def _fmt_state_counts(values: dict[str, object] | None) -> str:
    counts = dict(values or {})
    preferred_order = ["queued", "running", "completed", "failed", "canceled", "dry-run"]
    items: list[str] = []
    for key in preferred_order:
        if key in counts:
            try:
                numeric = int(counts.pop(key))
            except Exception:
                continue
            if numeric > 0:
                items.append(f"{key}={numeric}")
    for key, value in sorted(counts.items()):
        try:
            numeric = int(value)
        except Exception:
            continue
        if numeric <= 0:
            continue
        items.append(f"{key}={numeric}")
    return ", ".join(items) if items else "none"


def _fmt_optional_dur(seconds: object) -> str:
    try:
        if seconds is None:
            return "-"
        return _fmt_dur(float(seconds))
    except Exception:
        return "-"


def _trim_cell(text: object, width: int) -> str:
    value = str(text or "").replace("\n", " ").strip() or "-"
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _render_table(headers: list[str], rows: list[list[object]], *, max_widths: list[int] | None = None) -> list[str]:
    if not rows:
        return ["- none"]
    widths = [len(header) for header in headers]
    rendered_rows: list[list[str]] = []
    for row in rows:
        values = [str(cell or "").replace("\n", " ").strip() or "-" for cell in row]
        rendered_rows.append(values)
        for idx, value in enumerate(values):
            widths[idx] = max(widths[idx], len(value))
    if max_widths:
        widths = [min(widths[idx], max_widths[idx]) for idx in range(len(widths))]
    lines = [
        " | ".join(_trim_cell(header, widths[idx]).ljust(widths[idx]) for idx, header in enumerate(headers)),
        "-+-".join("-" * width for width in widths),
    ]
    for row in rendered_rows:
        lines.append(" | ".join(_trim_cell(cell, widths[idx]).ljust(widths[idx]) for idx, cell in enumerate(row)))
    return lines


def _fmt_repo(repo: dict[str, Any]) -> str:
    url = str(repo.get("url") or "").strip()
    ref = str(repo.get("ref") or "").strip()
    sha = str(repo.get("sha") or "").strip()
    parts = []
    if url:
        parts.append(url)
    if ref:
        parts.append(f"ref={ref}")
    if sha:
        parts.append(f"sha={sha[:12]}")
    return " | ".join(parts) if parts else "-"


def _stage_sort_key(stage: dict[str, Any]) -> tuple[int, str]:
    verdict = str(stage.get("verdict") or "").upper()
    priority = {
        "FAILED": 0,
        "WARNING": 1,
        "SKIPPED": 2,
        "PASSED": 3,
    }.get(verdict, 4)
    return priority, str(stage.get("stage_id") or stage.get("title") or "")


def _render_summary_text(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None

    run_payload = dict(payload.get("run") or {})
    totals = dict(payload.get("totals") or {})
    stages = list(payload.get("stages") or [])
    targets = list(payload.get("targets") or [])
    failures = list(payload.get("failures") or [])
    events = dict(payload.get("events") or {})
    artifacts = dict(payload.get("artifacts") or {})
    watch = dict(payload.get("watch") or {})

    if not run_payload:
        return None

    health = dict(run_payload.get("health") or {})
    failure = dict(run_payload.get("failure") or {})
    lines = [
        "Run",
        f"- ID: {run_payload.get('run_id') or '-'}",
        f"- Verdict: {run_payload.get('verdict') or '-'}",
        f"- Health: {health.get('state') or '-'}",
        f"- Duration: {_fmt_optional_dur(run_payload.get('duration_seconds'))}",
        f"- Repo: {_fmt_repo(dict(run_payload.get('repo') or {}))}",
    ]

    headline = str(failure.get("headline") or "").strip()
    if headline:
        lines.append(f"- Failure: {headline}")

    error = str(run_payload.get("error") or "").strip()
    if error and not headline:
        lines.append(f"- Error: {error}")

    lines.extend(
        [
            "",
            "Watch",
            f"- Total: {watch.get('total', 0)}",
            f"- By Watcher: {_fmt_count_map(dict(watch.get('counts_by_watcher') or {}))}",
            f"- By Source: {_fmt_count_map(dict(watch.get('counts_by_source') or {}))}",
            f"- By Action: {_fmt_count_map(dict(watch.get('counts_by_action') or {}))}",
            f"- By Status: {_fmt_count_map(dict(watch.get('counts_by_status') or {}))}",
        ]
    )
    first_trigger = dict(watch.get("first_trigger") or {})
    if first_trigger:
        trigger_bits = [
            str(first_trigger.get("watcher") or "-"),
            str(first_trigger.get("status") or "-"),
            str(first_trigger.get("action") or "-"),
            str(first_trigger.get("source") or "-"),
        ]
        stage_id = str(first_trigger.get("stage_id") or "").strip()
        job_id = str(first_trigger.get("job_id") or "").strip()
        if stage_id:
            trigger_bits.append(f"stage={stage_id}")
        if job_id:
            trigger_bits.append(f"job={job_id}")
        message = str(first_trigger.get("message") or "").strip()
        if message:
            trigger_bits.append(message)
        lines.append(f"- First Trigger: {' | '.join(trigger_bits)}")
    else:
        lines.append("- First Trigger: none")

    lines.extend(
        [
            "",
            "Totals",
            f"- Targets: {totals.get('targets_total', 0)}",
            "- Stages: {total} total, {passed} passed, {failed} failed, {skipped} skipped".format(
                total=totals.get("stages_total", 0),
                passed=totals.get("stages_passed", 0),
                failed=totals.get("stages_failed", 0),
                skipped=totals.get("stages_skipped", 0),
            ),
            "- Jobs: {total} total, {passed} passed, {failed} failed, {timed_out} timed out".format(
                total=totals.get("jobs_total", 0),
                passed=totals.get("jobs_passed", 0),
                failed=totals.get("jobs_failed", 0),
                timed_out=totals.get("jobs_timed_out", 0),
            ),
            f"- Artifacts: {totals.get('artifacts_total', 0)}",
            f"- Stage Failures By Type: {_fmt_count_map(dict(totals.get('stages_by_failure_type') or {}))}",
            f"- Job Failures By Type: {_fmt_count_map(dict(totals.get('jobs_by_failure_type') or {}))}",
        ]
    )

    lines.extend(
        [
            "Failures",
        ]
    )
    if failures:
        failure_rows = [
            [
                failure.get("scope") or "-",
                failure.get("target_display") or failure.get("target_name") or failure.get("target_id") or "-",
                failure.get("stage_title") or failure.get("stage_id") or "-",
                failure.get("job_id") or "-",
                failure.get("verdict") or "-",
                failure.get("message") or "",
            ]
            for failure in failures[:10]
        ]
        lines.extend(
            _render_table(
                ["scope", "target", "stage", "job", "verdict", "message"],
                failure_rows,
                max_widths=[12, 42, 28, 16, 10, 72],
            )
        )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Stages",
        ]
    )
    if stages:
        stage_rows = []
        for stage in sorted(stages, key=_stage_sort_key)[:10]:
            stage_rows.append(
                [
                    stage.get("title") or stage.get("stage_id") or "-",
                    stage.get("target_display") or stage.get("target_name") or stage.get("target_id") or "-",
                    stage.get("verdict") or "-",
                    stage.get("executor") or "-",
                    stage.get("runner") or "-",
                    stage.get("jobs_total", 0),
                    stage.get("jobs_failed", 0),
                    _fmt_optional_dur(stage.get("duration_seconds")),
                    stage.get("error") or "",
                ]
            )
        lines.extend(
            _render_table(
                ["stage", "target", "verdict", "executor", "runner", "jobs", "failed", "duration", "error"],
                stage_rows,
                max_widths=[30, 42, 10, 10, 10, 6, 8, 10, 72],
            )
        )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Targets",
        ]
    )
    if targets:
        target_rows = []
        for target in targets:
            labels = dict(target.get("labels") or {})
            label_text = ",".join(f"{key}={value}" for key, value in sorted(labels.items())) or "-"
            target_rows.append(
                [
                    target.get("display_name") or target.get("name") or target.get("target_id") or "-",
                    target.get("kind") or "-",
                    target.get("scope") or target.get("address") or target.get("group") or "-",
                    target.get("verdict") or "-",
                    target.get("stages_total", 0),
                    target.get("stages_failed", 0),
                    target.get("jobs_total", 0),
                    target.get("jobs_failed", 0),
                    _fmt_optional_dur(target.get("duration_seconds")),
                    label_text,
                ]
            )
        lines.extend(
            _render_table(
                ["target", "kind", "scope", "verdict", "stages", "stage_failures", "jobs", "job_failures", "duration", "labels"],
                target_rows,
                max_widths=[42, 12, 36, 10, 8, 15, 8, 12, 10, 56],
            )
        )
    else:
        lines.append("- none")

    event_rows = list(events.get("rows") or [])
    lines.extend(
        [
            "",
            "Events",
            f"- Path: {events.get('path') or '-'}",
            f"- Total: {events.get('total', len(event_rows))}",
            f"- Status: {_fmt_count_map(dict(events.get('counts_by_status') or {}))}",
            f"- Components: {_fmt_count_map(dict(events.get('counts_by_component') or {}))}",
            f"- Actions: {_fmt_count_map(dict(events.get('counts_by_action') or {}))}",
        ]
    )
    if event_rows:
        event_table_rows = []
        for row in event_rows[:10]:
            event_table_rows.append(
                [
                    row.get("seq") or "-",
                    row.get("component") or "-",
                    row.get("action") or "-",
                    row.get("status") or "-",
                    row.get("target_id") or "-",
                    row.get("stage_id") or "-",
                    row.get("job_id") or "-",
                    row.get("message") or "",
                ]
            )
        lines.extend(
            _render_table(
                ["seq", "component", "action", "status", "target", "stage", "job", "message"],
                event_table_rows,
                max_widths=[6, 12, 12, 10, 18, 28, 18, 72],
            )
        )

    primary_artifacts = list(artifacts.get("primary") or [])
    lines.extend(
        [
            "",
            "Artifacts",
            f"- Files Total: {artifacts.get('files_total', len(primary_artifacts))}",
            f"- By Category: {_fmt_count_map(dict(artifacts.get('counts_by_category') or {}))}",
            f"- By Source: {_fmt_count_map(dict(artifacts.get('counts_by_source') or {}))}",
            f"- Primary Entries: {len(primary_artifacts)}",
        ]
    )
    if primary_artifacts:
        artifact_rows = []
        for item in primary_artifacts[:10]:
            if isinstance(item, dict):
                artifact_rows.append(
                    [
                        item.get("label") or item.get("category") or item.get("stage_id") or "artifact",
                        item.get("stage_title") or item.get("stage_id") or "-",
                        item.get("target_name") or "-",
                        item.get("category") or "-",
                        item.get("source") or "-",
                        item.get("path") or "-",
                    ]
                )
            else:
                artifact_rows.append([str(item), "-", "-", "-", "-", str(item)])
        lines.extend(
            _render_table(
                ["label", "stage", "target", "category", "source", "path"],
                artifact_rows,
                max_widths=[26, 28, 20, 14, 12, 80],
            )
        )
    else:
        lines.append("- none")

    return "\n".join(lines)


def _render_context_details(config: CLIConfig, name: str | None = None) -> str:
    ctx = get_context(config, name)
    payload = ctx.model_dump(exclude_none=True)
    payload["active"] = ctx.name == (config.active_context or "local")
    return yaml.safe_dump(payload, sort_keys=False).rstrip()


def _print_context_list(config: CLIConfig) -> None:
    rows = context_rows(config)
    table_rows = [
        [
            row.get("active") or "",
            row.get("name") or "-",
            row.get("mode") or "-",
            row.get("api_url") or "-",
            row.get("team") or "-",
            row.get("project") or "-",
            row.get("workspace") or "-",
        ]
        for row in rows
    ]
    typer.echo(
        "\n".join(
            _render_table(
                ["active", "name", "mode", "api_url", "team", "project", "workspace"],
                table_rows,
                max_widths=[6, 18, 10, 36, 18, 18, 28],
            )
        )
    )


def _load_cli_config() -> tuple[CLIConfig, Path]:
    path = default_config_path()
    cfg = load_config(path)
    return cfg, path


def _print_begin(
    *,
    run_id: str,
    run_dir: str,
    summary: str,
    started_at: float,
) -> None:
    typer.echo("\n=== START ===")
    typer.echo(f"run_id:     {run_id}")
    typer.echo(f"run_dir:    {run_dir}")
    typer.echo(f"summary:    {summary}")
    typer.echo(f"started_at: {_fmt_ts(started_at)}")


def _print_end(*, ended_at: float, started_at: float) -> None:
    typer.echo(f"ended_at:   {_fmt_ts(ended_at)}")
    typer.echo(f"elapsed:    {_fmt_dur(ended_at - started_at)}")


def _print_summary(res: dict) -> None:
    ok = bool(res.get("ok"))
    run_id = res.get("run_id")
    run_dir = res.get("run_dir")
    summary = res.get("run_summary_path")
    err = res.get("error")

    status = "SUCCESS" if ok else "FAILED"
    typer.echo(f"\n=== {status} ===")
    summary_text = None
    if summary:
        try:
            payload = json.loads(Path(str(summary)).read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                summary_text = _render_summary_text(payload)
        except Exception:
            summary_text = None
    if summary_text:
        typer.echo(summary_text)
    else:
        typer.echo(f"run_id:   {run_id}")
        typer.echo(f"run_dir:  {run_dir}")
    if err:
        typer.echo(f"error:    {err}")
    typer.echo("")
    typer.echo("Files")
    typer.echo(f"- run_dir:  {run_dir}")
    typer.echo(f"- summary:  {summary}")
    if summary:
        try:
            payload = json.loads(Path(str(summary)).read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                events = dict(payload.get("events") or {})
                if events.get("path"):
                    typer.echo(f"- events:   {Path(str(run_dir)) / str(events.get('path'))}")
        except Exception:
            pass


def _print_remote_submission(res: RunDispatchResult, context: ContextProfile) -> None:
    typer.echo(f"\n=== {'SUCCESS' if res.ok else 'FAILED'} ===")
    typer.echo(f"run_id:   {res.run_id}")
    typer.echo(f"backend:  {context.api_url or '-'}")
    typer.echo(f"context:  {context.name}")
    typer.echo(f"accepted: {res.accepted}")
    if res.status_code is not None:
        typer.echo(f"status:   {res.status_code}")
    if res.message:
        typer.echo(f"message:  {res.message}")
    if res.payload:
        typer.echo("payload:")
        typer.echo(json.dumps(res.payload, indent=2, sort_keys=True))


def _render_remote_run_rows(payload: dict[str, Any]) -> list[list[object]]:
    items = list(payload.get("items") or [])
    rows: list[list[object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = dict(item.get("status") or {})
        metadata = dict(status.get("metadata") or {})
        rows.append(
            [
                item.get("run_id") or "-",
                status.get("state") or "-",
                status.get("verdict") or "-",
                metadata.get("team") or "-",
                metadata.get("project") or "-",
                item.get("message") or status.get("message") or "-",
            ]
        )
    return rows


def _print_remote_runs(payload: dict[str, Any]) -> None:
    typer.echo(f"total: {payload.get('total', 0)}")
    typer.echo(
        "\n".join(
            _render_table(
                ["run_id", "state", "verdict", "team", "project", "message"],
                _render_remote_run_rows(payload),
                max_widths=[24, 12, 12, 18, 20, 72],
            )
        )
    )


def _render_remote_dashboard_rows(payload: dict[str, Any]) -> list[list[object]]:
    items = list(payload.get("recent_runs") or [])
    rows: list[list[object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                item.get("run_id") or "-",
                item.get("state") or "-",
                item.get("verdict") or "-",
                item.get("team") or "-",
                item.get("project") or "-",
                _fmt_optional_dur(item.get("duration_seconds")),
                item.get("message") or "-",
            ]
        )
    return rows


def _print_remote_dashboard(payload: dict[str, Any]) -> None:
    typer.echo(f"total: {payload.get('total', 0)}")
    typer.echo(f"states: {_fmt_state_counts(dict(payload.get('counts_by_state') or {}))}")
    typer.echo(f"verdicts: {_fmt_count_map(dict(payload.get('counts_by_verdict') or {}))}")
    typer.echo(f"teams: {_fmt_count_map(dict(payload.get('counts_by_team') or {}))}")
    typer.echo(f"projects: {_fmt_count_map(dict(payload.get('counts_by_project') or {}))}")
    typer.echo("recent runs:")
    typer.echo(
        "\n".join(
            _render_table(
                ["run_id", "state", "verdict", "team", "project", "duration", "message"],
                _render_remote_dashboard_rows(payload),
                max_widths=[24, 12, 12, 18, 20, 10, 72],
            )
        )
    )


def _render_detail_block(title: str, values: dict[str, Any], keys: list[tuple[str, str]]) -> list[str]:
    lines = [title]
    if not values:
        lines.append("- none")
        return lines
    for label, key in keys:
        value = values.get(key)
        if isinstance(value, dict):
            lines.append(f"- {label}:")
            rendered = json.dumps(value, indent=2, sort_keys=True)
            for line in rendered.splitlines():
                lines.append(f"  {line}")
        elif isinstance(value, list):
            lines.append(f"- {label}:")
            rendered = json.dumps(value, indent=2, sort_keys=True)
            for line in rendered.splitlines():
                lines.append(f"  {line}")
        elif value is None or value == "":
            lines.append(f"- {label}: -")
        else:
            lines.append(f"- {label}: {value}")
    return lines


def _print_remote_run_detail(payload: dict[str, Any]) -> None:
    request = dict(payload.get("request") or {})
    status = dict(payload.get("status") or {})
    result = dict(payload.get("result") or {})
    metadata = dict(status.get("metadata") or {})
    summary = payload.get("summary")
    links = dict(payload.get("links") or {})

    typer.echo(f"run_id:   {payload.get('run_id') or '-'}")
    typer.echo(f"accepted: {payload.get('accepted', False)}")
    typer.echo(f"state:    {status.get('state') or '-'}")
    typer.echo(f"verdict:  {status.get('verdict') or '-'}")
    typer.echo(f"message:  {payload.get('message') or status.get('message') or '-'}")
    typer.echo(f"duration: {_fmt_optional_dur(payload.get('duration_seconds'))}")
    typer.echo(f"team:     {metadata.get('team') or '-'}")
    typer.echo(f"project:  {metadata.get('project') or '-'}")
    typer.echo(f"context:  {metadata.get('context') or '-'}")
    typer.echo(f"workspace: {payload.get('workspace') or metadata.get('workspace') or '-'}")
    typer.echo(f"branch:   {payload.get('branch') or '-'}")
    typer.echo(f"ref:      {payload.get('ref') or '-'}")
    typer.echo(f"tags:     {', '.join(payload.get('tags') or []) or '-'}")
    typer.echo(f"links:    {json.dumps(links, sort_keys=True)}")
    typer.echo("")
    typer.echo(
        "\n".join(
            _render_detail_block(
                "Request",
                request,
                [
                    ("mode", "mode"),
                    ("spec_path", "spec_path"),
                    ("repo_url", "repo_url"),
                    ("ref", "ref"),
                    ("team", "team"),
                    ("project", "project"),
                    ("workspace", "workspace"),
                    ("context", "context"),
                    ("inputs", "inputs"),
                    ("tags", "tags"),
                    ("metadata", "metadata"),
                ],
            )
        )
    )
    typer.echo("")
    typer.echo(
        "\n".join(
            _render_detail_block(
                "Status",
                status,
                [
                    ("state", "state"),
                    ("verdict", "verdict"),
                    ("message", "message"),
                    ("started_at", "started_at"),
                    ("ended_at", "ended_at"),
                    ("stage_id", "stage_id"),
                    ("job_id", "job_id"),
                    ("attempt", "attempt"),
                    ("progress", "progress"),
                    ("metadata", "metadata"),
                ],
            )
        )
    )
    if result:
        typer.echo("")
        typer.echo(
            "\n".join(
                _render_detail_block(
                    "Result",
                    result,
                    [
                        ("ok", "ok"),
                        ("state", "state"),
                        ("verdict", "verdict"),
                        ("error", "error"),
                        ("run_dir", "run_dir"),
                        ("summary_path", "summary_path"),
                        ("metadata", "metadata"),
                    ],
                )
            )
        )
    if isinstance(summary, dict) and summary:
        typer.echo("")
        typer.echo("Summary")
        summary_text = _render_summary_text(summary)
        if summary_text:
            typer.echo(summary_text)
        else:
            typer.echo(json.dumps(summary, indent=2, sort_keys=True))


def _run_detail_stages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summary = dict(payload.get("summary") or {})
    stages = list(summary.get("stages") or [])
    return [stage for stage in stages if isinstance(stage, dict)]


def _find_run_detail_stage(payload: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
    target = str(stage_id or "").strip()
    if not target:
        return None
    for stage in _run_detail_stages(payload):
        candidates = {
            str(stage.get("stage_id") or "").strip(),
            str(stage.get("stage_title") or "").strip(),
            str(stage.get("title") or "").strip(),
        }
        if target in candidates:
            return stage
    return None


def _render_stage_rows(payload: dict[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = []
    for stage in _run_detail_stages(payload):
        artifacts = dict(stage.get("artifacts") or {})
        transport = dict(stage.get("transport") or {})
        rows.append(
            [
                stage.get("stage_id") or "-",
                stage.get("stage_title") or stage.get("title") or "-",
                stage.get("verdict") or "-",
                stage.get("executor") or "-",
                transport.get("type") or "-",
                _fmt_optional_dur(stage.get("duration_seconds")),
                stage.get("jobs_total") or len(list(stage.get("jobs") or [])),
                stage.get("jobs_failed") or 0,
                artifacts.get("files_total") or artifacts.get("count") or 0,
            ]
        )
    return rows


def _print_remote_stages(payload: dict[str, Any]) -> None:
    typer.echo(f"run_id: {payload.get('run_id') or '-'}")
    typer.echo(f"total:  {len(_run_detail_stages(payload))}")
    typer.echo(
        "\n".join(
            _render_table(
                ["stage_id", "title", "verdict", "executor", "transport", "duration", "jobs", "failed", "artifacts"],
                _render_stage_rows(payload),
                max_widths=[24, 32, 12, 12, 12, 10, 8, 8, 10],
            )
        )
    )


def _render_job_rows(stage: dict[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = []
    for job in list(stage.get("jobs") or []):
        if not isinstance(job, dict):
            continue
        transport = dict(job.get("transport") or {})
        rows.append(
            [
                job.get("job_id") or "-",
                job.get("verdict") or job.get("state") or "-",
                job.get("attempt") if job.get("attempt") is not None else "-",
                _fmt_optional_dur(job.get("duration_seconds")),
                transport.get("type") or "-",
                job.get("target_id") or "-",
                job.get("message") or job.get("error") or "-",
            ]
        )
    return rows


def _print_remote_stage_detail(payload: dict[str, Any], stage_id: str) -> None:
    stage = _find_run_detail_stage(payload, stage_id)
    if stage is None:
        typer.echo(f"stage_id: {stage_id}")
        typer.echo("error:    stage not found in run summary")
        return

    artifacts = dict(stage.get("artifacts") or {})
    transport = dict(stage.get("transport") or {})
    typer.echo(f"run_id:   {payload.get('run_id') or '-'}")
    typer.echo(f"stage_id: {stage.get('stage_id') or '-'}")
    typer.echo(f"title:    {stage.get('stage_title') or stage.get('title') or '-'}")
    typer.echo(f"verdict:  {stage.get('verdict') or '-'}")
    typer.echo(f"executor: {stage.get('executor') or '-'}")
    typer.echo(f"transport:{transport.get('type') or '-'}")
    typer.echo(f"target:   {stage.get('target_id') or '-'} / {stage.get('target_name') or '-'} / {stage.get('target_kind') or '-'}")
    typer.echo(f"duration: {_fmt_optional_dur(stage.get('duration_seconds'))}")
    typer.echo(f"jobs:     {stage.get('jobs_total') or len(list(stage.get('jobs') or []))} total, {stage.get('jobs_failed') or 0} failed")
    typer.echo(f"artifacts:{artifacts.get('files_total') or artifacts.get('count') or 0}")
    if stage.get("message") or stage.get("error"):
        typer.echo(f"message:  {stage.get('message') or stage.get('error') or '-'}")
    if artifacts:
        typer.echo("")
        typer.echo(
            "\n".join(
                _render_detail_block(
                    "Artifacts",
                    artifacts,
                    [
                        ("files_total", "files_total"),
                        ("primary", "primary"),
                        ("highlights", "highlights"),
                        ("counts_by_category", "counts_by_category"),
                    ],
                )
            )
        )
    jobs = list(stage.get("jobs") or [])
    if jobs:
        typer.echo("")
        typer.echo("Jobs")
        typer.echo(
            "\n".join(
                _render_table(
                    ["job_id", "verdict/state", "attempt", "duration", "transport", "target", "message"],
                    _render_job_rows(stage),
                    max_widths=[20, 16, 8, 10, 12, 16, 72],
                )
            )
        )


def _print_remote_job_detail(payload: dict[str, Any], stage_id: str, job_id: str) -> None:
    stage = _find_run_detail_stage(payload, stage_id)
    if stage is None:
        typer.echo(f"stage_id: {stage_id}")
        typer.echo("error:    stage not found in run summary")
        return
    jobs = [job for job in list(stage.get("jobs") or []) if isinstance(job, dict)]
    target = str(job_id or "").strip()
    selected = next(
        (
            job
            for job in jobs
            if target in {
                str(job.get("job_id") or "").strip(),
                str(job.get("title") or "").strip(),
            }
        ),
        None,
    )
    if selected is None:
        typer.echo(f"stage_id: {stage_id}")
        typer.echo(f"job_id:   {job_id}")
        typer.echo("error:    job not found in stage summary")
        return

    transport = dict(selected.get("transport") or {})
    typer.echo(f"run_id:   {payload.get('run_id') or '-'}")
    typer.echo(f"stage_id: {stage.get('stage_id') or '-'}")
    typer.echo(f"job_id:   {selected.get('job_id') or '-'}")
    typer.echo(f"title:    {selected.get('title') or selected.get('step') or '-'}")
    typer.echo(f"state:    {selected.get('state') or selected.get('verdict') or '-'}")
    typer.echo(f"verdict:  {selected.get('verdict') or '-'}")
    typer.echo(f"attempt:  {selected.get('attempt') if selected.get('attempt') is not None else '-'}")
    typer.echo(f"duration: {_fmt_optional_dur(selected.get('duration_seconds'))}")
    typer.echo(f"transport:{transport.get('type') or '-'}")
    typer.echo(f"target:   {selected.get('target_id') or '-'} / {selected.get('target_name') or '-'} / {selected.get('target_kind') or '-'}")
    typer.echo(f"message:  {selected.get('message') or selected.get('error') or '-'}")
    if selected.get("stdout") or selected.get("stderr"):
        typer.echo("")
        typer.echo("Output")
        if selected.get("stdout"):
            typer.echo("- stdout:")
            typer.echo(str(selected.get("stdout")))
        if selected.get("stderr"):
            typer.echo("- stderr:")
            typer.echo(str(selected.get("stderr")))


def _print_remote_cancel(payload: dict[str, Any]) -> None:
    status = dict(payload.get("status") or {})
    metadata = dict(status.get("metadata") or {})

    typer.echo(f"run_id:   {payload.get('run_id') or '-'}")
    typer.echo(f"accepted: {payload.get('accepted', False)}")
    typer.echo(f"state:    {status.get('state') or '-'}")
    typer.echo(f"verdict:  {status.get('verdict') or '-'}")
    typer.echo(f"message:  {payload.get('message') or status.get('message') or '-'}")
    typer.echo(f"worker:   {metadata.get('worker_id') or '-'}")
    typer.echo(f"reason:   {metadata.get('canceled_reason') or '-'}")
    typer.echo(f"links:    {json.dumps(dict(payload.get('links') or {}), sort_keys=True)}")


def _render_event_rows(payload: dict[str, Any]) -> list[list[object]]:
    items = list(payload.get("items") or [])
    rows: list[list[object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                item.get("seq") or "-",
                item.get("ts") or "-",
                item.get("component") or "-",
                item.get("action") or "-",
                item.get("status") or "-",
                item.get("message") or "-",
            ]
        )
    return rows


def _print_remote_events(payload: dict[str, Any]) -> None:
    typer.echo(f"run_id: {payload.get('run_id') or '-'}")
    typer.echo(f"path:   {payload.get('path') or '-'}")
    typer.echo(f"total:  {payload.get('total', 0)}")
    typer.echo(f"next:   {payload.get('next_offset', 0)}")
    typer.echo(
        "\n".join(
            _render_table(
                ["seq", "ts", "component", "action", "status", "message"],
                _render_event_rows(payload),
                max_widths=[6, 24, 12, 18, 10, 72],
            )
        )
    )


def _print_remote_event_lines(payload: dict[str, Any]) -> list[str]:
    rows = []
    for item in list(payload.get("items") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            "[{ts}] {component}:{action} {status} | {message}".format(
                ts=item.get("ts") or "-",
                component=item.get("component") or "-",
                action=item.get("action") or "-",
                status=item.get("status") or "-",
                message=item.get("message") or "-",
            )
        )
    return rows


def _render_artifact_rows(payload: dict[str, Any]) -> list[list[object]]:
    items = list(payload.get("items") or [])
    rows: list[list[object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        depth = int(item.get("depth") or 0)
        name = str(item.get("name") or item.get("path") or "-")
        if depth > 0:
            name = f"{'  ' * depth}{name}"
        rows.append(
            [
                name,
                item.get("kind") or "-",
                "-" if item.get("size_bytes") is None else str(item.get("size_bytes")),
                item.get("modified_at") or "-",
                item.get("path") or "-",
            ]
        )
    return rows


def _render_artifact_search_rows(payload: dict[str, Any]) -> list[list[object]]:
    items = list(payload.get("items") or [])
    rows: list[list[object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                item.get("path") or "-",
                item.get("line_number") or "-",
                item.get("line") or "-",
            ]
        )
    return rows


def _print_remote_artifacts(payload: dict[str, Any]) -> None:
    typer.echo(f"run_id: {payload.get('run_id') or '-'}")
    typer.echo(f"path:   {payload.get('path') or '-'}")
    typer.echo(f"kind:   {payload.get('kind') or '-'}")
    if payload.get("kind") == "search":
        typer.echo(f"query:  {payload.get('query') or '-'}")
        typer.echo(f"total:  {payload.get('total', 0)}")
        typer.echo(f"next:   {payload.get('next_offset', 0)}")
        typer.echo(
            "\n".join(
                _render_table(
                    ["path", "line", "match"],
                    _render_artifact_search_rows(payload),
                    max_widths=[48, 8, 96],
                )
            )
        )
        return
    if payload.get("kind") == "file":
        typer.echo(f"size:   {payload.get('size_bytes') or 0}")
        typer.echo(f"mime:   {payload.get('mime_type') or '-'}")
        typer.echo(f"text:   {payload.get('is_text', False)}")
        typer.echo(f"truncated: {payload.get('truncated', False)}")
        preview = list(payload.get("preview") or [])
        if preview:
            typer.echo("preview:")
            for line in preview:
                typer.echo(f"  {line}")
        content = payload.get("content")
        if content:
            typer.echo("content:")
            for line in str(content).splitlines():
                typer.echo(f"  {line}")
        return

    typer.echo(f"total:  {payload.get('total', 0)}")
    typer.echo(f"next:   {payload.get('next_offset', 0)}")
    typer.echo(
        "\n".join(
            _render_table(
                ["name", "kind", "size", "modified_at", "path"],
                _render_artifact_rows(payload),
                max_widths=[36, 8, 12, 24, 96],
            )
        )
    )


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _duration_from_status(status: dict[str, Any]) -> float | None:
    started = _parse_iso_datetime(status.get("started_at"))
    ended = _parse_iso_datetime(status.get("ended_at"))
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started).total_seconds())


def _render_compare_rows(payload: dict[str, Any]) -> list[list[object]]:
    left = dict(payload.get("left") or {})
    right = dict(payload.get("right") or {})
    left_status = dict(left.get("status") or {})
    right_status = dict(right.get("status") or {})
    left_metadata = dict(left.get("metadata") or {})
    right_metadata = dict(right.get("metadata") or {})
    return [
        ["run_id", left.get("run_id") or "-", right.get("run_id") or "-", left.get("run_id") == right.get("run_id")],
        ["state", left.get("state") or "-", right.get("state") or "-", left.get("state") == right.get("state")],
        [
            "verdict",
            left_status.get("verdict") or "-",
            right_status.get("verdict") or "-",
            left_status.get("verdict") == right_status.get("verdict"),
        ],
        [
            "team",
            left_metadata.get("team") or "-",
            right_metadata.get("team") or "-",
            left_metadata.get("team") == right_metadata.get("team"),
        ],
        [
            "project",
            left_metadata.get("project") or "-",
            right_metadata.get("project") or "-",
            left_metadata.get("project") == right_metadata.get("project"),
        ],
        [
            "context",
            left_metadata.get("context") or "-",
            right_metadata.get("context") or "-",
            left_metadata.get("context") == right_metadata.get("context"),
        ],
        [
            "duration_seconds",
            _fmt_optional_dur(left.get("duration_seconds")),
            _fmt_optional_dur(right.get("duration_seconds")),
            left.get("duration_seconds") == right.get("duration_seconds"),
        ],
        [
            "tags",
            ", ".join(left.get("tags") or []) or "-",
            ", ".join(right.get("tags") or []) or "-",
            left.get("tags") == right.get("tags"),
        ],
    ]


def _print_compare(payload: dict[str, Any]) -> None:
    typer.echo(f"left:  {payload.get('left_run_id') or '-'}")
    typer.echo(f"right: {payload.get('right_run_id') or '-'}")
    summary = dict(payload.get("summary") or {})
    typer.echo(
        "summary: "
        + ", ".join(
            [
                f"same_state={summary.get('same_state', False)}",
                f"same_verdict={summary.get('same_verdict', False)}",
                f"same_team={summary.get('same_team', False)}",
                f"same_project={summary.get('same_project', False)}",
                f"duration_delta_seconds={summary.get('duration_delta_seconds')}",
            ]
        )
    )
    typer.echo(
        "\n".join(
            _render_table(
                ["field", "left", "right", "same"],
                _render_compare_rows(payload),
                max_widths=[20, 28, 28, 8],
            )
        )
    )


def _print_replay_result(res: RunDispatchResult, context: ContextProfile) -> None:
    typer.echo(f"\n=== {'SUCCESS' if res.ok else 'FAILED'} ===")
    typer.echo(f"run_id:   {res.run_id}")
    typer.echo(f"backend:  {context.api_url or '-'}")
    typer.echo(f"context:  {context.name}")
    typer.echo(f"accepted: {res.accepted}")
    if res.status_code is not None:
        typer.echo(f"status:   {res.status_code}")
    if res.message:
        typer.echo(f"message:  {res.message}")
    if res.payload:
        typer.echo("payload:")
        typer.echo(json.dumps(res.payload, indent=2, sort_keys=True))


def _print_worker_serve_summary(summary: WorkerServeSummary, *, context: ContextProfile, worker_id: str) -> None:
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    typer.echo(f"worker:  {worker_id}")
    typer.echo(f"loops:   {summary.loops}")
    typer.echo(f"claimed: {summary.runs_claimed}")
    typer.echo(f"passed:  {summary.runs_completed}")
    typer.echo(f"failed:  {summary.runs_failed}")
    typer.echo(f"idle:    {summary.idle_polls}")
    typer.echo(f"stopped: {summary.stopped_reason or '-'}")


def _worker_list_rows(workers: list[WorkerStatus]) -> list[list[object]]:
    rows: list[list[object]] = []
    for worker in workers:
        labels = ", ".join(worker.labels or []) or "-"
        heartbeat = _parse_iso_datetime(worker.last_heartbeat_at)
        rows.append(
            [
                worker.worker_id,
                worker.health_state or "-",
                f"{worker.active_leases}/{worker.capacity}",
                worker.mode or "-",
                worker.host or "-",
                worker.user or "-",
                _fmt_ts(heartbeat.timestamp()) if heartbeat is not None else "-",
                labels,
            ]
        )
    return rows


def _print_worker_list(workers: list[WorkerStatus], *, context: ContextProfile) -> None:
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    typer.echo(f"total: {len(workers)}")
    typer.echo(
        "\n".join(
            _render_table(
                ["worker_id", "health", "leases", "mode", "host", "user", "last_heartbeat", "labels"],
                _worker_list_rows(workers),
                max_widths=[24, 12, 8, 10, 24, 16, 24, 64],
            )
        )
    )


def _print_worker_status(worker: WorkerStatus, *, context: ContextProfile) -> None:
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    typer.echo(f"worker_id: {worker.worker_id}")
    typer.echo(f"mode: {worker.mode}")
    typer.echo(f"health: {worker.health_state}")
    typer.echo(f"leases: {worker.active_leases}/{worker.capacity}")
    typer.echo(f"host: {worker.host or '-'}")
    typer.echo(f"user: {worker.user or '-'}")
    typer.echo(f"labels: {', '.join(worker.labels or []) or '-'}")
    typer.echo(f"last_heartbeat: {worker.last_heartbeat_at or '-'}")
    typer.echo(f"created_at: {worker.created_at or '-'}")
    typer.echo(f"updated_at: {worker.updated_at or '-'}")
    if worker.health_reasons:
        typer.echo(f"health_reasons: {', '.join(worker.health_reasons)}")
    if worker.metadata:
        typer.echo("metadata:")
        typer.echo(json.dumps(worker.metadata, indent=2, sort_keys=True))


@app.command("run")
def run_cmd(
    spec: str = typer.Argument(..., help="Path to run spec YAML"),
    suite: Optional[str] = typer.Option(None, "--suite", help="Run only one suite by name"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Resolve the spec using a named profile"),
    mode: Mode = typer.Option("run", "--mode", help="run or dry-run"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Override run id (otherwise spec.run.run_id/name-based)"),
    build: bool = typer.Option(
        False,
        "--build/--no-build",
        help="Force docker image build for selected suite or all stages (executor=docker stages only).",
    ),
    input_value: List[str] = typer.Option(
        [],
        "--input",
        help="Input override KEY=VALUE resolved through the spec inputs section. Can be used multiple times.",
    ),
    lint_spec: bool = typer.Option(False, "--lint-spec", help="Print spec lint findings before execution"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
    verbosity: ConsoleVerbosity | None = typer.Option(
        None,
        "--verbosity",
        help="Console verbosity: quiet, summary, normal, or verbose. Defaults to spec.run.console_verbosity or normal.",
    ),
) -> None:
    inputs_map = parse_assignment_list(list(input_value or []))
    spec_path = Path(spec).expanduser()
    sp, resolved_inputs = _load_spec(
        str(spec_path),
        profile=profile,
        inputs_map=inputs_map,
        lint=bool(lint_spec),
    )
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    opts = RunOptions.from_spec_and_cli(
        sp,
        mode=mode,
        run_id=run_id,
        resolved_inputs=resolved_inputs,
        console_verbosity=verbosity,
    )
    invocation = RunInvocation(
        spec_path=spec_path,
        spec_text=spec_path.read_text(encoding="utf-8"),
        spec=sp,
        resolved_inputs=resolved_inputs,
        mode=mode,
        run_id=opts.run_id,
        suite=suite,
        build=build,
        verbosity=opts.console_verbosity,
        profile=profile,
        context=context,
        lint_spec=bool(lint_spec),
    )
    transport = resolve_transport(context)
    if context.mode == "local":
        pm = PathManager(sp, opts.run_id)
        t0 = time.time()
        _print_begin(
            run_id=opts.run_id,
            run_dir=str(pm.run_dir),
            summary=str(pm.run_summary_path),
            started_at=t0,
        )
        res = transport.execute(invocation)
        t1 = time.time()
        _print_end(ended_at=t1, started_at=t0)
        if json_out:
            typer.echo(json.dumps(res.as_dict(), indent=2, sort_keys=True))
        else:
            _print_summary(res.payload)
    else:
        res = transport.execute(invocation)
        if json_out:
            typer.echo(json.dumps(res.as_dict(), indent=2, sort_keys=True))
        else:
            _print_remote_submission(res, context)

    raise typer.Exit(code=0 if res.ok else 1)


@app.command("runs")
def runs_cmd(
    team: Optional[str] = typer.Option(None, "--team", help="Filter by team"),
    project: Optional[str] = typer.Option(None, "--project", help="Filter by project"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Filter by workspace"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Filter by branch"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Filter by ref"),
    status: List[str] = typer.Option([], "--status", help="Filter by status or verdict. Can be repeated."),
    tag: List[str] = typer.Option([], "--tag", help="Filter by tags. Can be repeated."),
    limit: int = typer.Option(50, "--limit", help="Maximum runs to return"),
    offset: int = typer.Option(0, "--offset", help="Skip this many matching runs"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    query = RunQuery(
        team=team,
        project=project,
        workspace=workspace,
        branch=branch,
        ref=ref,
        status=list(status or []),
        tags=list(tag or []),
        limit=limit,
        offset=offset,
    )
    try:
        payload = transport.list_runs(query)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"backend: {context.api_url or '-'}")
        typer.echo(f"context: {context.name}")
        if any([team, project, workspace, branch, ref, status, tag, limit != 50, offset != 0]):
            active_filters = [
                f"team={team}" if team else None,
                f"project={project}" if project else None,
                f"workspace={workspace}" if workspace else None,
                f"branch={branch}" if branch else None,
                f"ref={ref}" if ref else None,
                f"status={','.join(status)}" if status else None,
                f"tag={','.join(tag)}" if tag else None,
                f"limit={limit}" if limit != 50 else None,
                f"offset={offset}" if offset else None,
            ]
            typer.echo("filters: " + ", ".join(filter(None, active_filters)))
        _print_remote_runs(payload)


@app.command("dashboard")
def dashboard_cmd(
    team: Optional[str] = typer.Option(None, "--team", help="Filter by team"),
    project: Optional[str] = typer.Option(None, "--project", help="Filter by project"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Filter by workspace"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Filter by branch"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Filter by ref"),
    status: List[str] = typer.Option([], "--status", help="Filter by status or verdict. Can be repeated."),
    tag: List[str] = typer.Option([], "--tag", help="Filter by tags. Can be repeated."),
    limit: int = typer.Option(5, "--limit", help="How many recent runs to include"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    query = RunQuery(
        team=team,
        project=project,
        workspace=workspace,
        branch=branch,
        ref=ref,
        status=list(status or []),
        tags=list(tag or []),
        limit=limit,
    )
    try:
        payload = transport.dashboard(query)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"backend: {context.api_url or '-'}")
        typer.echo(f"context: {context.name}")
        if any([team, project, workspace, branch, ref, status, tag, limit != 5]):
            active_filters = [
                f"team={team}" if team else None,
                f"project={project}" if project else None,
                f"workspace={workspace}" if workspace else None,
                f"branch={branch}" if branch else None,
                f"ref={ref}" if ref else None,
                f"status={','.join(status)}" if status else None,
                f"tag={','.join(tag)}" if tag else None,
                f"limit={limit}" if limit != 5 else None,
            ]
            typer.echo("filters: " + ", ".join(filter(None, active_filters)))
        _print_remote_dashboard(payload)


@app.command("show")
def show_cmd(
    run_id: str = typer.Argument(..., help="Run id to show"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.get_run_detail(run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"backend: {context.api_url or '-'}")
        typer.echo(f"context: {context.name}")
        _print_remote_run_detail(payload)


@app.command("events")
def events_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    offset: int = typer.Option(0, "--offset", help="Skip this many events"),
    limit: int = typer.Option(50, "--limit", help="Maximum events to return per poll"),
    follow: bool = typer.Option(False, "--follow/--once", help="Keep polling until the run finishes"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    current_offset = max(0, int(offset))
    printed_any = False

    def _poll() -> dict[str, Any]:
        return transport.get_run_events(run_id, offset=current_offset, limit=limit, follow=follow)

    try:
        payload = _poll()
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_out and not follow:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    typer.echo(f"run_id: {run_id}")
    if payload.get("path"):
        typer.echo(f"path: {payload.get('path')}")
    typer.echo(f"total: {payload.get('total', 0)}")

    while True:
        items = list(payload.get("items") or [])
        if items:
            printed_any = True
            if json_out:
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            else:
                typer.echo("\n".join(_print_remote_event_lines(payload)))
            current_offset = int(payload.get("next_offset") or (current_offset + len(items)))
        elif not follow:
            break

        if not follow:
            break

        state = str(payload.get("state") or "").strip()
        if not state:
            try:
                detail = transport.get_run_detail(run_id)
            except NotImplementedError as exc:
                raise typer.BadParameter(str(exc)) from exc
            state = str((detail.get("status") or {}).get("state") or detail.get("state") or "").strip()
        if state in {"completed", "failed", "canceled", "dry-run"} and not items:
            break
        if state in {"completed", "failed", "canceled"} and items:
            # one last drain already printed, then stop
            next_payload = transport.get_run_events(run_id, offset=current_offset, limit=limit, follow=True)
            if not list(next_payload.get("items") or []):
                break
            payload = next_payload
            continue

        time.sleep(0.5)
        payload = transport.get_run_events(run_id, offset=current_offset, limit=limit, follow=follow)

    if not printed_any and not json_out:
        typer.echo("- no events")


@app.command("stages")
def stages_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.get_run_detail(run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_stages(payload)


@app.command("stage")
def stage_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    stage_id: str = typer.Argument(..., help="Stage id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.get_run_detail(run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_stage_detail(payload, stage_id)


@app.command("jobs")
def jobs_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    stage_id: str = typer.Argument(..., help="Stage id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.get_run_detail(run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_stage_detail(payload, stage_id)


@app.command("job")
def job_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    stage_id: str = typer.Argument(..., help="Stage id to inspect"),
    job_id: str = typer.Argument(..., help="Job id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.get_run_detail(run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_job_detail(payload, stage_id, job_id)


@app.command("artifacts")
def artifacts_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    path: Optional[str] = typer.Option(None, "--path", help="Browse a subtree or file relative to the run root"),
    recursive: bool = typer.Option(True, "--recursive/--flat", help="Browse recursively from the selected path"),
    offset: int = typer.Option(0, "--offset", help="Skip this many entries"),
    limit: int = typer.Option(DEFAULT_ARTIFACT_BROWSER_LIMIT, "--limit", help="Maximum entries to return"),
    search: Optional[str] = typer.Option(None, "--search", help="Search text across artifact files"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.list_run_artifacts(
            run_id,
            path=path,
            recursive=recursive,
            offset=offset,
            limit=limit,
            search=search,
        )
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_artifacts(payload)


@app.command("logs")
def logs_cmd(
    run_id: str = typer.Argument(..., help="Run id to inspect"),
    path: Optional[str] = typer.Option(
        None,
        "--path",
        help="Browse a subtree or file relative to the run logs root",
    ),
    recursive: bool = typer.Option(True, "--recursive/--flat", help="Browse recursively from the selected path"),
    offset: int = typer.Option(0, "--offset", help="Skip this many entries"),
    limit: int = typer.Option(DEFAULT_ARTIFACT_BROWSER_LIMIT, "--limit", help="Maximum entries to return"),
    search: Optional[str] = typer.Option(None, "--search", help="Search text across logs"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    selected_path = path or "logs"
    try:
        payload = transport.list_run_artifacts(
            run_id,
            path=selected_path,
            recursive=recursive,
            offset=offset,
            limit=limit,
            search=search,
        )
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    typer.echo(f"log_path: {selected_path}")
    if search:
        typer.echo(f"search: {search}")
    _print_remote_artifacts(payload)


@app.command("cancel")
def cancel_cmd(
    run_id: str = typer.Argument(..., help="Run id to cancel"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Human-readable cancellation reason"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.cancel_run(run_id, reason=reason)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"backend: {context.api_url or '-'}")
    typer.echo(f"context: {context.name}")
    _print_remote_cancel(payload)


@app.command("compare")
def compare_cmd(
    left_run_id: str = typer.Argument(..., help="Left run id"),
    right_run_id: str = typer.Argument(..., help="Right run id"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    try:
        payload = transport.compare_runs(left_run_id, right_run_id)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"backend: {context.api_url or '-'}")
        typer.echo(f"context: {context.name}")
        _print_compare(payload)


@app.command("replay")
def replay_cmd(
    run_id: str = typer.Argument(..., help="Source run id to replay"),
    mode: str = typer.Option("exact", "--mode", help="Replay mode: exact, failed-only, from-stage, from-job"),
    stage_id: Optional[str] = typer.Option(None, "--stage-id", help="Stage id for partial replay"),
    job_id: Optional[str] = typer.Option(None, "--job-id", help="Job id for partial replay"),
    local: bool = typer.Option(False, "--local/--remote", help="Replay locally instead of on the remote backend"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    transport = resolve_transport(context)
    request = ReplayRequest(
        run_id=run_id,
        mode=mode,  # type: ignore[arg-type]
        stage_id=stage_id,
        job_id=job_id,
        local=local,
    )
    try:
        res = transport.replay_run(request)
    except NotImplementedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_out:
        typer.echo(json.dumps(res.as_dict(), indent=2, sort_keys=True))
    else:
        _print_replay_result(res, context)


@worker_app.command("list")
def worker_list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    if context.mode != "remote":
        raise typer.BadParameter("worker list requires a remote backend context")
    api_url = str(context.api_url or "").strip()
    if not api_url:
        raise typer.BadParameter(f"Remote context '{context.name}' is missing api_url")
    client = WorkerClient(api_url=api_url, token=context.token)
    workers = client.list_workers()
    if json_out:
        typer.echo(json.dumps([worker.model_dump(exclude_none=True) for worker in workers], indent=2, sort_keys=True))
    else:
        _print_worker_list(workers, context=context)


@worker_app.command("status")
def worker_status_cmd(
    worker_id: str = typer.Argument(..., help="Worker id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    if context.mode != "remote":
        raise typer.BadParameter("worker status requires a remote backend context")
    api_url = str(context.api_url or "").strip()
    if not api_url:
        raise typer.BadParameter(f"Remote context '{context.name}' is missing api_url")
    client = WorkerClient(api_url=api_url, token=context.token)
    worker = client.get_worker(worker_id)
    if json_out:
        typer.echo(json.dumps(worker.model_dump(exclude_none=True), indent=2, sort_keys=True))
    else:
        _print_worker_status(worker, context=context)


@worker_app.command("serve")
def worker_serve_cmd(
    worker_id: Optional[str] = typer.Option(None, "--worker-id", help="Worker id to register and claim work with"),
    label: List[str] = typer.Option([], "--label", help="Worker routing label. Can be repeated."),
    policy: str = typer.Option(DEFAULT_QUEUE_POLICY, "--policy", help="Queue claim policy: fifo, lifo, priority, affinity"),
    host: Optional[str] = typer.Option(None, "--host", help="Worker host or endpoint"),
    user: Optional[str] = typer.Option(None, "--user", help="Worker user"),
    capacity: int = typer.Option(DEFAULT_WORKER_CAPACITY, "--capacity", help="Maximum active leases for this worker"),
    max_runs: Optional[int] = typer.Option(None, "--max-runs", help="Stop after claiming this many runs"),
    max_idle_polls: Optional[int] = typer.Option(None, "--max-idle-polls", help="Stop after this many empty polls"),
    idle_sleep_seconds: float = typer.Option(DEFAULT_WORKER_IDLE_SLEEP_SECONDS, "--idle-sleep", help="Sleep between empty polls"),
    heartbeat_interval_seconds: float = typer.Option(DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS, "--heartbeat-interval", help="Heartbeat cadence while running"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout"),
) -> None:
    cfg, _ = _load_cli_config()
    context = get_context(cfg)
    if context.mode != "remote":
        raise typer.BadParameter("worker serve requires a remote backend context")
    api_url = str(context.api_url or "").strip()
    if not api_url:
        raise typer.BadParameter(f"Remote context '{context.name}' is missing api_url")

    resolved_worker_id = str(worker_id or context.metadata.get("worker_id") or context.name).strip() or context.name
    registration = WorkerRegistration(
        worker_id=resolved_worker_id,
        mode="remote",
        host=host or context.metadata.get("worker_host"),
        user=user or context.metadata.get("worker_user"),
        labels=list(label or []),
        capacity=capacity,
        metadata={
            "context": context.name,
            "team": context.team,
            "project": context.project,
            "workspace": context.workspace,
        },
    )
    runtime = RemoteWorkerRuntime(
        client=WorkerClient(api_url=api_url, token=context.token),
        worker=registration,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    summary = runtime.serve(
        worker_id=resolved_worker_id,
        worker_labels=list(label or []),
        policy=policy,
        max_runs=max_runs,
        max_idle_polls=max_idle_polls,
        idle_sleep_seconds=idle_sleep_seconds,
    )
    if json_out:
        typer.echo(json.dumps(asdict(summary), indent=2, sort_keys=True))
    else:
        _print_worker_serve_summary(summary, context=context, worker_id=resolved_worker_id)


@config_app.command("list")
def config_list() -> None:
    cfg, path = _load_cli_config()
    typer.echo(f"config: {path}")
    _print_context_list(cfg)


@config_app.command("show")
def config_show(
    name: Optional[str] = typer.Argument(None, help="Context name to show (defaults to the active context)"),
) -> None:
    cfg, path = _load_cli_config()
    typer.echo(f"config: {path}")
    typer.echo(_render_context_details(cfg, name))


@config_app.command("use")
def config_use(
    name: str = typer.Argument(..., help="Context name to activate"),
) -> None:
    cfg, path = _load_cli_config()
    try:
        cfg = set_active_context(cfg, name)
    except KeyError as e:
        raise typer.BadParameter(str(e)) from e
    save_config(cfg, path)
    typer.echo(f"active context: {name}")


@config_app.command("add")
def config_add(
    name: str = typer.Argument(..., help="Context name to add or replace"),
    mode: str = typer.Option("remote", "--mode", help="Context mode: local or remote"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Backend API URL for remote contexts"),
    token: Optional[str] = typer.Option(None, "--token", help="Access token for remote contexts"),
    team: Optional[str] = typer.Option(None, "--team", help="Default team name"),
    project: Optional[str] = typer.Option(None, "--project", help="Default project name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Default workspace path or label"),
) -> None:
    cfg, path = _load_cli_config()
    normalized_mode = str(mode or "").strip().lower() or "remote"
    if name == "local" and normalized_mode != "local":
        raise typer.BadParameter("The built-in 'local' context must use --mode local")
    if normalized_mode == "remote" and not str(api_url or "").strip():
        raise typer.BadParameter("Remote contexts require --api-url")
    if normalized_mode not in {"local", "remote"}:
        raise typer.BadParameter("Context mode must be either 'local' or 'remote'")
    if normalized_mode == "local":
        api_url = None
        token = None
    ctx = ContextProfile(
        name=name,
        mode=normalized_mode,  # type: ignore[arg-type]
        api_url=api_url,
        token=token,
        team=team,
        project=project,
        workspace=workspace,
    )
    cfg = upsert_context(cfg, ctx)
    save_config(cfg, path)
    typer.echo(f"saved context: {name}")


@config_app.command("set")
def config_set(
    name: str = typer.Argument(..., help="Existing context name to update"),
    mode: str = typer.Option("remote", "--mode", help="Context mode: local or remote"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Backend API URL for remote contexts"),
    token: Optional[str] = typer.Option(None, "--token", help="Access token for remote contexts"),
    team: Optional[str] = typer.Option(None, "--team", help="Default team name"),
    project: Optional[str] = typer.Option(None, "--project", help="Default project name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Default workspace path or label"),
) -> None:
    cfg, path = _load_cli_config()
    if name not in cfg.contexts and name != "local":
        raise typer.BadParameter(f"Unknown context: {name}")
    normalized_mode = str(mode or "").strip().lower() or "remote"
    if name == "local" and normalized_mode != "local":
        raise typer.BadParameter("The built-in 'local' context must use --mode local")
    if normalized_mode == "remote" and not str(api_url or "").strip():
        raise typer.BadParameter("Remote contexts require --api-url")
    if normalized_mode == "local":
        api_url = None
        token = None
    ctx = ContextProfile(
        name=name,
        mode=normalized_mode,  # type: ignore[arg-type]
        api_url=api_url,
        token=token,
        team=team,
        project=project,
        workspace=workspace,
    )
    cfg = upsert_context(cfg, ctx)
    save_config(cfg, path)
    typer.echo(f"updated context: {name}")


app.add_typer(worker_app, name="worker")
app.add_typer(config_app, name="config")


def main(argv: list[str] | None = None) -> None:
    # allow python -m testfabric.cli.main
    app(standalone_mode=True)


if __name__ == "__main__":
    main()
