# testfabric/orchestrator/plan/compiler.py
from __future__ import annotations

from urllib.parse import urlparse

from testfabric.spec.schema import DockerTransport, RunSpec, StageSection
from testfabric.orchestrator.plan.models import RunPlan, StagePlan, Mode
from testfabric.workers.targets import load_targets_file, select_targets


def _normalize_selector(selector: str) -> str:
    raw = str(selector or "").strip()
    if not raw:
        return raw
    if raw == "all" or raw.startswith("group:") or raw.startswith("host:"):
        return raw
    return f"group:{raw}"


def _grouped_targets(spec: RunSpec) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for group, hosts in spec.target_group_map().items():
        for host_id, host in hosts.items():
            out.append(
                {
                    "host_id": host_id,
                    "name": str(host.name or host_id),
                    "address": host.address,
                    "groups": [group],
                    "labels": dict(host.labels or {}),
                    "credential": host.credential,
                    "transport": host.transport.model_dump(exclude_unset=True, exclude_none=False) if host.transport is not None else {},
                }
            )
    return out


def _select_inline_targets(spec: RunSpec, selector: str) -> list[dict[str, object]]:
    normalized = _normalize_selector(selector)
    return list(select_targets(_grouped_targets(spec), normalized))


def _merge_transport_layer(
    merged: dict[str, object],
    layer: dict[str, object],
) -> dict[str, object]:
    for key, value in layer.items():
        if value is None:
            merged.pop(str(key), None)
            continue
        merged[str(key)] = value
    return merged


def _transport_dict(transport: DockerTransport | dict[str, object] | None, *, include_none: bool = False) -> dict[str, object]:
    if transport is None:
        return {}
    if isinstance(transport, DockerTransport):
        return transport.model_dump(exclude_none=not include_none)
    return dict(transport)


def _watch_dict(watch: object | None) -> dict[str, object]:
    if watch is None:
        return {}
    model_dump = getattr(watch, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(exclude_none=True))
    if isinstance(watch, dict):
        return dict(watch)
    return {}


def _inventory_targets(spec: RunSpec) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for group, hosts in spec.target_group_map().items():
        for host_id, host in hosts.items():
            payload = {
                "host_id": host_id,
                "name": str(host.name or host_id),
                "address": host.address,
                "groups": [group],
                "labels": dict(host.labels or {}),
                "credential": host.credential,
                "transport": host.transport.model_dump(exclude_unset=True, exclude_none=False) if host.transport is not None else {},
            }
            targets.append(payload)
    return targets


def _resolve_watch_inventory_target(spec: RunSpec, target_ref: str) -> dict[str, object]:
    raw = str(target_ref or "").strip()
    if not raw:
        raise ValueError("remote_file target reference cannot be empty")

    targets = _inventory_targets(spec)
    exact_map: dict[str, dict[str, object]] = {}
    for target in targets:
        for alias in (str(target.get("host_id") or "").strip(), str(target.get("name") or "").strip()):
            if not alias:
                continue
            existing = exact_map.get(alias)
            if existing is not None and existing != target:
                raise ValueError(f"Ambiguous remote_file target alias '{alias}'")
            exact_map[alias] = target

    if raw in exact_map:
        return exact_map[raw]

    selector = raw if raw in {"all"} or raw.startswith("host:") or raw.startswith("group:") else f"group:{raw}"
    selected = list(select_targets(targets, selector))
    if len(selected) != 1:
        raise ValueError(f"remote_file target '{raw}' must resolve to exactly one host, got {len(selected)}")
    return dict(selected[0])


def _resolve_remote_ssh_source(spec: RunSpec, source: dict[str, object]) -> dict[str, object]:
    source_type = str(source.get("type") or "").strip()
    if source_type not in {"remote_file", "metric"}:
        return source

    target_ref = str(source.get("target") or "").strip()
    if not target_ref:
        return source

    target = _resolve_watch_inventory_target(spec, target_ref)
    merged: dict[str, object] = {}
    merged = _merge_transport_layer(merged, _transport_dict(spec.workers.transport))
    credential_name = str(target.get("credential") or "").strip()
    if credential_name:
        credential = spec.credential_map().get(credential_name)
        if credential is None:
            raise ValueError(f"Unknown credential '{credential_name}' referenced by remote_file target '{target_ref}'")
        merged = _merge_transport_layer(merged, _transport_dict(credential))
    merged = _merge_transport_layer(merged, _transport_dict(target.get("transport") or {}, include_none=True))

    host = str(source.get("host") or target.get("address") or merged.get("host") or "").strip()
    if not host:
        raise ValueError(f"{source_type} target '{target_ref}' must provide a host/address")

    resolved = dict(source)
    resolved.setdefault("target", target_ref)
    resolved["host"] = host
    for field in ("user", "port", "key_path", "known_hosts", "via"):
        if resolved.get(field) in (None, "", False):
            value = merged.get(field)
            if value not in (None, "", False):
                resolved[field] = value
    if str(merged.get("password") or "").strip():
        raise ValueError(f"{source_type} sources do not support SSH password auth; use key-based SSH access")
    return resolved


def _resolve_watch_sources(spec: RunSpec, node: object) -> object:
    if isinstance(node, dict):
        resolved = {str(key): _resolve_watch_sources(spec, value) for key, value in node.items()}
        if str(resolved.get("type") or "").strip() in {"remote_file", "metric"}:
            return _resolve_remote_ssh_source(spec, resolved)
        return resolved
    if isinstance(node, list):
        return [_resolve_watch_sources(spec, item) for item in node]
    return node


def _merge_docker_transport(spec: RunSpec, *, credential_name: str | None, host_transport: dict[str, object] | None) -> dict[str, object]:
    merged: dict[str, object] = {}
    merged = _merge_transport_layer(merged, _transport_dict(spec.workers.transport))
    if credential_name:
        credential = spec.credential_map().get(credential_name)
        if credential is None:
            raise ValueError(f"Unknown credential '{credential_name}' referenced by target")
        merged = _merge_transport_layer(merged, _transport_dict(credential))
    merged = _merge_transport_layer(merged, _transport_dict(host_transport, include_none=True))
    return {str(key): value for key, value in merged.items() if value is not None}


def _docker_endpoint_mode(base_url: str | None) -> str:
    scheme = str(urlparse(str(base_url or "").strip()).scheme or "").strip().lower()
    if scheme in {"ssh", "tcp", "https"}:
        return scheme
    return "ssh"


def _docker_endpoint_base_url(
    *,
    merged_transport: dict[str, object],
    host: str,
    user: str,
    port: str,
) -> tuple[str, str]:
    explicit_base_url = str(merged_transport.get("base_url") or "").strip()
    if explicit_base_url:
        mode = _docker_endpoint_mode(explicit_base_url)
        return explicit_base_url, mode

    tls_value = merged_transport.get("tls")
    if tls_value not in (None, False, ""):
        url = f"https://{host}"
        if port and port != "2376":
            url = f"{url}:{port}"
        return url, "https"

    url = f"ssh://{user}@{host}"
    if port and port != "22":
        url = f"{url}:{port}"
    return url, "ssh"


def _resolve_stage_targets(spec: RunSpec, *, executor: str, selector: str | None = None) -> list[dict[str, object]]:
    selector = str(selector or "").strip()

    if executor == "docker" and selector:
        targets = _select_inline_targets(spec, selector)
        resolved: list[dict[str, object]] = []
        for target in targets:
            merged_transport = _merge_docker_transport(
                spec,
                credential_name=str(target.get("credential") or "").strip() or None,
                host_transport=dict(target.get("transport") or {}),
            )
            password = str(merged_transport.get("password") or "").strip()
            if password:
                raise ValueError("Remote Docker targets do not support SSH password auth; use key-based SSH access")
            host = str(merged_transport.get("host") or target.get("address") or "").strip()
            user = str(merged_transport.get("user") or "root").strip() or "root"
            if not host:
                raise ValueError("Docker target requires an address or explicit base_url")
            port_hint = "2376" if merged_transport.get("tls") not in (None, False, "") else "22"
            port = str(merged_transport.get("port") or port_hint).strip() or port_hint
            base_url, mode = _docker_endpoint_base_url(
                merged_transport=merged_transport,
                host=host,
                user=user,
                port=port,
            )
            parsed = urlparse(base_url)
            host = str(parsed.hostname or host).strip()
            if parsed.username:
                user = parsed.username
            if parsed.port:
                port = str(parsed.port)
            docker_endpoint = {
                "mode": mode,
                "base_url": base_url,
                "host": host,
                "user": user,
                "port": port,
                "key_path": str(merged_transport.get("key_path") or "").strip(),
                "known_hosts": str(merged_transport.get("known_hosts") or "").strip(),
                "via": str(merged_transport.get("via") or "").strip(),
                "tls": merged_transport.get("tls"),
            }
            resolved.append(
                {
                    "target_id": str(target["host_id"]),
                    "target_name": str(target["name"]),
                    "target_kind": "docker-target",
                    "target_group": None if _normalize_selector(selector) in {"all", f"host:{target['host_id']}", f"host:{target['name']}"} else (
                        _normalize_selector(selector).split(":", 1)[1] if _normalize_selector(selector).startswith("group:") else ((target.get("groups") or [None])[0])
                    ),
                    "target_address": host,
                    "target_labels": dict(target.get("labels") or {}),
                    "target_credential": target.get("credential"),
                    "docker_endpoint": docker_endpoint,
                    "transport_type": mode,
                }
            )
        return resolved

    return [
        {
            "target_id": "local",
            "target_name": "local",
            "target_kind": "local",
            "target_group": None,
            "target_address": None,
            "target_labels": {},
            "docker_endpoint": {},
            "transport_type": executor,
        }
    ]


def compile_run_plan(
    spec: RunSpec,
    *,
    run_id: str,
    mode: Mode,
    suite_override: str | None = None,
    build: bool = False,  # NEW: force build
) -> RunPlan:
    # 1) choose stage list
    if suite_override:
        if suite_override not in spec.suites:
            raise ValueError(f"Unknown suite '{suite_override}'. Available: {sorted(spec.suites.keys())}")
        # Single-stage run (suite override) - allow build flag
        stages = [StageSection(title=f"run {suite_override}", suite=suite_override, build=bool(build))]
    else:
        stages = list(spec.pipeline.stages or [])
        if not stages:
            raise ValueError("pipeline.stages is empty")

    pool_cap = int(spec.effective_worker_capacity())

    run_watch = _resolve_watch_sources(spec, _watch_dict(getattr(spec, "watch", None)))
    out: list[StagePlan] = []
    for st in stages:
        suite_name = (st.suite or "").strip()
        if not suite_name:
            raise ValueError("stage.suite is required")
        if suite_name not in spec.suites:
            raise ValueError(f"Unknown suite '{suite_name}'. Available: {sorted(spec.suites.keys())}")

        suite_cfg = spec.suites[suite_name]
        kind = suite_cfg.kind  # "pytest" | "command"

        # 2) resolve executor
        # 2) resolve executor (stage > suite > pipeline default > inferred)
        ex = (st.executor or "").strip()
        if not ex:
            ex = (getattr(suite_cfg, "executor", None) or "").strip()
        if not ex:
            ex = (spec.pipeline.default_executor or "").strip()
        if not ex:
            ex = "docker" if kind == "pytest" else "local"
        if ex not in ("docker", "local"):
            raise ValueError(f"Invalid executor '{ex}' for stage '{suite_name}'")

        # 3) resolve runner
        rn = (st.runner or "").strip()
        if not rn:
            rn = (getattr(suite_cfg, "runner", None) or "").strip()
        if not rn:
            rn = kind
        if rn not in ("pytest", "command", "expect"):
            raise ValueError(f"Invalid runner '{rn}' for stage '{suite_name}'")
        if rn != kind:
            raise ValueError(f"stage.runner ({rn}) must match suite kind ({kind})")

        # 4) resolve parallelism
        mw = int(st.max_workers or spec.parallelism.max_workers)
        cs = int(st.chunk_size or spec.parallelism.chunk_size)
        mr = int(st.max_retries or spec.parallelism.max_retries)

        if mw <= 0:
            raise ValueError(f"stage.max_workers must be >= 1, got {mw}")
        if cs <= 0:
            raise ValueError(f"stage.chunk_size must be >= 1, got {cs}")
        if mr < 0:
            raise ValueError(f"stage.max_retries must be >= 0, got {mr}")

        if mw > pool_cap:
            raise ValueError(f"stage.max_workers ({mw}) cannot exceed workers.max_workers ({pool_cap})")

        execution = getattr(st, "execution", None)
        execution_mode = (getattr(execution, "mode", "once") or "once").strip()
        execution_count = int(getattr(execution, "count", 1) or 1)
        if execution_mode not in ("once", "repeat"):
            raise ValueError(f"Invalid execution.mode '{execution_mode}' for stage '{suite_name}'")
        if execution_count <= 0:
            raise ValueError(f"stage.execution.count must be >= 1, got {execution_count}")

        split = getattr(st, "split", None)
        split_count = int(getattr(split, "count", 1) or 1)
        split_manifest_path = getattr(split, "manifest_path", None)
        if split_count <= 0:
            raise ValueError(f"stage.split.count must be >= 1, got {split_count}")
        if split_manifest_path and split_count != 1:
            raise ValueError("stage.split.count cannot be combined with split.manifest_path")

        timeout = getattr(st, "timeout", None)
        job_timeout_seconds = getattr(timeout, "job_seconds", None)
        stage_timeout_seconds = getattr(timeout, "stage_seconds", None)
        if job_timeout_seconds is not None:
            job_timeout_seconds = int(job_timeout_seconds)
            if job_timeout_seconds <= 0:
                raise ValueError(f"stage.timeout.job_seconds must be >= 1, got {job_timeout_seconds}")
        if stage_timeout_seconds is not None:
            stage_timeout_seconds = int(stage_timeout_seconds)
            if stage_timeout_seconds <= 0:
                raise ValueError(f"stage.timeout.stage_seconds must be >= 1, got {stage_timeout_seconds}")

        lifecycle = getattr(st, "lifecycle", None)
        lifecycle_mode = (getattr(lifecycle, "mode", "run") or "run").strip()
        lifecycle_when = (getattr(lifecycle, "when", "on_success") or "on_success").strip()
        if lifecycle_mode not in ("run", "setup", "teardown"):
            raise ValueError(f"Invalid lifecycle.mode '{lifecycle_mode}' for stage '{suite_name}'")
        if lifecycle_when not in ("on_success", "always"):
            raise ValueError(f"Invalid lifecycle.when '{lifecycle_when}' for stage '{suite_name}'")

        targets = _resolve_stage_targets(
            spec,
            executor=ex,
            selector=str(getattr(st, "targets", None) or "").strip() or None,
        )
        for target in targets:
            out.append(
                StagePlan(
                    run_id=run_id,
                    mode=mode,  # type: ignore[arg-type]
                    index=len(out) + 1,
                    title=(st.title or "").strip(),
                    suite_name=suite_name,
                    kind=kind,      # type: ignore[arg-type]
                    executor=ex,    # type: ignore[arg-type]
                    runner=rn,      # type: ignore[arg-type]
                    target_id=str(target["target_id"]),
                    target_name=str(target["target_name"]),
                    target_kind=str(target["target_kind"]),
                    target_group=target["target_group"],
                    target_address=target["target_address"],
                    target_labels=dict(target["target_labels"]),
                    target_credential=str(target.get("target_credential") or "") or None,
                    docker_transport=dict(target.get("docker_transport") or {}),
                    docker_endpoint=dict(target.get("docker_endpoint") or {}),
                    transport_type=str(target["transport_type"]) if target["transport_type"] is not None else None,
                    build=bool(st.build) or bool(build),
                    execution_mode=execution_mode,  # type: ignore[arg-type]
                    execution_count=execution_count,
                    split_count=split_count,
                    split_manifest_path=str(split_manifest_path).strip() if split_manifest_path else None,
                    job_timeout_seconds=job_timeout_seconds,
                    stage_timeout_seconds=stage_timeout_seconds,
                    lifecycle_mode=lifecycle_mode,  # type: ignore[arg-type]
                    lifecycle_when=lifecycle_when,  # type: ignore[arg-type]
                    max_workers=mw,
                    chunk_size=cs,
                    max_retries=mr,
                    env=dict(getattr(st, "env", {}) or {}),
                )
            )

    return RunPlan(run_id=run_id, mode=mode, stages=out, watch=run_watch)
