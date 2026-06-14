#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  exec "$repo_root/.venv/bin/python" "$repo_root/scripts/run_example_specs.py" "$@"
fi

exec python3 "$repo_root/scripts/run_example_specs.py" "$@"
