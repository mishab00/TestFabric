from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from testfabric.bootstrap.remote_docker import (
    RemoteDockerBootstrapConfig,
    RemoteDockerBootstrapError,
    bootstrap_remote_docker,
    default_bootstrap_dir,
    render_yaml_snippet,
)


def _read_password_from_stdin() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Remote SSH password: ")
    return sys.stdin.readline().rstrip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap remote Docker control-plane access over SSH password auth.")
    parser.add_argument("host", help="Remote host or IP address")
    parser.add_argument("--user", default="root", help="SSH user, default: root")
    parser.add_argument("--port", type=int, default=22, help="SSH port, default: 22")
    parser.add_argument(
        "--bootstrap-dir",
        default=None,
        help="Directory for generated keypair and known_hosts (default: ~/.testfabric/bootstrap/remote-docker/...)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password for the one-time bootstrap. Prefer --password-stdin for interactive use.",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of the command line.",
    )
    parser.add_argument("--no-verify-docker", action="store_true", help="Skip the post-bootstrap docker version check.")
    ns = parser.parse_args(argv)

    password = ns.password
    if ns.password_stdin or password is None:
        password = _read_password_from_stdin()

    bootstrap_dir = Path(ns.bootstrap_dir).expanduser() if ns.bootstrap_dir else default_bootstrap_dir(ns.host, user=ns.user, port=ns.port)
    try:
        result = bootstrap_remote_docker(
            RemoteDockerBootstrapConfig(
                host=ns.host,
                user=ns.user,
                port=ns.port,
                password=password,
                bootstrap_dir=bootstrap_dir,
                verify_docker=not bool(ns.no_verify_docker),
            )
        )
    except RemoteDockerBootstrapError as e:
        print(str(e), file=sys.stderr)
        return 1

    print("Bootstrap complete")
    print(f"bootstrap_dir: {result.bootstrap_dir}")
    print(f"private_key:   {result.private_key_path}")
    print(f"known_hosts:   {result.known_hosts_path}")
    if result.docker_endpoint.get("server_version"):
        print(f"server_version: {result.docker_endpoint['server_version']}")
    print("")
    print("Use this snippet in your spec:")
    print(render_yaml_snippet(result).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
