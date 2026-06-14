# Portal Readiness Fixtures

These checked-in specs are meant to keep the TestFabric execution contract stable while we design the separate portal.

1. `run_portal_simple_success.yaml`
   - Validates the dashboard and run detail happy path.
   - Shows a small multi-stage run with persisted artifacts and a clean pass.

2. `run_portal_late_failure.yaml`
   - Validates failure surfacing in the run detail and stage detail views.
   - Shows a run that passes early stages and fails late with a clear failure headline.

3. `run_portal_watcher_heavy.yaml`
   - Validates watcher timelines, watcher-hit summaries, and metric display.
   - Shows session, file, and metric watchers all firing in one run.

4. `run_portal_retry.yaml`
   - Validates job attempt history and retry comparison views.
   - Shows a job that fails once, retries, then passes on the final attempt.

5. `run_portal_remote_performance.yaml`
   - Validates inventory-backed remote execution visibility and performance history.
   - Shows a remote-target metric source that can feed the future inventory and performance pages.
