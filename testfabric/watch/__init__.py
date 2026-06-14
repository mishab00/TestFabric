# testfabric.watch — Reactive watcher engine
#
# Micro-package: testfabric[watch]
# Dependencies: testfabric.core only (stdlib otherwise)
#
# Provides:
#   - WatchRuntime: file/HTTP/metric/remote-file watchers
#   - WatchAbort exception
#   - Pattern-based matching (regex, sequence, numeric thresholds)
#   - Action system (report, fail, script, send, success)
#
# Usage:
#   from testfabric.watch import WatchRuntime, WatchAbort
#
#   watch = WatchRuntime(
#       {"sources": [...], "watchers": [...]},
#       events=my_event_sink,
#   )
#   watch.start(phase="run")
#   watch.feed_output("some output text", stream="stdout")
#   watch.stop()

from .runtime import WatchRuntime, WatchAbort

__all__ = ["WatchAbort", "WatchRuntime"]
