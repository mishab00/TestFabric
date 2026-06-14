from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LintIssue:
    level: str  # error | warning
    path: str
    message: str


def _looks_like_secret(value: str) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    # lightweight heuristic, best-effort only
    return (
        len(s) >= 24
        and not s.startswith("${")
        and not s.startswith("$")
        and any(ch.isdigit() for ch in s)
        and any(ch.isalpha() for ch in s)
    )


def lint_raw_spec(data: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []

    workers = data.get("workers") or {}
    mode = str((workers.get("mode") or "local")).strip().lower()
    if mode in {"linode"}:
        issues.append(
            LintIssue(
                level="error",
                path="workers.mode",
                message=f"workers.mode='{mode}' is declared but not implemented yet",
            )
        )

    for i, st in enumerate(list((data.get("pipeline") or {}).get("stages") or []), start=1):
        ex = str((st or {}).get("executor") or "").strip().lower()
        if ex in {"linode"}:
            issues.append(
                LintIssue(
                    level="error",
                    path=f"pipeline.stages[{i}].executor",
                    message=f"executor '{ex}' is declared but not implemented yet",
                )
            )

    parallel = data.get("parallelism") or {}
    mw = int(parallel.get("max_workers") or 1)
    wmw_raw = workers.get("max_workers")
    if wmw_raw is not None:
        wmw = int(wmw_raw)
    else:
        stage_values = [
            int((st or {}).get("max_workers") or 0)
            for st in list((data.get("pipeline") or {}).get("stages") or [])
        ]
        wmw = max([mw, *stage_values, 1])
    if wmw_raw is not None and mw > wmw:
        issues.append(
            LintIssue(
                level="error",
                path="parallelism.max_workers",
                message=f"parallelism.max_workers ({mw}) cannot exceed workers.max_workers ({wmw})",
            )
        )

    suites = data.get("suites") or {}
    for suite_name, suite_cfg in suites.items():
        extra_env = dict((suite_cfg or {}).get("extra_env") or {})
        for k, v in extra_env.items():
            if isinstance(v, str) and _looks_like_secret(v):
                issues.append(
                    LintIssue(
                        level="warning",
                        path=f"suites.{suite_name}.extra_env.{k}",
                        message="looks like a hardcoded secret; prefer ${ENV_VAR}",
                    )
                )

    return issues
