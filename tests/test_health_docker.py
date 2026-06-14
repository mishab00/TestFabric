from __future__ import annotations

import unittest
from unittest.mock import patch

from testfabric.health.probes_docker import DockerHealthProbeSuite


class _FakeClient:
    def ping(self) -> None:
        return None

    def info(self) -> dict[str, str]:
        return {"ServerVersion": "27.0.0", "Driver": "overlay2"}


class _ClientContext:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    def __enter__(self) -> _FakeClient:
        return self.client

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class DockerHealthProbeTests(unittest.TestCase):
    def test_probe_checks_remote_endpoints_and_dedupes_duplicates(self) -> None:
        suite = DockerHealthProbeSuite()
        endpoints = [
            {"mode": "ssh", "base_url": "ssh://builder-1"},
            {"mode": "ssh", "base_url": "ssh://builder-1"},
            {"mode": "https", "base_url": "https://builder-2:2376"},
        ]

        with patch(
            "testfabric.health.probes_docker.docker_client",
            side_effect=lambda endpoint=None: _ClientContext(_FakeClient()),
        ) as docker_client:
            checks = suite.probe(required=False, endpoints=endpoints)

        self.assertEqual(docker_client.call_count, 2)
        self.assertEqual([check.name for check in checks], ["docker:ssh://builder-1", "docker:https://builder-2:2376"])
        self.assertTrue(all(check.ok for check in checks))
        self.assertTrue(all(check.details.get("endpoint") for check in checks))

    def test_probe_includes_local_docker_when_required(self) -> None:
        suite = DockerHealthProbeSuite()

        with patch(
            "testfabric.health.probes_docker.docker_client",
            side_effect=lambda endpoint=None: _ClientContext(_FakeClient()),
        ) as docker_client:
            checks = suite.probe(required=True, endpoints=[])

        self.assertEqual(docker_client.call_count, 1)
        self.assertEqual([check.name for check in checks], ["docker"])
        self.assertTrue(checks[0].ok)


if __name__ == "__main__":
    unittest.main()
