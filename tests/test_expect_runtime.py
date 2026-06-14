from __future__ import annotations

from dataclasses import dataclass
import unittest

from testfabric.expect.runtime import ExpectRuntime


@dataclass
class _RecordingEvents:
    events: list[object]

    def emit(self, event):
        self.events.append(event)

    def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
        return


class ExpectRuntimeTests(unittest.TestCase):
    def test_expect_runtime_sends_prompt_response_once(self) -> None:
        events = _RecordingEvents([])
        runtime = ExpectRuntime(
            [
                {
                    "name": "reply_password",
                    "when": {"match": "Password:"},
                    "then": {"send": "secret"},
                }
            ],
            events=events,
            run_scope={"run_id": "run-001", "stage_id": "stage-1", "job_id": "job-1"},
        )

        first = runtime.feed_output("Password:\n", {"stream": "stdout", "phase": "job", "attempt": 0})
        repeat = runtime.feed_output("Password:\n", {"stream": "stdout", "phase": "job", "attempt": 0})

        self.assertEqual(first, "secret\n")
        self.assertIsNone(repeat)
        self.assertEqual(runtime.sent, ["secret\n"])
        self.assertEqual(len([ev for ev in events.events if getattr(ev, "component", None) == "expect"]), 1)


if __name__ == "__main__":
    unittest.main()
