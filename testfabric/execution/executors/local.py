# testfabric/execution/executors/local.py
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from testfabric.core.context import StageContext
from testfabric.execution.executors.base import BuildRequest, ExecutableCommand, ExecResult


class LocalExecutorAdapter:
    """
    Runs commands on the controller (jumpbox).

    Notes:
      - workdir_repo=False => cmd.workdir is used as-is (absolute host path recommended)
      - workdir_repo=True  => cmd.workdir is relative to ctx.repo_path
      - artifacts_dir is used as-is (host path)
    """

    name = "local"

    def build(
        self,
        ctx: StageContext,
        req: BuildRequest,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        raise NotImplementedError("LocalExecutorAdapter.build is not supported (docker-only)")

    def run(
        self,
        ctx: StageContext,
        cmd: ExecutableCommand,
        *,
        on_stdout: Callable[[str], object] | None = None,
        on_stderr: Callable[[str], object] | None = None,
    ) -> ExecResult:
        workdir = self._resolve_workdir(ctx, cmd)
        Path(workdir).mkdir(parents=True, exist_ok=True)

        # best-effort: ensure host artifacts dir exists if provided
        ad = (cmd.artifacts_dir or "").strip()
        if ad:
            Path(ad).mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({k: str(v) for k, v in (cmd.env or {}).items()})

        try:
            p = subprocess.Popen(
                list(cmd.cmd),
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"Command not found: {cmd.cmd[0]}") from e

        out_chunks: list[str] = []
        err_chunks: list[str] = []
        stop_requested = threading.Event()
        stdin_lock = threading.RLock()
        stdin_closed = threading.Event()

        assert p.stdout is not None
        assert p.stderr is not None

        def pump(stream, sink: list[str], cb: Callable[[str], object] | None) -> None:
            for line in stream:
                sink.append(line)
                if cb:
                    keep = cb(line)
                    if isinstance(keep, str) and not stdin_closed.is_set():
                        try:
                            with stdin_lock:
                                assert p.stdin is not None
                                p.stdin.write(keep)
                                p.stdin.flush()
                        except Exception:
                            pass
                    if keep is False:
                        stop_requested.set()
                        break

        t_out = threading.Thread(target=pump, args=(p.stdout, out_chunks, on_stdout), daemon=True)
        t_err = threading.Thread(target=pump, args=(p.stderr, err_chunks, on_stderr), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        timeout_seconds = int(cmd.timeout_seconds) if cmd.timeout_seconds is not None else None
        deadline = time.monotonic() + float(timeout_seconds) if timeout_seconds is not None else None
        code = 0
        while True:
            if stop_requested.is_set():
                self._terminate_process(p)
                code = 130
                break
            polled = p.poll()
            if polled is not None:
                code = int(polled)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                self._terminate_process(p)
                code = 124
                timeout_msg = f"\nTESTFABRIC TIMEOUT: command exceeded {timeout_seconds}s\n"
                err_chunks.append(timeout_msg)
                if on_stderr:
                    on_stderr(timeout_msg)
                break
            time.sleep(0.05)

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        try:
            p.stdout.close()
        except Exception:
            pass
        try:
            p.stderr.close()
        except Exception:
            pass
        try:
            stdin_closed.set()
            if p.stdin is not None:
                p.stdin.close()
        except Exception:
            pass

        return ExecResult(
            exit_code=int(code),
            stdout="".join(out_chunks),
            stderr="".join(err_chunks),
            timed_out=timed_out,
        )

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

        try:
            process.wait(timeout=3)
            return
        except Exception:
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _resolve_workdir(self, ctx: StageContext, cmd: ExecutableCommand) -> str:
        """
        If workdir_repo=True => cmd.workdir is relative to ctx.repo_path
        Else => cmd.workdir is treated as an absolute host path (expanded + resolved)
        """
        wd = (cmd.workdir or ".").strip() or "."
        if not bool(cmd.workdir_repo):
            return str(Path(wd).expanduser().resolve())

        base = Path(ctx.repo_path).expanduser().resolve()
        return str((base / wd).resolve())
