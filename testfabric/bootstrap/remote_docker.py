from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RemoteDockerBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteDockerBootstrapConfig:
    host: str
    user: str = "root"
    port: int = 22
    password: str = ""
    bootstrap_dir: Path | None = None
    verify_docker: bool = True


@dataclass(frozen=True)
class RemoteDockerBootstrapResult:
    host: str
    user: str
    port: int
    bootstrap_dir: str
    private_key_path: str
    public_key_path: str
    known_hosts_path: str
    docker_endpoint: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "bootstrap_dir": self.bootstrap_dir,
            "private_key_path": self.private_key_path,
            "public_key_path": self.public_key_path,
            "known_hosts_path": self.known_hosts_path,
            "docker_endpoint": dict(self.docker_endpoint or {}),
        }


def _slug(value: str) -> str:
    out: list[str] = []
    last_dash = False
    for ch in (value or "").strip().lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "remote"


def default_bootstrap_dir(host: str, *, user: str = "root", port: int = 22) -> Path:
    return Path.home() / ".testfabric" / "bootstrap" / "remote-docker" / f"{_slug(host)}-{port}-{_slug(user)}"


def _require_command(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RemoteDockerBootstrapError(f"Required command not found: {name}")
    return found


def _run_checked(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RemoteDockerBootstrapError(
            f"Command failed ({proc.returncode}): {' '.join(shlex.quote(part) for part in args)}\n{proc.stderr.strip()}"
        )
    return proc


def _ensure_keypair(bootstrap_dir: Path, *, comment: str) -> tuple[Path, Path]:
    ssh_keygen = _require_command("ssh-keygen")
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    private_key = bootstrap_dir / "id_ed25519"
    public_key = bootstrap_dir / "id_ed25519.pub"
    if not private_key.exists():
        _run_checked([ssh_keygen, "-q", "-t", "ed25519", "-f", str(private_key), "-N", "", "-C", comment])
    if not public_key.exists():
        raise RemoteDockerBootstrapError(f"Missing public key after generation: {public_key}")
    os.chmod(private_key, 0o600)
    os.chmod(public_key, 0o644)
    return private_key, public_key


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _capture_known_hosts(*, host: str, port: int, bootstrap_dir: Path) -> Path:
    ssh_keyscan = _require_command("ssh-keyscan")
    known_hosts = bootstrap_dir / "known_hosts"
    proc = subprocess.run(
        [ssh_keyscan, "-p", str(port), "-H", host],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RemoteDockerBootstrapError(
            f"Failed to capture known_hosts for {host}:{port}: {proc.stderr.strip() or proc.stdout.strip() or 'no output'}"
        )
    known_hosts.write_text(proc.stdout, encoding="utf-8")
    os.chmod(known_hosts, 0o600)
    return known_hosts


def _bootstrap_authorized_keys(*, host: str, user: str, port: int, password: str, public_key: str) -> None:
    sshpass = _require_command("sshpass")
    ssh = _require_command("ssh")
    remote_script = (
        "set -eu; "
        "mkdir -p ~/.ssh; "
        "chmod 700 ~/.ssh; "
        "touch ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys; "
        f"pubkey={shlex.quote(public_key)}; "
        'grep -qxF -- "$pubkey" ~/.ssh/authorized_keys || printf "%s\\n" "$pubkey" >> ~/.ssh/authorized_keys'
    )
    _run_checked(
        [
            sshpass,
            "-p",
            password,
            ssh,
            "-p",
            str(port),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UserKnownHostsFile=/dev/null",
            f"{user}@{host}",
            "sh",
            "-lc",
            remote_script,
        ]
    )


def _verify_remote_docker(*, host: str, user: str, port: int, private_key: Path, known_hosts: Path) -> str:
    ssh = _require_command("ssh")
    proc = _run_checked(
        [
            ssh,
            "-i",
            str(private_key),
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            f"{user}@{host}",
            "docker",
            "version",
            "--format",
            "{{.Server.Version}}",
        ]
    )
    return proc.stdout.strip()


def bootstrap_remote_docker(config: RemoteDockerBootstrapConfig) -> RemoteDockerBootstrapResult:
    host = str(config.host or "").strip()
    user = str(config.user or "root").strip() or "root"
    if not host:
        raise RemoteDockerBootstrapError("host is required")
    if not str(config.password or "").strip():
        raise RemoteDockerBootstrapError("password is required for bootstrap")

    bootstrap_dir = config.bootstrap_dir or default_bootstrap_dir(host, user=user, port=int(config.port))
    bootstrap_dir = Path(bootstrap_dir).expanduser()
    private_key, public_key = _ensure_keypair(
        bootstrap_dir,
        comment=f"testfabric-bootstrap:{user}@{host}:{int(config.port)}",
    )
    _bootstrap_authorized_keys(
        host=host,
        user=user,
        port=int(config.port),
        password=str(config.password),
        public_key=_read_text(public_key),
    )
    known_hosts = _capture_known_hosts(host=host, port=int(config.port), bootstrap_dir=bootstrap_dir)
    server_version = ""
    if config.verify_docker:
        server_version = _verify_remote_docker(
            host=host,
            user=user,
            port=int(config.port),
            private_key=private_key,
            known_hosts=known_hosts,
        )
        if not server_version:
            raise RemoteDockerBootstrapError("Docker verification returned no server version")

    endpoint = {
        "mode": "ssh",
        "host": host,
        "user": user,
        "port": str(int(config.port)),
        "key_path": str(private_key),
        "known_hosts": str(known_hosts),
        "base_url": f"ssh://{user}@{host}:{int(config.port)}",
    }
    if server_version:
        endpoint["server_version"] = server_version

    return RemoteDockerBootstrapResult(
        host=host,
        user=user,
        port=int(config.port),
        bootstrap_dir=str(bootstrap_dir),
        private_key_path=str(private_key),
        public_key_path=str(public_key),
        known_hosts_path=str(known_hosts),
        docker_endpoint=endpoint,
    )


def render_yaml_snippet(result: RemoteDockerBootstrapResult) -> str:
    payload = {
        "credentials": {
            "remote_docker": {
                "user": result.user,
                "key_path": result.private_key_path,
                "known_hosts": result.known_hosts_path,
            }
        },
        "targets": {
            "builders": {
                result.host: {
                    "address": result.host,
                    "credential": "remote_docker",
                }
            }
        },
    }
    import yaml

    return yaml.safe_dump(payload, sort_keys=False)
