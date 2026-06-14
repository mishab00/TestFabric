# testfabric.core — Zero-dependency foundation layer
#
# Micro-package: testfabric[core]  (no extra deps for most items)
#
# Provides:
#   - Event system (EventSink, Event, FileEvents, NullEvents)
#   - Execution context dataclasses (RunContext, StageContext, DockerRuntime, …)
#   - API contracts (RunRequest, RunResult, RunStatus, …)
#   - Environment helpers (merge_env, expand_vars, expand_dict)
#   - Serialization helpers (to_dict_like)
#   - Redaction (SecretRedactor, redact_text, redact_data)
#   - Constants

from testfabric.core.constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_ARTIFACTS_DIR_NAME,
    DEFAULT_CONTEXT_MODE_LOCAL,
    DEFAULT_CONTEXT_MODE_REMOTE,
    DEFAULT_CONTEXT_NAME,
    DEFAULT_REMOTE_API_URL,
    DEFAULT_RUN_SUMMARY_FILENAME,
    DEFAULT_WORKER_ID,
    RUN_STATE_CANCELED,
    RUN_STATE_COMPLETED,
    RUN_STATE_DRY_RUN,
    RUN_STATE_FAILED,
    RUN_STATE_QUEUED,
    RUN_STATE_RUNNING,
    WORKER_STATE_DEGRADED,
    WORKER_STATE_HEALTHY,
    WORKER_STATE_STALE,
    WORKER_STATE_UNHEALTHY,
)

from testfabric.core.contracts import (
    CompareRequest,
    ReplayRequest,
    RunLease,
    RunQuery,
    RunRequest,
    RunResponse,
    RunResult,
    RunState,
    RunStatus,
    WorkerRegistration,
    WorkerStatus,
)

from testfabric.core.events import (
    Event,
    EventSink,
    FileEvents,
    NullEvents,
    prune_none,
    utc_ts,
)

from testfabric.core.envs import (
    expand_dict,
    expand_vars,
    merge_env,
)

from testfabric.core.serialize import to_dict_like

from testfabric.core.redaction import (
    DEFAULT_SECRET_FIELD_NAMES,
    MASK,
    SecretRedactor,
    redact_data,
    redact_text,
)

__all__ = [
    # Constants
    "APP_NAME",
    "APP_VERSION",
    # Contracts
    "CompareRequest",
    "ReplayRequest",
    "RunLease",
    "RunQuery",
    "RunRequest",
    "RunResponse",
    "RunResult",
    "RunState",
    "RunStatus",
    "WorkerRegistration",
    "WorkerStatus",
    # Events
    "Event",
    "EventSink",
    "FileEvents",
    "NullEvents",
    "prune_none",
    "utc_ts",
    # Env
    "expand_dict",
    "expand_vars",
    "merge_env",
    # Serialize
    "to_dict_like",
    # Redaction
    "DEFAULT_SECRET_FIELD_NAMES",
    "MASK",
    "SecretRedactor",
    "redact_data",
    "redact_text",
]
