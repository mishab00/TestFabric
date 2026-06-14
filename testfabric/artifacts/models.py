from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArtifactItem:
    category: str
    source: str
    path: str
    size_bytes: int
    job_id: str | None = None
    attempt: int | None = None


@dataclass(frozen=True)
class ArtifactManifest:
    manifest_version: int
    run_id: str
    worker_id: str
    stage: dict[str, object]
    items_total: int
    counts_by_category: dict[str, int] = field(default_factory=dict)
    counts_by_source: dict[str, int] = field(default_factory=dict)
    items: list[ArtifactItem] = field(default_factory=list)
