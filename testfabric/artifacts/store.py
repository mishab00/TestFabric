# testfabric/artifacts/store.py
from __future__ import annotations

import shutil
from pathlib import Path

from testfabric.artifacts.paths import PathManager, StageRef


class ArtifactStore:
    """
    Mirrors worker tmp artifacts into the canonical run_dir tree.
    """

    def __init__(self, paths: PathManager):
        self.paths = paths

    def mirror_stage(self, worker_id: str, stage_ref: StageRef) -> None:
        """
        Mirror:
          <workers_tmp>/<run_id>/<worker_id>/stages/<stage_slug>/...
        into:
          <run_dir>/workers/<worker_id>/stages/<stage_slug>/...

        This makes StageCoordinator summary_rel always valid:
          workers/<worker_id>/stages/<stage_slug>/stage-summary.json
        """
        src = self.paths.worker_stage_dir(worker_id, stage_ref)
        dst = self.paths.mirror_worker_dir(worker_id) / "stages" / stage_ref.slug

        if not src.exists():
            return

        dst.parent.mkdir(parents=True, exist_ok=True)

        # overwrite strategy: delete dst then copy
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)

        shutil.copytree(src, dst)
