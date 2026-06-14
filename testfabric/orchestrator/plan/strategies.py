from __future__ import annotations

from testfabric.execution.suites.base import TestItem
from testfabric.orchestrator.plan.split import SplitSpec


def _count_split(items: list[TestItem], *, count: int) -> list[list[TestItem]]:
    groups: list[list[TestItem]] = [[] for _ in range(max(1, int(count)))]
    for idx, item in enumerate(items):
        groups[idx % len(groups)].append(item)
    return groups


def _weighted_count_split(
    items: list[TestItem],
    *,
    count: int,
    item_weights: dict[str, float],
) -> list[list[TestItem]]:
    groups: list[list[TestItem]] = [[] for _ in range(max(1, int(count)))]
    loads: list[float] = [0.0 for _ in groups]

    weighted_items = sorted(
        list(items),
        key=lambda item: (-float(item_weights.get(item.id, 1.0)), item.id),
    )

    for item in weighted_items:
        idx = min(range(len(groups)), key=lambda i: (loads[i], i))
        groups[idx].append(item)
        loads[idx] += float(item_weights.get(item.id, 1.0))
    return groups


def _manifest_split(items: list[TestItem], *, manifest_groups: list[list[str]]) -> list[list[TestItem]]:
    item_by_id: dict[str, TestItem] = {}
    duplicate_ids: set[str] = set()
    for item in items:
        if item.id in item_by_id:
            duplicate_ids.add(item.id)
        item_by_id[item.id] = item

    if duplicate_ids:
        dup = ", ".join(sorted(duplicate_ids)[:5])
        raise ValueError(f"split manifest requires unique collected item ids, duplicates found: {dup}")

    assigned: set[str] = set()
    groups: list[list[TestItem]] = []
    for idx, group_ids in enumerate(manifest_groups, start=1):
        group: list[TestItem] = []
        seen_in_group: set[str] = set()
        for item_id in group_ids:
            key = str(item_id).strip()
            if not key:
                continue
            if key in seen_in_group:
                raise ValueError(f"split manifest group #{idx} contains duplicate item id: {key}")
            if key not in item_by_id:
                raise ValueError(f"split manifest references unknown item id: {key}")
            group.append(item_by_id[key])
            seen_in_group.add(key)
            assigned.add(key)
        if group:
            groups.append(group)

    missing = sorted(set(item_by_id) - assigned)
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"split manifest does not cover collected items: {preview}")
    return groups


def split_items(
    items: list[TestItem],
    *,
    split: SplitSpec,
    manifest_groups: list[list[str]] | None = None,
    item_weights: dict[str, float] | None = None,
) -> list[list[TestItem]]:
    if not split.enabled:
        return [list(items)] if items else []

    if split.manifest_path:
        if not manifest_groups:
            raise ValueError("Manifest split requires loaded manifest groups")
        return _manifest_split(items, manifest_groups=manifest_groups)
    weights = dict(item_weights or {})
    if weights:
        return _weighted_count_split(items, count=split.count, item_weights=weights)
    return _count_split(items, count=split.count)
