from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from testfabric.bootstrap.remote_docker import (
    RemoteDockerBootstrapConfig,
    RemoteDockerBootstrapResult,
    bootstrap_remote_docker,
    default_bootstrap_dir,
    render_yaml_snippet,
)


class RemoteDockerBootstrapTests(unittest.TestCase):
    def test_default_bootstrap_dir_is_stable(self) -> None:
        path = default_bootstrap_dir("1.1.1.1", user="root", port=22)
        self.assertIn(".testfabric/bootstrap/remote-docker", str(path))
        self.assertTrue(str(path).endswith("1-1-1-1-22-root"))

    def test_render_yaml_snippet_uses_generated_key_paths(self) -> None:
        result = RemoteDockerBootstrapResult(
            host="1.1.1.1",
            user="root",
            port=22,
            bootstrap_dir="/tmp/bootstrap",
            private_key_path="/tmp/bootstrap/id_ed25519",
            public_key_path="/tmp/bootstrap/id_ed25519.pub",
            known_hosts_path="/tmp/bootstrap/known_hosts",
            docker_endpoint={"mode": "ssh", "base_url": "ssh://root@1.1.1.1:22"},
        )
        text = render_yaml_snippet(result)
        self.assertIn("remote_docker", text)
        self.assertIn("/tmp/bootstrap/id_ed25519", text)
        self.assertIn("/tmp/bootstrap/known_hosts", text)

    def test_bootstrap_remote_docker_returns_endpoint_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap_dir = root / "bootstrap"
            with (
                patch("testfabric.bootstrap.remote_docker._ensure_keypair") as ensure_keypair,
                patch("testfabric.bootstrap.remote_docker._bootstrap_authorized_keys") as auth_keys,
                patch("testfabric.bootstrap.remote_docker._capture_known_hosts") as capture_known_hosts,
                patch("testfabric.bootstrap.remote_docker._verify_remote_docker") as verify_docker,
            ):
                (bootstrap_dir / "id_ed25519.pub").parent.mkdir(parents=True, exist_ok=True)
                (bootstrap_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAATEST testfabric\n", encoding="utf-8")
                ensure_keypair.return_value = (bootstrap_dir / "id_ed25519", bootstrap_dir / "id_ed25519.pub")
                auth_keys.return_value = None
                capture_known_hosts.return_value = bootstrap_dir / "known_hosts"
                verify_docker.return_value = "26.1.0"

                result = bootstrap_remote_docker(
                    RemoteDockerBootstrapConfig(
                        host="1.1.1.1",
                        user="root",
                        port=22,
                        password="XXXX",
                        bootstrap_dir=bootstrap_dir,
                        verify_docker=True,
                    )
                )

        self.assertEqual(result.private_key_path, str(root / "bootstrap" / "id_ed25519"))
        self.assertEqual(result.known_hosts_path, str(root / "bootstrap" / "known_hosts"))
        self.assertEqual(result.docker_endpoint["server_version"], "26.1.0")
        self.assertEqual(result.docker_endpoint["base_url"], "ssh://root@1.1.1.1:22")


if __name__ == "__main__":
    unittest.main()
