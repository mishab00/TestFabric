from __future__ import annotations

import json
from pathlib import Path

from testfabric.core.context import StageContext


def resolve_manifest_path(
    ctx: StageContext,
    *,
    manifest_path: str | None,
) -> Path:
    raw = (manifest_path or "").strip()

    if not raw:
        raise ValueError("split.manifest_path is required for manifest-based split")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path(ctx.repo_path) / p).resolve()
    if not p.exists():
        raise ValueError(f"split manifest not found: {p}")
    return p


def load_manifest_groups(
    ctx: StageContext,
    *,
    manifest_path: str | None,
) -> list[list[str]]:
    p = resolve_manifest_path(ctx, manifest_path=manifest_path)

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse split manifest JSON: {p}") from e

    if not isinstance(raw, dict):
        raise ValueError("split manifest must be a JSON object with a 'groups' field")
    if "groups" not in raw:
        raise ValueError("split manifest must contain a top-level 'groups' field")

    groups: list[list[str]] = []
    groups_obj = raw["groups"]
    if not isinstance(groups_obj, list):
        raise ValueError("split manifest 'groups' must be a JSON array")

    for idx, entry in enumerate(groups_obj, start=1):
        if not isinstance(entry, list):
            raise ValueError(f"split manifest group #{idx} must be a list of item ids")
        group = [str(item).strip() for item in entry if str(item).strip()]
        if not group:
            continue
        groups.append(group)

    if not groups:
        raise ValueError(f"split manifest produced no groups: {p}")
    return groups
