from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import TYPE_CHECKING

from testfabric.artifacts.models import ArtifactItem, ArtifactManifest

if TYPE_CHECKING:
    from testfabric.core.context import OutputsConfig, StageContext


_ATTEMPT_RE = re.compile(r"^attempt-(\d+)$")
_STAGE_METADATA_FILES = {"stage-summary.json", "collect-summary.json", "dispatch-summary.json"}


class ArtifactCollector:
    def collect_stage(self, ctx: "StageContext") -> ArtifactManifest:
        stage_root = Path(ctx.stage_tmp_dir).expanduser().resolve()
        items: list[ArtifactItem] = []

        for path in sorted(stage_root.rglob("*")):
            if not path.is_file():
                continue

            rel = path.relative_to(stage_root)
            if rel.name in _STAGE_METADATA_FILES:
                continue

            items.append(self._classify_item(rel, path.stat().st_size, ctx.outputs))

        counts_by_category = Counter(item.category for item in items)
        counts_by_source = Counter(item.source for item in items)

        return ArtifactManifest(
            manifest_version=1,
            run_id=ctx.run.run_id,
            worker_id=ctx.worker_id,
            stage={
                "index": ctx.stage_index,
                "title": ctx.stage_title,
                "suite": ctx.suite_name,
                "kind": ctx.kind,
                "slug": ctx.stage_slug,
            },
            items_total=len(items),
            counts_by_category=dict(sorted(counts_by_category.items())),
            counts_by_source=dict(sorted(counts_by_source.items())),
            items=items,
        )

    def _classify_item(self, rel: Path, size_bytes: int, outputs: "OutputsConfig") -> ArtifactItem:
        parts = rel.parts
        job_id: str | None = None
        attempt: int | None = None

        if parts and parts[0] == "logs":
            return ArtifactItem(
                category="log",
                source="stage_logs",
                path=rel.as_posix(),
                size_bytes=size_bytes,
            )

        if len(parts) >= 4 and parts[0] == "jobs":
            job_id = parts[1]
            attempt = self._parse_attempt(parts[2])
            return ArtifactItem(
                category="log",
                source="job_logs",
                path=rel.as_posix(),
                size_bytes=size_bytes,
                job_id=job_id,
                attempt=attempt,
            )

        if parts and parts[0] == "reports":
            category, job_id, attempt = self._classify_report_file(rel.name, outputs)
            return ArtifactItem(
                category=category,
                source="reports",
                path=rel.as_posix(),
                size_bytes=size_bytes,
                job_id=job_id,
                attempt=attempt,
            )

        return ArtifactItem(
            category="artifact",
            source="stage",
            path=rel.as_posix(),
            size_bytes=size_bytes,
        )

    def _classify_report_file(self, filename: str, outputs: "OutputsConfig") -> tuple[str, str | None, int | None]:
        junit_match = self._match_scoped_output(filename, outputs.junit)
        if junit_match is not None:
            return ("junit", junit_match[0], junit_match[1])

        html_match = self._match_scoped_output(filename, outputs.html)
        if html_match is not None:
            return ("html", html_match[0], html_match[1])

        if filename.endswith(".log"):
            return ("log", None, None)

        return ("artifact", None, None)

    def _match_scoped_output(self, filename: str, configured_name: str | None) -> tuple[str, int] | None:
        raw = (configured_name or "").strip()
        if not raw:
            return None

        if "." in raw:
            stem, ext = raw.rsplit(".", 1)
            pattern = re.compile(rf"^{re.escape(stem)}-(?P<job_id>.+)-a(?P<attempt>\d+)\.{re.escape(ext)}$")
        else:
            pattern = re.compile(rf"^{re.escape(raw)}-(?P<job_id>.+)-a(?P<attempt>\d+)$")

        match = pattern.match(filename)
        if not match:
            return None
        return match.group("job_id"), int(match.group("attempt"))

    def _parse_attempt(self, token: str) -> int | None:
        match = _ATTEMPT_RE.match(str(token or "").strip())
        if not match:
            return None
        return int(match.group(1))
