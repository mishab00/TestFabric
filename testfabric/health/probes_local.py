from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable

from testfabric.health.models import HealthCheckResult
from testfabric.health.policy import HealthPolicy


class LocalHealthProbeSuite:
    def __init__(self, policy: HealthPolicy):
        self.policy = policy

    def probe(self, *, writable_paths: Iterable[str | Path]) -> list[HealthCheckResult]:
        checks: list[HealthCheckResult] = []
        paths = self._normalize_paths(writable_paths)

        checks.append(self._probe_disk(paths))

        if self.policy.min_mem_gb is not None:
            checks.append(self._probe_memory())

        checks.extend(self._probe_writable_paths(paths))
        return checks

    def _normalize_paths(self, values: Iterable[str | Path]) -> list[Path]:
        out: list[Path] = []
        seen: set[str] = set()
        for raw in values:
            p = Path(raw).expanduser().resolve()
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        if not out:
            out.append(Path.cwd().resolve())
        return out

    def _probe_disk(self, paths: list[Path]) -> HealthCheckResult:
        target = None
        free_bytes = None
        total_bytes = None

        for path in paths:
            candidate = path if path.exists() else path.parent
            candidate.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(candidate)
            if free_bytes is None or usage.free < free_bytes:
                free_bytes = int(usage.free)
                total_bytes = int(usage.total)
                target = candidate

        free_gb = (free_bytes or 0) / float(1024 ** 3)
        minimum_gb = max(0, int(self.policy.min_disk_gb))
        ok = free_gb >= minimum_gb
        message = (
            f"Available disk {free_gb:.2f} GiB is below minimum {minimum_gb} GiB at {target}"
            if not ok
            else ""
        )
        return HealthCheckResult(
            name="disk",
            ok=ok,
            severity="required",
            message=message,
            details={
                "path": str(target) if target is not None else None,
                "free_bytes": int(free_bytes or 0),
                "total_bytes": int(total_bytes or 0),
                "min_disk_gb": minimum_gb,
            },
        )

    def _probe_memory(self) -> HealthCheckResult:
        available = self._available_memory_bytes()
        minimum_gb = int(self.policy.min_mem_gb or 0)

        if available is None:
            return HealthCheckResult(
                name="memory",
                ok=False,
                severity="warning",
                message="Could not determine available memory on this host.",
                details={"min_mem_gb": minimum_gb},
            )

        available_gb = available / float(1024 ** 3)
        ok = available_gb >= minimum_gb
        message = (
            f"Available memory {available_gb:.2f} GiB is below minimum {minimum_gb} GiB."
            if not ok
            else ""
        )
        return HealthCheckResult(
            name="memory",
            ok=ok,
            severity="required",
            message=message,
            details={"available_bytes": int(available), "min_mem_gb": minimum_gb},
        )

    def _probe_writable_paths(self, paths: list[Path]) -> list[HealthCheckResult]:
        checks: list[HealthCheckResult] = []
        for path in paths:
            try:
                path.mkdir(parents=True, exist_ok=True)
                marker = path / f".testfabric-health-{uuid.uuid4().hex}"
                marker.write_text("ok\n", encoding="utf-8")
                marker.unlink()
                checks.append(
                    HealthCheckResult(
                        name=f"writable:{path}",
                        ok=True,
                        severity="required",
                        details={"path": str(path)},
                    )
                )
            except Exception as e:
                checks.append(
                    HealthCheckResult(
                        name=f"writable:{path}",
                        ok=False,
                        severity="required",
                        message=f"Path is not writable: {path} ({e})",
                        details={"path": str(path)},
                    )
                )
        return checks

    def _available_memory_bytes(self) -> int | None:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            return int(page_size) * int(avail_pages)
        except (AttributeError, OSError, ValueError):
            return None
