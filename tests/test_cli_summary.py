from __future__ import annotations

import unittest

from testfabric.cli.main import _render_summary_text


class CliSummaryRenderTests(unittest.TestCase):
    def test_render_summary_text_uses_report_modules(self) -> None:
        payload = {
            "run": {
                "run_id": "demo-run",
                "verdict": "FAILED",
                "duration_seconds": 12.3,
                "repo": {
                    "url": ".",
                    "ref": "HEAD",
                    "sha": "1234567890abcdef",
                },
                "health": {
                    "state": "healthy",
                    "reasons": [],
                },
                "error": None,
                "failure": {
                    "headline": "Smoke Stage / job-0002: assertion failed",
                },
            },
            "totals": {
                "targets_total": 2,
                "stages_total": 3,
                "stages_passed": 1,
                "stages_failed": 1,
                "stages_skipped": 1,
                "stages_by_failure_type": {"infra": 1},
                "jobs_total": 5,
                "jobs_passed": 3,
                "jobs_failed": 1,
                "jobs_timed_out": 1,
                "jobs_by_failure_type": {"test": 1, "timeout": 1},
                "artifacts_total": 4,
            },
            "targets": [
                {
                    "target_id": "local",
                    "name": "local",
                    "display_name": "py311 [docker-template; group:python-matrix; python=3.11]",
                    "kind": "local",
                    "scope": "group:python-matrix",
                    "executor": "local",
                    "verdict": "FAILED",
                    "stages_total": 3,
                    "stages_failed": 1,
                    "jobs_total": 5,
                    "jobs_failed": 1,
                    "duration_seconds": 12.3,
                    "labels": {"python": "3.11"},
                }
            ],
            "stages": [
                {
                    "stage_id": "02-pass",
                    "title": "Pass Stage",
                    "target_id": "local",
                    "target_name": "local",
                    "target_display": "py311 [docker-template; group:python-matrix; python=3.11]",
                    "verdict": "PASSED",
                    "executor": "local",
                    "runner": "command",
                    "jobs_total": 1,
                    "jobs_failed": 0,
                    "duration_seconds": 1.0,
                    "error": None,
                },
                {
                    "stage_id": "01-smoke",
                    "title": "Smoke Stage",
                    "target_id": "local",
                    "target_name": "local",
                    "target_display": "py311 [docker-template; group:python-matrix; python=3.11]",
                    "verdict": "FAILED",
                    "executor": "local",
                    "runner": "pytest",
                    "jobs_total": 2,
                    "jobs_failed": 1,
                    "duration_seconds": 6.0,
                    "error": "1 job failed",
                },
            ],
            "failures": [
                {
                    "scope": "job",
                    "stage_id": "01-smoke",
                    "job_id": "job-0002",
                    "message": "assertion failed",
                }
            ],
            "watch": {
                "total": 1,
                "counts_by_watcher": {"api_degraded": 1},
                "counts_by_source": {"api_health": 1},
                "counts_by_action": {"report": 1},
                "counts_by_status": {"info": 1},
                "first_trigger": {
                    "watcher": "api_degraded",
                    "source": "api_health",
                    "status": "info",
                    "action": "report",
                    "message": "API health reported a degraded state",
                    "target_id": "local",
                    "stage_id": "01-smoke",
                    "job_id": "job-0002",
                    "attempt": 0,
                    "phase": "stage",
                },
            },
            "events": {
                "path": "events.jsonl",
                "total": 7,
                "counts_by_status": {"fail": 1, "start": 3, "success": 3},
                "counts_by_component": {"job": 2, "run": 2, "stage": 3, "watch": 1},
                "counts_by_action": {"job:end": 1, "run:start": 1, "watch:api_degraded": 1},
                "rows": [],
            },
            "artifacts": {
                "files_total": 4,
                "counts_by_category": {"artifact": 2, "junit": 1, "html": 1},
                "counts_by_source": {"reports": 4},
                "primary": [
                    {
                        "label": "JUnit XML",
                        "stage_title": "Smoke Stage",
                        "target_name": "local",
                        "path": "workers/w000/stages/01-smoke/reports/junit.xml",
                    }
                ]
            },
        }

        text = _render_summary_text(payload)

        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("Run", text)
        self.assertIn("- ID: demo-run", text)
        self.assertIn("- Verdict: FAILED", text)
        self.assertIn("- Failure: Smoke Stage / job-0002: assertion failed", text)
        self.assertIn("Totals", text)
        self.assertIn("- Targets: 2", text)
        self.assertIn("- Stages: 3 total, 1 passed, 1 failed, 1 skipped", text)
        self.assertIn("- Jobs: 5 total, 3 passed, 1 failed, 1 timed out", text)
        self.assertIn("- Stage Failures By Type: infra=1", text)
        self.assertIn("- Job Failures By Type: test=1, timeout=1", text)
        self.assertIn("Targets", text)
        self.assertIn("target                                     | kind  | scope", text)
        self.assertIn("py311 [docker-template", text)
        self.assertIn("group:python-matrix", text)
        self.assertIn("python=3.11", text)
        self.assertIn("Failures", text)
        self.assertIn("scope | target | stage", text)
        self.assertIn("job   | -      | 01-smoke | job-0002", text)
        self.assertIn("Watch", text)
        self.assertIn("- Total: 1", text)
        self.assertIn("- By Watcher: api_degraded=1", text)
        self.assertIn("- First Trigger: api_degraded | info | report | api_health | stage=01-smoke | job=job-0002 | API health reported a degraded state", text)
        self.assertIn("Stages", text)
        self.assertIn("stage       | target", text)
        self.assertIn("Smoke Stage | py311 [docker-template", text)
        self.assertIn("Pass Stage  | py311 [docker-template", text)
        self.assertLess(text.index("\nFailures\n"), text.index("\nStages\n"))
        self.assertLess(text.index("\nStages\n"), text.index("\nTargets\n"))
        self.assertIn("Events", text)
        self.assertIn("- Path: events.jsonl", text)
        self.assertIn("- Total: 7", text)
        self.assertIn("- Status: fail=1, start=3, success=3", text)
        self.assertIn("- Components: job=2, run=2, stage=3", text)
        self.assertIn("Artifacts", text)
        self.assertIn("- Files Total: 4", text)
        self.assertIn("- By Category: artifact=2, html=1, junit=1", text)
        self.assertIn("- By Source: reports=4", text)
        self.assertIn("label     | stage", text)
        self.assertIn("JUnit XML | Smoke Stage", text)

    def test_render_summary_text_returns_none_without_run_module(self) -> None:
        self.assertIsNone(_render_summary_text({}))


if __name__ == "__main__":
    unittest.main()
