from __future__ import annotations

from pathlib import Path

from testfabric.core.context import StageContext
from testfabric.core.events import Event
from testfabric.execution.executors.base import BuildRequest
from testfabric.execution.executors.docker_template import build_request_for_runtime
from testfabric.execution.executors.registry import ExecutorRegistry


class StageBuilder:
    def __init__(self, executors: ExecutorRegistry) -> None:
        self.executors = executors
        self._built_images: set[tuple[str, str, str]] = set()

    def build_if_needed(self, ctx: StageContext) -> None:
        if (ctx.executor or "").strip().lower() != "docker":
            return

        if ctx.docker is None:
            raise ValueError("docker runtime config is required when using docker executor")

        requested_mode = (ctx.docker.mode or "auto").strip().lower()
        if not ctx.build and requested_mode not in {"auto", "template"}:
            return

        req = build_request_for_runtime(ctx.repo_path, ctx.docker)
        if req is None:
            return

        endpoint_key = str(ctx.docker_endpoint.get("base_url") or "local")
        cache_key = (str(req.image), str(req.dockerfile), str(req.repo_path), endpoint_key)
        if cache_key in self._built_images:
            return

        ctx.events.emit(
            Event(
                "docker",
                "build",
                "start",
                {"image": req.image, "dockerfile": req.dockerfile, "mode": ctx.docker.mode},
            )
        )

        ex = self.executors.get("docker")

        log_dir = Path(ctx.stage_logs_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "docker-build.log"
        log_path.write_text("", encoding="utf-8")

        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"=== BUILD START image={req.image} dockerfile={req.dockerfile} no_cache={req.no_cache} ===\n")
            f.flush()

            def on_line(t: str) -> None:
                f.write(t)
                f.flush()
                # global stream file under run_dir
                ctx.events.stream("logs/docker-build.stream.log", t, echo_prefix="[docker] ")

            ex.build(ctx, req, on_line=on_line)
            self._built_images.add(cache_key)

            f.write("=== BUILD END ===\n")
            f.flush()

        ctx.events.emit(Event("docker", "build", "success", {"image": req.image, "log": str(log_path)}))
