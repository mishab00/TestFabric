from __future__ import annotations

from dataclasses import replace
import hashlib
import textwrap
from pathlib import Path

from testfabric.core.context import DockerRuntime
from testfabric.execution.executors.base import BuildRequest


_DEFAULT_IMAGE = "testfabric-tests:ci"
_MANAGED_DIR = Path(".testfabric/docker")


def resolve_runtime(repo_path: str, runtime: DockerRuntime) -> DockerRuntime:
    repo_root = Path(repo_path).expanduser().resolve()
    requested_mode = (runtime.mode or "auto").strip().lower()
    effective_mode = requested_mode

    if requested_mode == "auto":
        dockerfile_path = (repo_root / str(runtime.dockerfile or "Dockerfile")).resolve()
        effective_mode = "custom" if dockerfile_path.exists() else "template"

    image = str(runtime.image or "").strip()
    if requested_mode in {"auto", "template"} and (not image or image == _DEFAULT_IMAGE):
        image = _managed_image_tag(repo_root, runtime)

    return replace(runtime, mode=effective_mode, image=image or _DEFAULT_IMAGE)


def build_request_for_runtime(repo_path: str, runtime: DockerRuntime) -> BuildRequest | None:
    resolved = resolve_runtime(repo_path, runtime)
    mode = resolved.mode

    if mode == "custom":
        return BuildRequest(
            repo_path=repo_path,
            dockerfile=str(resolved.dockerfile),
            image=str(resolved.image),
            build_args=dict(resolved.build_args or {}),
            no_cache=bool(resolved.no_cache),
        )

    dockerfile_rel = _ensure_managed_dockerfile(Path(repo_path).expanduser().resolve(), resolved)
    return BuildRequest(
        repo_path=repo_path,
        dockerfile=dockerfile_rel,
        image=str(resolved.image),
        build_args=dict(resolved.build_args or {}),
        no_cache=bool(resolved.no_cache),
    )


def _managed_image_tag(repo_root: Path, runtime: DockerRuntime) -> str:
    payload = "|".join(
        [
            str(repo_root),
            str(runtime.base_image),
            ",".join(runtime.python_requirements or []),
            ",".join(runtime.pip_packages or []),
            ",".join(runtime.system_packages or []),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"testfabric-managed:{digest}"


def _ensure_managed_dockerfile(repo_root: Path, runtime: DockerRuntime) -> str:
    managed_dir = repo_root / _MANAGED_DIR
    managed_dir.mkdir(parents=True, exist_ok=True)

    requirements = _resolve_requirements(repo_root, runtime)
    use_poetry = _should_use_poetry(repo_root, runtime, requirements)
    dockerfile_name = f"managed-{_config_hash(runtime, requirements, use_poetry)}.Dockerfile"
    dockerfile_path = managed_dir / dockerfile_name

    dockerfile_path.write_text(
        _render_managed_dockerfile(
            runtime=runtime,
            requirements=requirements,
            use_poetry=use_poetry,
        ),
        encoding="utf-8",
    )
    return str(dockerfile_path.relative_to(repo_root))


def _resolve_requirements(repo_root: Path, runtime: DockerRuntime) -> list[str]:
    explicit = [str(x).strip() for x in list(runtime.python_requirements or []) if str(x).strip()]
    if explicit:
        missing = [item for item in explicit if not (repo_root / item).exists()]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Managed docker requirements file(s) not found: {joined}")
        return explicit

    default_requirements = repo_root / "requirements.txt"
    if default_requirements.exists():
        return ["requirements.txt"]

    return []


def _should_use_poetry(repo_root: Path, runtime: DockerRuntime, requirements: list[str]) -> bool:
    if requirements:
        return False
    if runtime.pip_packages:
        return False
    return (repo_root / "pyproject.toml").exists()


def _config_hash(runtime: DockerRuntime, requirements: list[str], use_poetry: bool) -> str:
    payload = "|".join(
        [
            str(runtime.base_image),
            ",".join(runtime.system_packages or []),
            ",".join(requirements),
            ",".join(runtime.pip_packages or []),
            "poetry" if use_poetry else "no-poetry",
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _render_managed_dockerfile(
    *,
    runtime: DockerRuntime,
    requirements: list[str],
    use_poetry: bool,
) -> str:
    lines: list[str] = [
        f"FROM {runtime.base_image}",
        "ENV PIP_DISABLE_PIP_VERSION_CHECK=1",
        "ENV PYTHONDONTWRITEBYTECODE=1",
        "ENV PYTHONUNBUFFERED=1",
    ]

    packages = [str(x).strip() for x in list(runtime.system_packages or []) if str(x).strip()]
    if packages:
        pkg_list = " ".join(packages)
        lines.append(
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            f"{pkg_list} && rm -rf /var/lib/apt/lists/*"
        )

    needs_python_install = bool(requirements or runtime.pip_packages or use_poetry)
    if needs_python_install:
        lines.append("RUN python -m pip install --upgrade pip")

    if requirements:
        for idx, req in enumerate(requirements, start=1):
            lines.append(f"COPY {req} /tmp/testfabric/requirements-{idx}.txt")
            lines.append(f"RUN python -m pip install -r /tmp/testfabric/requirements-{idx}.txt")

    pip_packages = [str(x).strip() for x in list(runtime.pip_packages or []) if str(x).strip()]
    if pip_packages:
        lines.append(f"RUN python -m pip install {' '.join(pip_packages)}")

    if use_poetry:
        lines.append("RUN python -m pip install poetry")
        lines.append("COPY pyproject.toml /tmp/testfabric/pyproject.toml")
        lines.append("RUN poetry config virtualenvs.create false")
        lines.append(
            "RUN cd /tmp/testfabric && poetry install --no-interaction --no-root"
        )

    lines.append(f"WORKDIR {runtime.workdir}")
    return textwrap.dedent("\n".join(lines)).strip() + "\n"
