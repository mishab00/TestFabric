from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from git import Repo

from testfabric.workspace.gitops import ensure_checkout


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class WorkspaceIsolationTests(unittest.TestCase):
    def test_ensure_checkout_accepts_default_head_ref_for_local_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "source"
            repo = Repo.init(repo_dir)

            sha = _commit_file(repo, repo_dir, "marker.txt", "head-default\n", "init")

            workdir = root / "work"
            checkout_dir, got_sha = ensure_checkout(str(repo_dir), "HEAD", str(workdir))

            self.assertEqual((Path(checkout_dir) / "marker.txt").read_text(encoding="utf-8"), "head-default\n")
            self.assertEqual(got_sha, sha)

    def test_ensure_checkout_isolates_different_repos(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_a_dir = root / "source-a"
            repo_b_dir = root / "source-b"
            repo_a = Repo.init(repo_a_dir)
            repo_b = Repo.init(repo_b_dir)

            sha_a = _commit_file(repo_a, repo_a_dir, "marker.txt", "repo-a\n", "init a")
            sha_b = _commit_file(repo_b, repo_b_dir, "marker.txt", "repo-b\n", "init b")
            ref_a = repo_a.active_branch.name
            ref_b = repo_b.active_branch.name

            workdir = root / "work"
            checkout_a, got_sha_a = ensure_checkout(str(repo_a_dir), ref_a, str(workdir))
            checkout_b, got_sha_b = ensure_checkout(str(repo_b_dir), ref_b, str(workdir))

            self.assertNotEqual(checkout_a, checkout_b)
            self.assertEqual((Path(checkout_a) / "marker.txt").read_text(encoding="utf-8"), "repo-a\n")
            self.assertEqual((Path(checkout_b) / "marker.txt").read_text(encoding="utf-8"), "repo-b\n")
            self.assertEqual(got_sha_a, sha_a)
            self.assertEqual(got_sha_b, sha_b)

    def test_ensure_checkout_isolates_different_refs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "source"
            repo = Repo.init(repo_dir)

            main_sha = _commit_file(repo, repo_dir, "marker.txt", "main\n", "init main")
            main_ref = repo.active_branch.name

            repo.git.checkout("-b", "feature")
            feature_sha = _commit_file(repo, repo_dir, "marker.txt", "feature\n", "feature update")
            repo.git.checkout(main_ref)

            workdir = root / "work"
            checkout_main, got_main_sha = ensure_checkout(str(repo_dir), main_ref, str(workdir))
            checkout_feature, got_feature_sha = ensure_checkout(str(repo_dir), "feature", str(workdir))

            self.assertNotEqual(checkout_main, checkout_feature)
            self.assertEqual((Path(checkout_main) / "marker.txt").read_text(encoding="utf-8"), "main\n")
            self.assertEqual((Path(checkout_feature) / "marker.txt").read_text(encoding="utf-8"), "feature\n")
            self.assertEqual(got_main_sha, main_sha)
            self.assertEqual(got_feature_sha, feature_sha)


if __name__ == "__main__":
    unittest.main()
