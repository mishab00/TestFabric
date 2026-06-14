from __future__ import annotations

from pathlib import Path
import hashlib
import re
from typing import Tuple

from git import Repo, GitCommandError, InvalidGitRepositoryError, NoSuchPathError


class GitOpsError(RuntimeError):
    pass


_NON_ALNUM = re.compile(r"[^A-Za-z0-9._-]+")


def _normalize_repo_url(repo_url: str) -> str:
    raw = str(repo_url or "").strip()
    if not raw:
        return ""

    if raw.startswith("file://"):
        return str(Path(raw[7:]).expanduser().resolve())

    try:
        p = Path(raw).expanduser()
        if p.exists():
            return str(p.resolve())
    except OSError:
        pass

    return raw


def _slugify(value: str, *, default: str) -> str:
    text = _NON_ALNUM.sub("-", str(value or "").strip()).strip("-._")
    return text[:48] or default


def _identity_segment(value: str, *, default: str) -> str:
    normalized = str(value or "").strip()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{_slugify(normalized, default=default)}-{digest}"


def _repo_checkout_path(base: Path, repo_url: str, ref: str, repos_subdir: str = "repos") -> Path:
    repo_key = _identity_segment(_normalize_repo_url(repo_url), default="repo")
    ref_key = _identity_segment(ref or "HEAD", default="head")
    return base / repos_subdir / repo_key / ref_key


def _require_matching_origin(repo: Repo, repo_url: str, repo_path: Path) -> None:
    expected = _normalize_repo_url(repo_url)
    if not expected:
        raise GitOpsError("Repository URL is empty.")

    try:
        origin = repo.remotes.origin
    except AttributeError as e:
        raise GitOpsError(f"Repository at {repo_path} has no origin remote; refusing to reuse it.") from e

    actual_urls = [_normalize_repo_url(u) for u in origin.urls]
    if expected not in actual_urls:
        joined = ", ".join(actual_urls) if actual_urls else "<none>"
        raise GitOpsError(
            f"Repository at {repo_path} points to a different origin ({joined}); expected {expected}."
        )


def _ensure_repo(repo_url: str, repo_path: Path) -> Repo:
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        repo = Repo(str(repo_path))
        if repo.bare:
            raise GitOpsError(f"Repository at {repo_path} is bare; expected a working tree.")
        _require_matching_origin(repo, repo_url, repo_path)
        return repo
    except (InvalidGitRepositoryError, NoSuchPathError):
        try:
            return Repo.clone_from(repo_url, str(repo_path))
        except GitCommandError as e:
            raise GitOpsError(f"Failed to clone repo: {repo_url} -> {repo_path}: {e}") from e


def _fetch_all(repo: Repo) -> None:
    try:
        for remote in repo.remotes:
            remote.fetch(tags=True, prune=True)
    except GitCommandError as e:
        raise GitOpsError(f"Failed to fetch remotes for {repo.working_tree_dir}: {e}") from e


def _checkout_ref(repo: Repo, ref: str) -> None:
    """
    Checkout a branch, tag, or commit SHA into the working tree.

    Behavior:
    - Branch: track origin/<branch> if exists
    - Tag: checkout tag
    - SHA: detached HEAD
    """
    refs = {r.name: r for r in repo.refs}

    if str(ref or "").strip().upper() == "HEAD":
        try:
            repo.git.checkout("--force", "HEAD")
            return
        except GitCommandError as e:
            raise GitOpsError(f"Failed to checkout HEAD for {repo.working_tree_dir}: {e}") from e

    # Case 1: origin/<ref> exists → treat as branch
    origin_ref = f"origin/{ref}"
    if origin_ref in refs:
        try:
            repo.git.checkout("-B", ref, origin_ref)
            return
        except GitCommandError as e:
            raise GitOpsError(f"Failed to checkout branch '{ref}' from {origin_ref}: {e}") from e

    # Case 2: local branch exists
    if ref in refs:
        try:
            repo.git.checkout("--force", ref)
            return
        except GitCommandError as e:
            raise GitOpsError(f"Failed to checkout local branch '{ref}': {e}") from e

    # Case 3: tag exists
    try:
        if repo.git.tag("--list", ref):
            repo.git.checkout(ref)
            return
    except GitCommandError:
        pass

    # Case 4: assume SHA or resolvable ref → detached HEAD
    try:
        repo.git.checkout("--detach", ref)
        return
    except GitCommandError as e:
        raise GitOpsError(f"Failed to checkout ref '{ref}': {e}") from e
def ensure_checkout(repo_url: str, ref: str, workdir: str, repo_subdir: str = "repos") -> Tuple[str, str]:
    """
    Ensures an isolated checkout exists under:
      <workdir>/<repo_subdir>/<repo-identity>/<ref-identity>

    This keeps different projects and refs from reusing the same working tree.
    Returns: (repo_path, commit_sha)
    """
    base = Path(workdir).expanduser().resolve()
    repo_path = _repo_checkout_path(base, repo_url, ref, repo_subdir)

    repo = _ensure_repo(repo_url, repo_path)
    _fetch_all(repo)
    _checkout_ref(repo, ref)

    # Best-effort fast-forward pull if it's a branch with upstream
    try:
        # If HEAD is not detached and has upstream, pull ff-only
        if not repo.head.is_detached:
            repo.remotes.origin.pull(ff_only=True)
    except Exception:
        # Not fatal, ref might be SHA/tag or no upstream configured
        pass

    sha = repo.head.commit.hexsha
    return str(repo_path), sha
