from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TargetNode:
    host_id: str
    name: str
    address: str | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)
    labels: dict[str, str] = field(default_factory=dict)
    credential: str | None = None
    transport: dict[str, Any] = field(default_factory=dict)


def _stringify_map(data: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in dict(data or {}).items():
        k = str(key).strip()
        if not k:
            continue
        out[k] = str(value)
    return out


def _normalize_host(name: str, data: Any, *, groups: set[str] | None = None) -> TargetNode:
    payload = dict(data or {}) if isinstance(data, dict) else {}
    address = str(
        payload.get("address")
        or payload.get("host")
        or payload.get("hostname")
        or payload.get("ansible_host")
        or ""
    ).strip() or None
    labels = _stringify_map(payload.get("labels"))
    credential = str(payload.get("credential") or "").strip() or None
    transport = dict(payload.get("transport") or {}) if isinstance(payload.get("transport"), dict) else {}
    host_groups = sorted(set(groups or set()) | set(str(item).strip() for item in list(payload.get("groups") or []) if str(item).strip()))
    display_name = str(payload.get("name") or name).strip() or name
    return TargetNode(
        host_id=name,
        name=display_name,
        address=address,
        groups=tuple(host_groups),
        labels=labels,
        credential=credential,
        transport=transport,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Targets YAML must parse to a mapping/dict at top-level.")
    return data


def _load_hosts_section(data: dict[str, Any]) -> dict[str, TargetNode]:
    hosts_section = data.get("hosts")
    if not isinstance(hosts_section, dict):
        return {}
    out: dict[str, TargetNode] = {}
    for raw_name, raw_data in hosts_section.items():
        name = str(raw_name).strip()
        if not name:
            continue
        out[name] = _normalize_host(name, raw_data)
    return out


def _load_hierarchical_targets(data: dict[str, Any]) -> dict[str, TargetNode]:
    all_section = data.get("all")
    if not isinstance(all_section, dict):
        return {}

    groups_by_host: dict[str, set[str]] = {}
    host_payloads: dict[str, Any] = {}

    all_hosts = all_section.get("hosts")
    if isinstance(all_hosts, dict):
        for raw_name, raw_data in all_hosts.items():
            name = str(raw_name).strip()
            if not name:
                continue
            host_payloads[name] = raw_data

    children = all_section.get("children")
    if isinstance(children, dict):
        for raw_group, group_body in children.items():
            group = str(raw_group).strip()
            if not group or not isinstance(group_body, dict):
                continue
            group_hosts = group_body.get("hosts")
            if not isinstance(group_hosts, dict):
                continue
            for raw_name, raw_data in group_hosts.items():
                name = str(raw_name).strip()
                if not name:
                    continue
                host_payloads.setdefault(name, raw_data)
                groups_by_host.setdefault(name, set()).add(group)

    out: dict[str, TargetNode] = {}
    for name, raw_data in host_payloads.items():
        out[name] = _normalize_host(name, raw_data, groups=groups_by_host.get(name))
    return out


def _load_grouped_targets(data: dict[str, Any]) -> dict[str, TargetNode]:
    reserved = {"file"}
    out: dict[str, TargetNode] = {}
    for raw_group, raw_hosts in data.items():
        group = str(raw_group).strip()
        if not group or group in reserved:
            continue
        if not isinstance(raw_hosts, dict):
            continue
        for raw_name, raw_data in raw_hosts.items():
            name = str(raw_name).strip()
            if not name:
                continue
            target = _normalize_host(name, raw_data, groups={group})
            if name in out:
                merged_groups = set(out[name].groups) | {group} | set(target.groups)
                merged_labels = dict(out[name].labels)
                merged_labels.update(target.labels)
                merged_transport = dict(out[name].transport)
                merged_transport.update(target.transport)
                out[name] = TargetNode(
                    host_id=out[name].host_id,
                    name=target.name or out[name].name,
                    address=target.address or out[name].address,
                    groups=tuple(sorted(merged_groups)),
                    labels=merged_labels,
                    credential=target.credential or out[name].credential,
                    transport=merged_transport,
                )
            else:
                out[name] = target
    return out


def load_targets_mapping(data: dict[str, Any]) -> list[TargetNode]:
    hosts = _load_hosts_section(data)
    if not hosts:
        hosts = _load_hierarchical_targets(data)
    if not hosts:
        hosts = _load_grouped_targets(data)
    if not hosts:
        raise ValueError("Targets must define hosts either as grouped mappings, under 'hosts:', or under 'all.hosts:'.")
    return sorted(hosts.values(), key=lambda item: item.host_id)


def dump_grouped_targets(targets: list[TargetNode]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for target in targets:
        groups = list(target.groups) or ["default"]
        for group in groups:
            grouped.setdefault(group, {})
            payload: dict[str, Any] = {}
            if target.name and target.name != target.host_id:
                payload["name"] = target.name
            if target.address:
                payload["address"] = target.address
            if target.labels:
                payload["labels"] = dict(target.labels)
            if target.credential:
                payload["credential"] = target.credential
            if target.transport:
                payload["transport"] = dict(target.transport)
            grouped[group][target.host_id] = payload
    return grouped


def load_targets_file(path: str, *, base_dir: str | Path | None = None) -> list[TargetNode]:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("targets path is required")

    targets_path = Path(raw).expanduser()
    if not targets_path.is_absolute() and base_dir is not None:
        targets_path = Path(base_dir).expanduser() / targets_path
    targets_path = targets_path.resolve()

    if not targets_path.exists():
        raise ValueError(f"Targets file not found: {targets_path}")

    data = _load_yaml(targets_path)
    return load_targets_mapping(data)


def _target_field(target: dict[str, Any] | TargetNode, field: str, default: Any = None) -> Any:
    if isinstance(target, dict):
        return target.get(field, default)
    return getattr(target, field, default)


def select_targets(targets: list[dict[str, Any]] | list[TargetNode], selector: str) -> list[Any]:
    raw = str(selector or "").strip()
    if not raw:
        raise ValueError("target selector is required")

    if raw == "all":
        selected = list(targets)
    elif raw.startswith("host:"):
        wanted = raw.split(":", 1)[1].strip()
        selected = [
            target
            for target in targets
            if _target_field(target, "host_id") == wanted or _target_field(target, "name") == wanted
        ]
    elif raw.startswith("group:"):
        wanted = raw.split(":", 1)[1].strip()
        selected = [
            target
            for target in targets
            if wanted in set(_target_field(target, "groups", ()) or ())
        ]
    else:
        raise ValueError(f"Unsupported selector: {raw}")

    if not selected:
        raise ValueError(f"No targets matched selector '{raw}'")
    return selected
