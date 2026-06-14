# TestFabric

Scenario-first test orchestration framework for mixed `command` and `pytest` pipelines.

## What It Does

TestFabric executes a stage pipeline where each stage references a reusable suite.

- `pipeline.stages`: execution order
- `suites`: reusable workload definitions
- `runner`: workload semantics (`pytest` or `command`)
- `executor`: environment (`local` or `docker`)

Stages execute sequentially. Jobs inside a stage execute in parallel according to stage `max_workers` or the global `parallelism.max_workers` default.

## Current Support

- Implemented: `local`, `docker` executors
- Recommended remote path: remote Docker targets driven by grouped `targets` and `credentials`
- Not implemented yet: `linode` workers/executors

## Install

```bash
poetry install
```

## CLI

```bash
poetry run testfabric run.yaml
```

Useful options:

```bash
poetry run testfabric run.yaml \
  --profile remote \
  --suite e2e \
  --build \
  --input token=XXXX \
  --input artifactory_username=svc-build \
  --lint-spec
```

The terminal recap now mirrors the same report modules that are saved in `summary.json`, so the CLI and saved run report stay aligned around:

- `run`
- `totals`
- `targets`
- `failures`
- `events`
- `artifacts`

For long-running or very large runs, you can also control live terminal noise with CLI verbosity:

```bash
poetry run testfabric run.yaml --verbosity summary
```

Supported values:

- `quiet`: keep writing logs and `events.jsonl`, but suppress live event and stream output
- `summary`: show run/stage/health/dispatcher progress and failures, but suppress per-job success chatter and stream lines
- `normal`: show structured events including job start/end, but suppress streamed stdout/stderr lines
- `verbose`: show both structured events and streamed stdout/stderr lines

You can also set a default in YAML:

```yaml
run:
  console_verbosity: summary
```

## Typed Inputs And Profiles

`RunSpec.load()` now resolves a spec in this order:

- schema defaults
- selected profile inputs/overlays
- explicitly declared environment and file-backed inputs
- CLI `--input KEY=VALUE`
- `${inputs.name}` expansion

Use `inputs` for values that vary by environment, secret source, or caller:

```yaml
inputs:
  env_name:
    required: true
  token:
    type: secret
    env: TOKEN
    required: true
  artifactory_api_key:
    type: secret
    env: ARTIFACTORY_API_KEY
    required: true

profiles:
  remote:
    inputs:
      env_name: remote
  devenv:
    inputs:
      env_name: devenv
```

If a run needs values from several environment variables, declare each one explicitly:

```yaml
inputs:
  token:
    type: secret
    env: TOKEN
    required: true
  artifactory_api_key:
    type: secret
    env: ARTIFACTORY_API_KEY
    required: true
  registry_password:
    type: secret
    env: PASSWORD
    required: true
```

Supported expansion syntax:

- `${inputs.name}`: empty string if missing
- `${inputs.name:-default}`: default value if missing/empty
- `${inputs.name?message}`: fail fast with message if missing/empty

Example:

```yaml
suites:
  e2e:
    kind: pytest
    path: tests/e2e
    args: "--env=${inputs.env_name} --token=${inputs.token} -q"
    extra_env:
      TOKEN: "${inputs.token}"
```

You can then run the same spec in different environments without changing the YAML:

```bash
poetry run testfabric run_local.yaml --profile remote --input token=XXXX
```

Important:

- inputs only resolve from environment when the input explicitly declares `env: NAME`
- raw `${NAME}` expansion is no longer part of the public config contract
- this applies the same way when several env-backed inputs are used in the same spec
- inputs declared as `type: secret` are now redacted from live event logs, streamed logs, per-job logs, and pytest collect logs
- spec models are now strict: unknown YAML keys are rejected during load instead of being silently ignored
- raw shell runtime env like `$TESTFABRIC_ARTIFACTS_DIR` and `$TESTFABRIC_JOB_ARTIFACTS_DIR` is preserved inside command strings; only braced `${...}` expressions are expanded by spec loading

## Minimal Local Specs

TestFabric no longer needs an external Git URL for simple local use. A local repository path such as `.` is valid for `run.repo_url`, and the local-friendly defaults now work for a truly minimal command spec:

- `run.repo_url` defaults to `.`
- `run.ref` defaults to `HEAD`
- `run.workdir` defaults to `.testfabric/work`
- `run.artifacts_dir` defaults to `artifacts`
- `parallelism.max_workers` defaults to `1`
- command suites default to the `local` executor unless you explicitly set `executor: docker`

For command steps you now have three shapes:

- `cmd`: argv list for plain commands with no shell features
- `bash`: bash script text, or a YAML list of lines that will be joined with newlines
- `shell`: POSIX `sh` script text, or a YAML list of lines that will be joined with newlines

Use `bash` or `shell` when you need shell features like redirection, pipes, or `$TESTFABRIC_*` variable expansion.
For plain command output capture, prefer `stdout_to` / `stderr_to` instead of shell redirection. These step-level fields work with `cmd`, `bash`, and `shell`, are relative to `TESTFABRIC_ARTIFACTS_DIR`, and may include runtime env like `$TESTFABRIC_JOB_ID`.
For simple one-line commands in examples, `shell` is often the nicest readable default. Keep `cmd` for cases where you want explicit argv tokens with no shell interpretation.

Minimal one-shot local example:

```yaml
pipeline:
  stages:
    - suite: curl_google

suites:
  curl_google:
    steps:
      - name: curl-google
        shell: curl -I -sS https://google.com
        stdout_to: google-headers.txt
```

Run-level output now stays on one unified saved contract:

- `summary.json`: user-facing machine-readable run summary
- `events.jsonl`: raw chronological event log
- `workers/<worker>/stages/<stage>/stage-summary.json`: per-stage summary and artifact inventory

The reporting contract is intentionally data-first right now:

- `summary.json` is the main run report
- `summary.json` includes compact modules for `run`, `totals`, `targets`, `stages`, `failures`, `artifacts`, `events`, and `debug`
- `events.jsonl` is the raw timeline
- each `stage-summary.json` mirrors the same stage shape used inside `summary.json`
- the run-level `artifacts` module now carries labeled primary entries plus category/source counts, so the CLI and future renderers can point to meaningful outputs instead of only raw paths

## Remote Targets

TestFabric supports grouped `targets` plus reusable `credentials`. The recommended remote path is `executor: docker` against a selected remote Docker target.

Canonical grouped target format:

```yaml
targets:
  clients:
    client-a:
      address: 1.1.1.1
      labels:
        role: client
    client-b:
      address: 1.1.1.1
  servers:
    server-a:
      address: 1.1.1.1
```

With external files, keep the same grouped shape:

```yaml
clients:
  client-a:
    address: 1.1.1.1
    credential: client_key
  client-b:
    address: 1.1.1.1

servers:
  server-a:
    address: 1.1.1.1
```

Selectors:

- `all`
- `group:<name>`
- `host:<name>`

Example:

```yaml
targets:
  file: inventory.yaml

credentials:
  file: credentials.yaml

pipeline:
  stages:
    - suite: smoke
      executor: docker
      targets: clients
```

Rules:

- `targets.file` and `credentials.file` are resolved relative to the main spec file
- stage `targets:` chooses which target group or host to run on
- for remote Docker, `credentials` supply the connection identity for the Docker control plane
- target `transport` can override credential defaults per host, including removing inherited routing with `via: null`
- Docker must already be installed on the remote target
- password auth is not implemented for the remote Docker control plane; use key-based access

Examples:

```bash
poetry run testfabric run_remote_docker.yaml --mode dry-run
poetry run testfabric run_remote_docker.yaml
```

`dry-run` produces one planned stage per selected target and a run summary with real per-target rows. `run` mode executes Docker workloads on the selected remote target while keeping the normal artifact/report contract on the controller.

## Watchers

Watchers let you react to live signals during a run without hard-coding every decision into the main command itself.

- `session` sources watch live stdout/stderr from a job
- `file` sources watch local files by polling or tailing
- `http` sources poll API endpoints like `/health`
- `remote_file` sources watch a remote file over SSH through inventory-backed target resolution
- `metric` sources poll a command and let watchers threshold the numeric output, including inventory-backed remote host commands
- `watchers` are global and describe what condition is being watched
- `then` carries the response, such as `report`, `fail`, or `shell`
- `report` is the annotation action and can carry an optional custom message
- `shell` is the direct escape hatch for custom one-line or multiline scripts
- `when.sequence` watches ordered message flows and `missing` can warn or fail if the flow never completes
- metric/threshold watchers can use checks like `gt`, `gte`, `lt`, and `lte`
- shell/script actions receive run/stage/job/target/workers IDs in their environment
- command suites can also carry an `expect` block for prompt/response flows
- `session` sources can watch `stdout` or `stderr` as separate streams

Small example with comments:

```yaml
watch:
  phases: [run, stage]
  sources:
    # Poll a health endpoint while the stage is running.
    - name: api_health
      type: http
      phases: [run, stage]
      url: "http://127.0.0.1:${inputs.http_watch_port}/health"
      mode: poll
      interval_seconds: 1
  watchers:
    # Report when the endpoint is degraded.
    - name: api_degraded
      source: api_health
      when:
        status: 500
        match: degraded
      then:
        report:
          message: "API health reported a degraded state"

pipeline:
  stages:
    # The stage does its normal work while the watcher observes the endpoint.
    - title: observe_api_health
      suite: watch_http_health
      executor: local
```

The checked-in [`run_watch_local.yaml`](run_watch_local.yaml) example shows the same watcher model with a custom `shell` response that echoes the matched text. You can swap that shell for a multiline cleanup command such as `docker rm -f ...` when you want a teardown-style response, and [`run_watch_teardown.yaml`](run_watch_teardown.yaml) shows that pattern in a teardown phase.
[`run_watch_file.yaml`](run_watch_file.yaml) shows the same watcher model against the run's streamed stdout log.
[`run_watch_remote_file.yaml`](run_watch_remote_file.yaml) shows the same watcher model against a remote file over SSH using inventory-backed target resolution.
[`run_watch_remote_metric.yaml`](run_watch_remote_metric.yaml) shows the same inventory-backed transport for a remote host metric command.
[`run_watch_metric.yaml`](run_watch_metric.yaml) shows metric sources thresholded with `gt` checks.
[`run_watch_sequence.yaml`](run_watch_sequence.yaml) shows an ordered message flow watcher that reports when the final milestone appears.
[`run_watch_sequence_missing.yaml`](run_watch_sequence_missing.yaml) shows the same ordered flow watcher failing when the final milestone never appears.

This is useful for:
- interactive prompts
- log scanning
- API health checks
- custom script actions for echo/cleanup tasks
- fail-fast checks for remote Docker or command output

Example files in this repo:

- [run_command_interactive_local.yaml](run_command_interactive_local.yaml)
- [run_watch_local.yaml](run_watch_local.yaml)
- [run_watch_file.yaml](run_watch_file.yaml)
- [run_watch_http.yaml](run_watch_http.yaml)
- [run_watch_metric.yaml](run_watch_metric.yaml)
- [run_watch_sequence.yaml](run_watch_sequence.yaml)
- [run_watch_sequence_missing.yaml](run_watch_sequence_missing.yaml)
- [run_watch_stderr.yaml](run_watch_stderr.yaml)
- [run_watch_remote_file.yaml](run_watch_remote_file.yaml)
- [run_watch_remote_metric.yaml](run_watch_remote_metric.yaml)
- [run_watch_remote_docker.yaml](run_watch_remote_docker.yaml)
- [run_watch_teardown.yaml](run_watch_teardown.yaml)

Repeated serial variant with only a small delta:

```yaml
pipeline:
  stages:
    - suite: curl_google
      execution:
        mode: repeat
        count: 5

suites:
  curl_google:
    steps:
      - name: write-job-id
        bash:
          - mkdir -p "$TESTFABRIC_JOB_ARTIFACTS_DIR"
          - printf "%s\n" "$TESTFABRIC_JOB_ID" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/job.txt"
      - name: curl-google
        shell: curl -I -sS https://google.com
        stdout_to: google-$TESTFABRIC_JOB_ID.txt
```

Repeated parallel variant with only a small delta:

```yaml
pipeline:
  stages:
    - suite: curl_google
      max_workers: 5
      execution:
        mode: repeat
        count: 5

suites:
  curl_google:
    steps:
      - name: write-job-id
        bash:
          - mkdir -p "$TESTFABRIC_JOB_ARTIFACTS_DIR"
          - printf "%s\n" "$TESTFABRIC_JOB_ID" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/job.txt"
      - name: curl-google
        shell: curl -I -sS https://google.com
        stdout_to: google-$TESTFABRIC_JOB_ID.txt
```

If you want one global concurrency limit for all stages, put it under `parallelism.max_workers`
and omit stage `max_workers`:

```yaml
parallelism:
  max_workers: 3

pipeline:
  stages:
    - suite: curl_google
      execution:
        mode: repeat
        count: 5

suites:
  curl_google:
    steps:
      - name: write-job-id
        bash:
          - mkdir -p "$TESTFABRIC_JOB_ARTIFACTS_DIR"
          - printf "%s\n" "$TESTFABRIC_JOB_ID" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/job.txt"
      - name: curl-google
        shell: curl -I -sS https://google.com
        stdout_to: google-$TESTFABRIC_JOB_ID.txt
```

In that example, `execution.count: 5` creates 5 jobs total, `parallelism.max_workers: 3`
lets 3 run at once, and the remaining 2 stay queued until a slot is free.

Example files in this repo:

- [run_minimal_artifact_local.yaml](run_minimal_artifact_local.yaml)
- [run_minimal_curl_local.yaml](run_minimal_curl_local.yaml)
- [run_minimal_curl_repeat.yaml](run_minimal_curl_repeat.yaml)
- [run_minimal_curl_repeat_parallel.yaml](run_minimal_curl_repeat_parallel.yaml)
- [run_minimal_curl_repeat_parallel_global.yaml](run_minimal_curl_repeat_parallel_global.yaml)
- [run_minimal_nping_docker.yaml](run_minimal_nping_docker.yaml)

## Workspace Isolation

Controller-side checkouts are now isolated by repository identity and ref under the configured `run.workdir`.

What this gives us:

- different projects can share the same base workdir safely
- different refs from the same project do not reuse the same working tree
- an existing checkout with the wrong Git origin is rejected instead of silently reused

This matters for black-box use across many projects, because TestFabric no longer assumes a single shared `repo/` checkout.

## Managed Docker

Docker now supports three modes:

- `custom`: use the repo Dockerfile explicitly
- `template`: generate a managed Dockerfile from the repo and TestFabric settings
- `auto`: use the repo Dockerfile if it exists, otherwise fall back to the managed Dockerfile

Example:

```yaml
docker:
  mode: auto
  image: your-tests:ci
  base_image: python:3.11-slim
  python_requirements:
    - requirements.txt
  system_packages:
    - git
    - curl
```

What this gives us:

- Docker isolation without forcing every project to maintain a custom Dockerfile
- the same isolation path for `pytest` and `command` suites
- cleaner host environments when many projects run on the same machine
- a simpler adoption path for black-box usage
- managed Docker no longer forces a `pip` upgrade when the stage does not actually install Python dependencies, which makes trivial Docker runs more offline-friendly

Testing note:

- [tests/test_docker_execution.py](/Users/mbetekht/PycharmProjects/TestFabric/tests/test_docker_execution.py) covers Docker build/run orchestration logic with mocks
- [tests/test_docker_live.py](/Users/mbetekht/PycharmProjects/TestFabric/tests/test_docker_live.py) is a daemon-gated live integration test
- the live test skips cleanly when Docker is unavailable
- if Docker is available but your preferred cached base image is not `python:3.11-slim`, set `TESTFABRIC_DOCKER_BASE_IMAGE` before running the test suite

## Artifacts

TestFabric injects a stable artifacts contract into executed work:

- `TESTFABRIC_ARTIFACTS_DIR`
- `TESTFABRIC_JOB_ARTIFACTS_DIR`
- `TESTFABRIC_RUN_ID`
- `TESTFABRIC_STAGE_ID`
- `TESTFABRIC_JOB_ID`
- `TESTFABRIC_ATTEMPT`

What this gives us:

- tests and scripts can write artifacts without knowing the final CI layout
- the same contract works for local and Docker execution
- each stage summary carries a compact artifact inventory without requiring a second manifest file

Current behavior:

- files under `reports/`, `logs/`, and per-job `jobs/<job>/attempt-<n>/` logs are indexed automatically
- stage summaries include a compact artifact block with counts and primary file paths
- run summaries surface the same stage artifact information directly
- worker staging now defaults to `<artifacts_dir>/workers_tmp`, so staging and final mirrored output stay under the same artifact root unless you override `run.workers_tmp`
- changing `run.artifacts_dir` or `run.workers_tmp` changes the actual execution layout and the injected `TESTFABRIC_*` paths accordingly

Usage model:

- TestFabric owns the directory structure, mirroring, and summary generation
- users do not need to create or manage the full tree manually
- if a test or command wants to publish custom outputs, it only needs to write files into `TESTFABRIC_ARTIFACTS_DIR` or `TESTFABRIC_JOB_ARTIFACTS_DIR`

In other words:

- summary JSON tells you what happened
- stage summary JSON tells you which files were collected for that stage
- the actual files under `logs/` and `reports/` are the payload

Example command usage:

```bash
mkdir -p "$TESTFABRIC_JOB_ARTIFACTS_DIR"
printf "hello\n" > "$TESTFABRIC_ARTIFACTS_DIR/custom.txt"
printf "trace\n" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/trace.txt"
```

Example files in this repo:

- [run_stress_local.yaml](run_stress_local.yaml) writes outputs with `TESTFABRIC_JOB_ARTIFACTS_DIR`
- [run_split_local.yaml](run_split_local.yaml) writes outputs from split command steps

## Execution Modes

Stage execution now supports:

- `once`: the normal mode
- `repeat`: repeat the collected work items a fixed number of times

Example:

```yaml
pipeline:
  stages:
    - title: stress-script
      suite: smoke_cmd
      executor: local
      max_workers: 5
      execution:
        mode: repeat
        count: 100
```

What this gives us:

- local stress execution without Docker
- the same repeat model for local and Docker executors
- a clean separation between repeat and retry

Current first-version behavior:

- `max_workers` still controls concurrency
- `execution.count` controls total repeated executions
- without `split`, repeat expands collected work items into repeated jobs
- with `split`, repeat clones the split groups, which is a good fit for repeating the same parallel partition multiple times

Example file in this repo:

- [run_stress_local.yaml](run_stress_local.yaml) shows a local repeated command run with health preflight and artifact output
- [run_repeat_split_local.yaml](run_repeat_split_local.yaml) shows a repeated local stage that also uses split groups

## Lifecycle

Stages now support simple lifecycle metadata:

- `run`: the normal stage mode
- `setup`: preparation stage
- `teardown`: cleanup stage
- `when: always`: run even after an earlier stage failed

Example:

```yaml
pipeline:
  stages:
    - title: bringup
      suite: prepare_env
      lifecycle:
        mode: setup

    - title: tests
      suite: e2e

    - title: cleanup
      suite: cleanup_env
      lifecycle:
        mode: teardown
        when: always
```

What this gives us:

- predictable setup and teardown semantics
- reliable cleanup after failures
- grouped lifecycle behavior instead of only global stop-on-first-failure

Current behavior:

- a failing setup or run stage only blocks the rest of its current lifecycle group
- `when: always` allows teardown-style stages to run after earlier failures in that group
- a new `setup` stage starts a fresh lifecycle group
- skipped stages are recorded in the run summary with a skip reason

Example file in this repo:

- [run_lifecycle_groups_local.yaml](run_lifecycle_groups_local.yaml) shows two local lifecycle groups in one pipeline

## Split Execution

Stages can now split collected work into a fixed number of execution groups:

```yaml
pipeline:
  stages:
    - title: split-tests
      suite: e2e
      executor: local
      split:
        count: 4
```

What this gives us:

- one logical stage in YAML instead of duplicating nearly identical stages
- a simple Jenkins-like split model for tests or command steps
- balanced count-based grouping by default, with optional manifest-driven grouping

Current first-version behavior:

- `split.count` is optional and defaults to `1`
- count-based split is the default shape when `split.manifest_path` is not set
- `split.manifest_path` switches the stage to manifest-driven grouping
- when combined with `execution.mode: repeat`, split groups are repeated as whole jobs

Example file in this repo:

- [run_split_local.yaml](run_split_local.yaml) shows a local command stage split into three execution groups
- [run_repeat_split_local.yaml](run_repeat_split_local.yaml) shows split groups repeated multiple times
- [run_split_manifest_local.yaml](run_split_manifest_local.yaml) shows manifest-driven split groups for local execution
- [manifests/split.json](manifests/split.json) is a real sample manifest file used by the manifest example

## Timeouts

Stages now support explicit safety timeouts:

- `timeout.job_seconds`: maximum runtime for one job attempt
- `timeout.stage_seconds`: maximum wall-clock budget for the whole stage

Example:

```yaml
pipeline:
  stages:
    - title: guarded-command
      suite: smoke_cmd
      executor: local
      timeout:
        job_seconds: 30
        stage_seconds: 120
```

What this gives us:

- long-running or stuck jobs fail predictably instead of hanging forever
- stage-level budgets work for repeated and split execution too
- timeout failures are surfaced distinctly from ordinary execution failures

Current first-version behavior:

- local and Docker executors both honor job timeouts
- stage timeout stops launching new work after the stage budget is exhausted
- pending work is marked as timed out once the stage budget is gone

Example file in this repo:

- [run_timeout_local.yaml](run_timeout_local.yaml) shows a local command stage that is expected to time out

## Minimal Spec

```yaml
run:
  name: sample
  repo_url: git@github.com:yourorg/yourrepo.git
  ref: main
  workdir: ./work
  artifacts_dir: ./artifacts

inputs:
  env_name:
    required: true
  token:
    type: secret
    env: TOKEN
    required: true

profiles:
  remote:
    inputs:
      env_name: remote

workers:
  mode: local
  max_workers: 4

pipeline:
  default_executor: local
  stages:
    - title: precheck
      suite: precheck
      executor: local
    - title: tests
      suite: e2e
      executor: docker
      build: true

suites:
  precheck:
    kind: command
    steps:
      - name: whoami
        cmd: ["bash", "-lc", "whoami"]

  e2e:
    kind: pytest
    path: tests/e2e
    rootdir: tests
    select:
      - api/test_users.py
    args: "--env=${inputs.env_name} --token=${inputs.token} -q"
    extra_env:
      TOKEN: "${inputs.token}"

docker:
  dockerfile: Dockerfile
  image: your-tests:ci
  repo_mount: /work
  workdir: /work
  run:
    network: host
    shm_size: 1g

parallelism:
  max_workers: 4
  chunk_size: 10
  max_retries: 1

outputs:
  junit: junit.xml
  html: report.html
```

## Health Preflight

Before stage execution starts, TestFabric now runs a worker preflight health check.

Current checks:

- free disk space on the paths TestFabric needs to write to
- writable-path checks for the run directory, worker temp area, artifacts area, and checked-out repo
- available memory if `health.min_mem_gb` is configured
- Docker daemon connectivity when the spec uses Docker, or when `health.require_docker: true`

Current health states:

- `healthy`: execution continues normally
- `degraded`: execution continues, but the run summary records warning-level issues
- `unhealthy`: execution fails early before stages start

Example:

```yaml
health:
  require_docker: false
  min_disk_gb: 20
  min_mem_gb: 8
```

Why this helps:

- catches infrastructure problems before they appear as fake test failures
- makes Docker-related failures fail fast with a clear reason
- detects unwritable artifact or workspace paths early
- gives a run-level health snapshot in the unified `summary.json`
- keeps the YAML portable because Docker is required automatically when the plan actually uses Docker

Current scope:

- implemented for local controller-side execution
- Docker preflight verifies daemon connectivity and basic daemon info
- remote-target health is a later step when inventory-backed remote workers are added

## Execution Model

For each stage:

1. Acquire worker slot
2. Create stage directories
3. Optionally build Docker image (`build: true`, docker stages only)
4. Collect items from runner
5. Chunk items into jobs
6. Dispatch jobs with retries
7. Write stage summary and mirror artifacts

Run-level summaries are written to:

- `<artifacts_dir>/<runs_subdir>/<run_id>/summary.json`
- `<artifacts_dir>/<runs_subdir>/<run_id>/events.jsonl`
- `<artifacts_dir>/<runs_subdir>/<run_id>/workers/<worker>/stages/<stage>/stage-summary.json`

`summary.json` is the unified run-level data contract. `events.jsonl` is the raw event stream, and each `stage-summary.json` is the canonical per-stage report.

## Project Layout (Current)

```text
testfabric/
  cli/
  core/
  spec/
  workspace/
  orchestrator/
    plan/
    stage/
    dispatch/
  execution/
    executors/
    suites/
  artifacts/
  reporting/
```

## Notes

- If `workers.max_workers` is omitted, local worker capacity is derived from the requested stage/global concurrency.
- If `workers.max_workers` is explicitly set, `parallelism.max_workers` and stage `max_workers` must not exceed it.
- The checked-in artifact example test keeps one real run tree under `artifacts/test-runs/example-minimal-artifact/` so you can inspect a concrete result layout after running the suite.
- For stable manual inspection, run:
  `./scripts/run_example_specs.sh`
  This writes each selected example under `artifacts/manual-runs/<spec-name>/`.
- In those stable roots:
  `work/` is the checked-out workspace used for execution.
  `artifacts/` is the TestFabric output root for that sandbox.
  Under `artifacts/`, `runs/` stores final mirrored runs and `workers_tmp/` stores worker staging content.
- Keep secrets in environment variables, not committed YAML.
- `run_remote_docker.yaml` shows the recommended remote pattern: local controller, remote Docker target, normal TestFabric reports/artifacts on the controller side.

#Refactor
While originally it was a cool idea, to create a framework for test runs, I think the project can benefit more if it is split into several tools that I will be able to add to my project or to any project as a helper
For example, the idea of threads, Docker sounds very good, and can be used - the benefits are enormous, async, threads, and any other tool from inside Python simply not working well when we need long-running instances, for example, for traffic or something else like a pytest pipeline, so threads that are based on Docker can be beneficial even without all the framework
