from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


DEFAULT_LOCAL_SPECS = [
    "run_minimal_artifact_local.yaml",
    "run_minimal_curl_local.yaml",
    "run_minimal_curl_repeat.yaml",
    "run_minimal_curl_repeat_parallel.yaml",
    "run_minimal_curl_repeat_parallel_global.yaml",
    "run_lifecycle_groups_local.yaml",
    "run_split_local.yaml",
    "run_repeat_split_local.yaml",
    "run_split_manifest_local.yaml",
    "run_stress_local.yaml",
    "run_timeout_local.yaml",
]

OPTIONAL_DOCKER_SPECS = [
    "run_minimal_nping_docker.yaml",
]


def _slugify(value: str) -> str:
    out: list[str] = []
    last_dash = False
    for ch in (value or "").strip().lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "run"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_specs(include_docker: bool) -> list[Path]:
    repo_root = _repo_root()
    names = list(DEFAULT_LOCAL_SPECS)
    if include_docker:
        names.extend(OPTIONAL_DOCKER_SPECS)
    return [repo_root / name for name in names]


def _selected_specs(args: list[str], *, include_docker: bool) -> list[Path]:
    if not args:
        return _default_specs(include_docker)

    repo_root = _repo_root()
    selected: list[Path] = []
    for raw in args:
        p = Path(raw)
        if not p.is_absolute():
            p = (repo_root / raw).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Spec file not found: {p}")
        selected.append(p)
    return selected


def _prepare_spec(spec_path: Path, *, clean: bool) -> tuple[RunSpec, Path, str]:
    repo_root = _repo_root()
    spec = RunSpec.load(str(spec_path))
    root = repo_root / "artifacts" / "manual-runs" / spec_path.stem
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    if str(getattr(spec.run, "repo_url", "") or "").strip() in {"", "."}:
        spec.run.repo_url = str(repo_root)
    spec.run.workdir = str(root / "work")
    spec.run.artifacts_dir = str(root / "artifacts")

    run_id = f"manual-{_slugify(spec_path.stem)}"
    return spec, root, run_id


def _run_spec(spec_path: Path, *, clean: bool) -> bool:
    spec, root, run_id = _prepare_spec(spec_path, clean=clean)
    result = Orchestrator(spec, RunOptions.from_spec_and_cli(spec, run_id=run_id)).run()

    print("")
    print(f"=== {spec_path.name} ===")
    print(f"root:     {root}")
    print(f"run_dir:  {result['run_dir']}")
    print(f"summary:  {result.get('run_summary_path')}")
    print(f"status:   {'PASSED' if result.get('ok') else 'FAILED'}")
    if result.get("error"):
        print(f"error:    {result['error']}")
    return bool(result.get("ok"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run checked-in example specs into stable artifacts/manual-runs/<spec>/ roots "
            "so their work/ and artifacts/ layout is easy to inspect."
        )
    )
    parser.add_argument("spec", nargs="*", help="Optional spec paths. Defaults to the safe local examples.")
    parser.add_argument(
        "--include-docker",
        action="store_true",
        help="Also include the checked-in Docker example in the default run set.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the stable manual root before rerunning a spec.",
    )
    ns = parser.parse_args(argv)

    try:
        specs = _selected_specs(ns.spec, include_docker=bool(ns.include_docker))
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    overall_ok = True
    for spec_path in specs:
        ok = _run_spec(spec_path, clean=not bool(ns.no_clean))
        overall_ok = overall_ok and ok

    print("")
    print("Manual example roots live under:")
    print(f"  {_repo_root() / 'artifacts' / 'manual-runs'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
