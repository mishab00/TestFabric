# testfabric/artifacts/layout.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactsLayout:
    """
    Spec-free artifacts layout.

    The spec can *produce* this layout, but runtime code should depend on this,
    not on the entire spec object.
    """
    artifacts_root: Path           # e.g. ./artifacts
    runs_subdir: str               # e.g. "runs"
    workers_tmp_root: Path         # e.g. ./artifacts/workers_tmp

    @property
    def runs_root(self) -> Path:
        return (self.artifacts_root / self.runs_subdir).resolve()

    @classmethod
    def from_spec(cls, spec: Any) -> "ArtifactsLayout":
        run = getattr(spec, "run", None)
        if run is None:
            raise ValueError("spec.run is required to build ArtifactsLayout")

        artifacts_root = Path(getattr(run, "artifacts_dir", "artifacts")).expanduser().resolve()
        runs_subdir = (getattr(run, "runs_subdir", "runs") or "runs").strip() or "runs"
        raw_workers_tmp = getattr(run, "workers_tmp", None)
        if raw_workers_tmp is None or not str(raw_workers_tmp).strip():
            workers_tmp_root = (artifacts_root / "workers_tmp").resolve()
        else:
            workers_tmp_root = Path(str(raw_workers_tmp).strip()).expanduser().resolve()

        return cls(
            artifacts_root=artifacts_root,
            runs_subdir=runs_subdir,
            workers_tmp_root=workers_tmp_root,
        )
