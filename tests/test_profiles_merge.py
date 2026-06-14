from __future__ import annotations

import unittest

from testfabric.inputs.profiles import apply_profile_overlay


class ProfileOverlayTests(unittest.TestCase):
    def test_profile_overlay_merges_arbitrary_spec_sections(self) -> None:
        raw = {
            "run": {
                "name": "sample",
                "repo_url": "git@example.com:org/repo.git",
                "ref": "main",
                "workdir": "./work",
                "artifacts_dir": "./artifacts",
            },
            "pipeline": {
                "stages": [{"suite": "smoke"}],
            },
            "suites": {
                "smoke": {
                    "kind": "command",
                    "steps": [{"name": "echo", "cmd": ["bash", "-lc", "echo ok"]}],
                }
            },
            "profiles": {
                "remote": {
                    "inputs": {"env_name": "remote"},
                    "run": {"ref": "profile-ref"},
                    "pipeline": {"default_executor": "docker"},
                }
            },
        }

        merged, profile = apply_profile_overlay(raw, profile_name="remote")

        self.assertEqual(merged["run"]["ref"], "profile-ref")
        self.assertEqual(merged["pipeline"]["default_executor"], "docker")
        self.assertIn("profiles", merged)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.inputs["env_name"], "remote")


if __name__ == "__main__":
    unittest.main()
