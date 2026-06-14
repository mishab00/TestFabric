# testfabric/workspace/controller.py
from __future__ import annotations

from typing import Any, Optional

from testfabric.core.context import RepoSnapshot
from testfabric.workspace.gitops import ensure_checkout


class ControllerWorkspace:
    """
    Owns repo lifecycle (checkout/fetch/update) for the run.

    MVP:
      - checkout once on controller, reused across all stages
      - caches result in-memory per instance
    """

    def __init__(self, spec: Any):
        self.spec = spec
        self._workspace: Optional[RepoSnapshot] = None

    def ensure_checked_out(self) -> RepoSnapshot:
        """
        Ensure repository exists and is checked out to spec.run.ref.
        Returns RepoSnapshot(repo_path, commit_sha).
        """
        if self._workspace is not None:
            return self._workspace

        run = getattr(self.spec, "run", None)
        if run is None:
            raise ValueError("spec.run is required")

        repo_url = (getattr(run, "repo_url", None) or "").strip()
        ref = (getattr(run, "ref", None) or "").strip() or "HEAD"
        workdir = (getattr(run, "workdir", None) or "").strip()

        if not repo_url:
            raise ValueError("spec.run.repo_url is required")
        if not workdir:
            raise ValueError("spec.run.workdir is required")

        repo_path, sha = ensure_checkout(repo_url, ref, workdir)

        self._workspace = RepoSnapshot(repo_path=repo_path, commit_sha=sha, repo_url=repo_url, ref=ref)
        return self._workspace
