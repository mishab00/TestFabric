#testfabric/executors/dockerops.py
import contextlib
import io
from dataclasses import dataclass
import os
import shlex
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import docker
from docker.client import DockerClient
from docker.errors import APIError, DockerException, ImageNotFound
from docker.tls import TLSConfig


# ---------- Exceptions ----------

class DockerOpsError(RuntimeError):
    pass


class DockerBuildFailed(DockerOpsError):
    def __init__(self, message: str, logs: List[str]):
        super().__init__(message)
        self.logs = logs


class DockerRunFailed(DockerOpsError):
    def __init__(self, message: str, container_id: Optional[str] = None):
        super().__init__(message)
        self.container_id = container_id


# ---------- Client ----------

_SSH_CONFIG_LOCK = threading.RLock()

@dataclass(frozen=True)
class PreparedEndpoint:
    client_kwargs: dict[str, object]
    temp_home: str | None = None
    cleanup_dir: str | None = None
    path_prefix: str | None = None


@contextlib.contextmanager
def _temporary_home(path: str | None) -> Iterator[None]:
    if not path:
        yield
        return
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = path
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


@contextlib.contextmanager
def _temporary_env(values: dict[str, str] | None) -> Iterator[None]:
    if not values:
        yield
        return
    old_values: dict[str, str | None] = {}
    for key, value in values.items():
        old_values[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_ssh_config(*, endpoint: Dict[str, str], temp_home: str) -> str:
    parsed = urlparse(str(endpoint.get("base_url") or "").strip())
    host = str(endpoint.get("host") or parsed.hostname or "").strip()
    if not host:
        raise DockerOpsError("Docker SSH endpoint is missing host")
    user = str(endpoint.get("user") or parsed.username or "root").strip() or "root"
    port = str(endpoint.get("port") or parsed.port or "22").strip() or "22"
    key_path = str(endpoint.get("key_path") or "").strip()
    via = str(endpoint.get("via") or "").strip()
    known_hosts = str(endpoint.get("known_hosts") or "").strip()

    ssh_dir = Path(temp_home) / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)

    known_hosts_dest = ssh_dir / "known_hosts"
    source_known_hosts = Path(known_hosts).expanduser() if known_hosts else Path(os.path.expanduser("~/.ssh/known_hosts"))
    if source_known_hosts.exists():
        shutil.copyfile(source_known_hosts, known_hosts_dest)
        os.chmod(known_hosts_dest, 0o600)

    lines = [
        f"Host {host}",
        f"  HostName {host}",
        f"  User {user}",
        f"  Port {port}",
    ]
    if key_path:
        local_key = ssh_dir / "id_ed25519"
        shutil.copyfile(Path(key_path).expanduser(), local_key)
        os.chmod(local_key, 0o600)
        lines.append(f"  IdentityFile {Path(key_path).expanduser()}")
        lines.append("  IdentitiesOnly yes")
        lines.append("  IdentityAgent none")
        lines.append("  PreferredAuthentications publickey")
    if known_hosts_dest.exists():
        lines.append(f"  UserKnownHostsFile {known_hosts_dest}")
    if via:
        lines.append(f"  ProxyCommand ssh -W %h:%p {via}")

    config_path = ssh_dir / "config"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(config_path, 0o600)
    return host


def _write_ssh_wrapper(*, endpoint: Dict[str, str], temp_home: str) -> str:
    parsed = urlparse(str(endpoint.get("base_url") or "").strip())
    host = str(endpoint.get("host") or parsed.hostname or "").strip()
    if not host:
        raise DockerOpsError("Docker SSH endpoint is missing host")
    user = str(endpoint.get("user") or parsed.username or "root").strip() or "root"
    port = str(endpoint.get("port") or parsed.port or "22").strip() or "22"
    key_path = str(endpoint.get("key_path") or "").strip()
    known_hosts = str(endpoint.get("known_hosts") or "").strip()
    via = str(endpoint.get("via") or "").strip()

    bin_dir = Path(temp_home) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise DockerOpsError("ssh command not found")

    config_path = Path(temp_home) / ".ssh" / "config"
    opts = [
        f"-F {shlex.quote(str(config_path))}",
        "-o IdentitiesOnly=yes",
        "-o IdentityAgent=none",
        "-o PreferredAuthentications=publickey",
    ]
    if key_path:
        opts.append(f"-i {shlex.quote(str(Path(key_path).expanduser()))}")
    if known_hosts:
        opts.append(f"-o UserKnownHostsFile={shlex.quote(str(Path(known_hosts).expanduser()))}")
    else:
        opts.append("-o UserKnownHostsFile=$HOME/.ssh/known_hosts")
    opts.append("-o StrictHostKeyChecking=yes")

    wrapper = bin_dir / "ssh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"exec {shlex.quote(ssh_path)} {' '.join(opts)} \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    return str(bin_dir)


def _infer_endpoint_mode(endpoint: Dict[str, str] | None) -> str:
    endpoint = dict(endpoint or {})
    mode = str(endpoint.get("mode") or "").strip().lower()
    if mode in {"ssh", "tcp", "https"}:
        return mode
    base_url = str(endpoint.get("base_url") or "").strip().lower()
    parsed_scheme = urlparse(base_url).scheme
    if parsed_scheme in {"ssh", "tcp", "https"}:
        return parsed_scheme
    return "local"


def _build_tls_config(tls_value: object) -> object:
    if tls_value is None or tls_value is False:
        return False
    if tls_value is True:
        return True
    if isinstance(tls_value, TLSConfig):
        return tls_value
    if isinstance(tls_value, dict):
        client_cert = tls_value.get("client_cert")
        client_key = tls_value.get("client_key")
        if client_cert and client_key:
            client_cert = (str(client_cert), str(client_key))
        ca_cert = tls_value.get("ca_cert")
        verify = tls_value.get("verify")
        return TLSConfig(
            client_cert=client_cert,
            ca_cert=str(ca_cert) if ca_cert else None,
            verify=bool(verify) if verify is not None else None,
        )
    return bool(tls_value)


def prepare_endpoint(endpoint: Dict[str, str] | None = None) -> PreparedEndpoint:
    endpoint = dict(endpoint or {})
    mode = _infer_endpoint_mode(endpoint)
    if mode in {"", "local"}:
        return PreparedEndpoint(client_kwargs={"from_env": True})

    base_url = str(endpoint.get("base_url") or "").strip()
    if not base_url:
        raise DockerOpsError("Docker endpoint is missing base_url")

    if mode == "ssh":
        needs_config = any(str(endpoint.get(key) or "").strip() for key in ("key_path", "known_hosts", "via"))
        if needs_config:
            temp_home = tempfile.mkdtemp(prefix="testfabric-docker-ssh-")
            host = _write_ssh_config(endpoint=endpoint, temp_home=temp_home)
            path_prefix = _write_ssh_wrapper(endpoint=endpoint, temp_home=temp_home)
            return PreparedEndpoint(
                client_kwargs={"base_url": f"ssh://{host}", "tls": False, "use_ssh_client": True, "version": "auto"},
                temp_home=temp_home,
                cleanup_dir=temp_home,
                path_prefix=path_prefix,
            )
        return PreparedEndpoint(
            client_kwargs={"base_url": base_url, "tls": False, "use_ssh_client": True, "version": "auto"}
        )

    tls_payload = endpoint.get("tls")
    if mode == "https" and tls_payload is None:
        tls_payload = True
    tls = _build_tls_config(tls_payload)
    return PreparedEndpoint(
        client_kwargs={"base_url": base_url, "tls": tls or False, "version": "auto"}
    )


@contextlib.contextmanager
def docker_client(endpoint: Dict[str, str] | None = None) -> Iterator[DockerClient]:
    prepared = prepare_endpoint(endpoint)
    client: DockerClient | None = None
    try:
        if prepared.temp_home:
            with _SSH_CONFIG_LOCK:
                with _temporary_home(prepared.temp_home):
                    with _temporary_env({"PATH": f"{prepared.path_prefix}:{os.environ.get('PATH', '')}"} if prepared.path_prefix else None):
                        client = _create_client(prepared)
                        yield client
        else:
            client = _create_client(prepared)
            yield client
    except DockerException as e:
        raise DockerOpsError(f"Failed to connect to Docker daemon: {e}") from e
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def get_client(endpoint: Dict[str, str] | None = None) -> DockerClient:
    prepared = prepare_endpoint(endpoint)
    return _create_client(prepared)


def _create_client(prepared: PreparedEndpoint) -> DockerClient:
    try:
        if prepared.client_kwargs.get("from_env"):
            client = docker.from_env()
        else:
            kwargs = dict(prepared.client_kwargs)
            client = docker.DockerClient(**kwargs)
    except DockerException as e:
        if prepared.cleanup_dir:
            shutil.rmtree(prepared.cleanup_dir, ignore_errors=True)
        raise DockerOpsError(f"Failed to connect to Docker daemon: {e}") from e

    original_close = client.close

    def _close_with_cleanup() -> None:
        try:
            original_close()
        finally:
            if prepared.cleanup_dir:
                shutil.rmtree(prepared.cleanup_dir, ignore_errors=True)

    client.close = _close_with_cleanup  # type: ignore[method-assign]
    return client


def _is_remote_endpoint(endpoint: Dict[str, str] | None) -> bool:
    endpoint = dict(endpoint or {})
    mode = str(endpoint.get("mode") or "local").strip().lower()
    if mode and mode != "local":
        return True
    base_url = str(endpoint.get("base_url") or "").strip().lower()
    return base_url.startswith("ssh://") or base_url.startswith("tcp://") or base_url.startswith("https://")


# ---------- Build ----------

@dataclass(frozen=True)
class BuildLog:
    lines: List[str]


def build_image(
    repo_path: str,
    dockerfile: str,
    image: str,
    build_args: Dict[str, str],
    no_cache: bool = False,
    endpoint: Dict[str, str] | None = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> BuildLog:
    """
    Build an image with live build output.

    - repo_path is the build context.
    - dockerfile is path relative to repo_path.
    - on_line(line) is called for each human-readable log line (with newline).
    """
    with docker_client(endpoint) as client:
        rp = Path(repo_path).resolve()
        df = (rp / dockerfile).resolve()

        logs_out: List[str] = []

        def emit(line: str) -> None:
            logs_out.append(line)
            if on_line:
                on_line(line)

        if not df.exists():
            msg = f"Dockerfile not found: {df}"
            emit(f"ERROR: {msg}\n")
            raise DockerBuildFailed(msg, logs_out)

        # Docker low-level API expects dockerfile relative to build context
        dockerfile_rel = str(df.relative_to(rp))

        try:
            stream = client.api.build(
                path=str(rp),
                dockerfile=dockerfile_rel,
                tag=image,
                buildargs=build_args or None,
                rm=True,
                pull=False,
                nocache=no_cache,
                decode=True,  # dict events
            )

            for entry in stream:
                if not isinstance(entry, dict):
                    continue

                # Primary human build output
                if entry.get("stream"):
                    emit(str(entry["stream"]))

                # Some daemons use "status"/"progress"
                if entry.get("status"):
                    status = str(entry.get("status", "")).strip()
                    progress = str(entry.get("progress", "")).strip()
                    if status:
                        emit(status + ((" " + progress) if progress else "") + "\n")

                # Fatal error
                if entry.get("error"):
                    err = str(entry["error"]).strip()
                    emit(f"ERROR: {err}\n")
                    raise DockerBuildFailed(f"Docker build failed for image '{image}': {err}", logs_out)

            return BuildLog(lines=logs_out)

        except DockerBuildFailed:
            raise
        except (APIError, DockerException) as e:
            emit(f"ERROR: {e}\n")
            raise DockerBuildFailed(f"Docker build failed for image '{image}': {e}", logs_out) from e


# ---------- Run ----------

@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    container_id: Optional[str]


def _decode(b: Optional[bytes]) -> str:
    if not b:
        return ""
    return b.decode("utf-8", errors="replace")


def _norm_abs_mount(p: str | None, *, default: str) -> str:
    s = (p or "").strip() or default
    if not s.startswith("/"):
        s = "/" + s
    # avoid trailing slash edge cases like "/app/"
    return s.rstrip("/") or "/"


def docker_run(
    image: str,
    repo_path: str | None,
    repo_mount: str | None,
    cmd_inside: List[str],
    env: Dict[str, str],
    artifacts_dir: Optional[str],
    workdir: Optional[str],
    network: Optional[str],
    shm_size: Optional[str],
    keep_container: bool,
    endpoint: Dict[str, str] | None = None,
    timeout_seconds: Optional[int] = None,
    on_stdout: Optional[Callable[[str], None]] = None,
    on_stderr: Optional[Callable[[str], None]] = None,
    entrypoint: str | None = None,
) -> RunResult:
    """
    Run a container with:
      - repo optionally mounted to repo_mount (default /work)
      - artifacts optionally mounted to /artifacts
    """
    remote_endpoint = _is_remote_endpoint(endpoint)

    volumes: Dict[str, Dict[str, str]] = {}

    # ✅ compute repo mount once
    repo_bind = _norm_abs_mount(repo_mount, default="/work")

    # ✅ Optional repo mount
    if repo_path and not remote_endpoint:
        rp = Path(repo_path).resolve()
        if not rp.exists():
            raise DockerRunFailed(f"Repo path does not exist: {rp}")
        volumes[str(rp)] = {"bind": repo_bind, "mode": "rw"}

    # ✅ Optional artifacts mount
    if artifacts_dir and not remote_endpoint:
        ad = Path(artifacts_dir).resolve()
        ad.mkdir(parents=True, exist_ok=True)
        volumes[str(ad)] = {"bind": "/artifacts", "mode": "rw"}

    # ✅ Choose a sensible working dir
    # - If caller passed workdir -> use it
    # - Else if repo mounted -> repo_bind
    # - Else -> /
    effective_workdir = workdir or (repo_bind if repo_path else "/")

    container = None
    timed_out = False
    timeout_timer: threading.Timer | None = None
    with docker_client(endpoint) as client:
        try:
            create_kwargs = dict(
                image=image,
                command=cmd_inside,
                environment=env or None,
                working_dir=effective_workdir,
                volumes=volumes or None,
                network=network if network else None,
                shm_size=shm_size if shm_size else None,
                detach=True,
                tty=False,
                stdin_open=False,
            )

            # Allow overriding image ENTRYPOINT
            if entrypoint is not None:
                create_kwargs["entrypoint"] = entrypoint

            container = client.containers.create(**create_kwargs)
            if remote_endpoint:
                _prime_remote_container(
                    container=container,
                    repo_path=repo_path,
                    repo_bind=repo_bind if repo_path else None,
                    ensure_artifacts=bool(artifacts_dir),
                )

        except ImageNotFound as e:
            raise DockerRunFailed(f"Docker image not found: {image}") from e
        except APIError as e:
            raise DockerRunFailed(f"Failed to create container for image '{image}': {e}") from e

        try:
            container.start()

            if timeout_seconds is not None:
                def enforce_timeout() -> None:
                    nonlocal timed_out
                    timed_out = True
                    try:
                        container.stop(timeout=3)
                    except Exception:
                        try:
                            container.kill()
                        except Exception:
                            pass

                timeout_timer = threading.Timer(float(timeout_seconds), enforce_timeout)
                timeout_timer.daemon = True
                timeout_timer.start()

            out_chunks: List[str] = []
            err_chunks: List[str] = []

            wait_res = container.wait()
            exit_code = int(wait_res.get("StatusCode", 1))

            if remote_endpoint:
                stdout_bytes = container.logs(stream=False, stdout=True, stderr=False, follow=False)
                stderr_bytes = container.logs(stream=False, stdout=False, stderr=True, follow=False)
                stdout_text = _decode(stdout_bytes if isinstance(stdout_bytes, bytes) else b"")
                stderr_text = _decode(stderr_bytes if isinstance(stderr_bytes, bytes) else b"")
                if stdout_text:
                    out_chunks.append(stdout_text)
                    if on_stdout:
                        keep = on_stdout(stdout_text)
                        if keep is False:
                            timed_out = True
                if stderr_text:
                    err_chunks.append(stderr_text)
                    if on_stderr:
                        keep = on_stderr(stderr_text)
                        if keep is False:
                            timed_out = True
            else:
                stream = container.attach(stream=True, logs=True, demux=True)
                for chunk in stream:
                    if not chunk:
                        continue
                    out_b, err_b = chunk

                    if out_b:
                        t = _decode(out_b)
                        out_chunks.append(t)
                        if on_stdout:
                            keep = on_stdout(t)
                            if keep is False:
                                try:
                                    container.stop(timeout=3)
                                except Exception:
                                    try:
                                        container.kill()
                                    except Exception:
                                        pass
                                timed_out = True
                                break

                    if err_b:
                        t = _decode(err_b)
                        err_chunks.append(t)
                        if on_stderr:
                            keep = on_stderr(t)
                            if keep is False:
                                try:
                                    container.stop(timeout=3)
                                except Exception:
                                    try:
                                        container.kill()
                                    except Exception:
                                        pass
                                timed_out = True
                                break

            if timeout_timer is not None:
                timeout_timer.cancel()

            if timed_out:
                timeout_msg = f"\nTESTFABRIC TIMEOUT: container exceeded {timeout_seconds}s\n"
                err_chunks.append(timeout_msg)
                if on_stderr:
                    on_stderr(timeout_msg)
                exit_code = 124

            return RunResult(
                exit_code=exit_code,
                stdout="".join(out_chunks),
                stderr="".join(err_chunks),
                container_id=container.id,
            )

        except APIError as e:
            raise DockerRunFailed(f"Container execution failed: {e}", container_id=getattr(container, "id", None)) from e

        finally:
            if container is not None and remote_endpoint and artifacts_dir:
                try:
                    _extract_remote_artifacts(container, Path(artifacts_dir).resolve())
                except Exception:
                    pass
            if timeout_timer is not None:
                timeout_timer.cancel()
            if container is not None and not keep_container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def _prime_remote_container(*, container, repo_path: str | None, repo_bind: str | None, ensure_artifacts: bool) -> None:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        if repo_path and repo_bind:
            root = Path(repo_path).resolve()
            repo_prefix = Path(str(repo_bind).lstrip("/"))
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root)
                if not str(rel):
                    continue
                if ".git" in rel.parts:
                    continue
                arcname = (repo_prefix / rel).as_posix()
                tar.add(path, arcname=arcname, recursive=False)
        if ensure_artifacts:
            info = tarfile.TarInfo(name="artifacts")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
    archive.seek(0)
    if archive.getbuffer().nbytes:
        container.put_archive("/", archive.read())


def _extract_remote_artifacts(container, local_artifacts_dir: Path) -> None:
    local_artifacts_dir.mkdir(parents=True, exist_ok=True)
    try:
        bits, _ = container.get_archive("/artifacts")
    except APIError:
        return
    payload = io.BytesIO()
    for chunk in bits:
        payload.write(chunk)
    payload.seek(0)
    if payload.getbuffer().nbytes == 0:
        return
    with tarfile.open(fileobj=payload, mode="r:*") as tar:
        tar.extractall(path=local_artifacts_dir)
