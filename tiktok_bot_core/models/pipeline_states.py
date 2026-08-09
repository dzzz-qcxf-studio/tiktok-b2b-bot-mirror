"""Shared status values for pipeline jobs and their stages."""

from __future__ import annotations

from collections.abc import Mapping

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_WAITING_DECISION = "waiting_decision"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_PARTIAL_FAILED = "partial_failed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLING = "cancelling"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_INTERRUPTED = "interrupted"

JOB_STATUSES = frozenset(
    {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_WAITING_DECISION,
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_PARTIAL_FAILED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLING,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_INTERRUPTED,
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_PARTIAL_FAILED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_INTERRUPTED,
    }
)

STAGE_STATUS_PENDING = "pending"
STAGE_STATUS_RUNNING = "running"
STAGE_STATUS_WAITING_DECISION = "waiting_decision"
STAGE_STATUS_SUCCEEDED = "succeeded"
STAGE_STATUS_FAILED = "failed"
STAGE_STATUS_SKIPPED = "skipped"
STAGE_STATUS_CANCELLED = "cancelled"

STAGE_STATUSES = frozenset(
    {
        STAGE_STATUS_PENDING,
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_WAITING_DECISION,
        STAGE_STATUS_SUCCEEDED,
        STAGE_STATUS_FAILED,
        STAGE_STATUS_SKIPPED,
        STAGE_STATUS_CANCELLED,
    }
)
TERMINAL_STAGE_STATUSES = frozenset(
    {
        STAGE_STATUS_SUCCEEDED,
        STAGE_STATUS_FAILED,
        STAGE_STATUS_SKIPPED,
        STAGE_STATUS_CANCELLED,
    }
)

PIPELINE_STAGES = (
    "collect",
    "filter",
    "strategy",
    "outreach",
    "report",
    "iterate",
)
KNOWN_PIPELINE_STAGES = frozenset(PIPELINE_STAGES)

JOB_USER_STATUS_PENDING = "pending"
JOB_USER_STATUS_QUALIFIED = "qualified"
JOB_USER_STATUS_REJECTED = "rejected"
JOB_USER_STATUS_CONTACTED = "contacted"
JOB_USER_STATUS_REPLIED = "replied"
JOB_USER_STATUSES = frozenset(
    {
        JOB_USER_STATUS_PENDING,
        JOB_USER_STATUS_QUALIFIED,
        JOB_USER_STATUS_REJECTED,
        JOB_USER_STATUS_CONTACTED,
        JOB_USER_STATUS_REPLIED,
    }
)

DISCOVERY_STATUS_CANDIDATE = "candidate"
DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE = "needs_more_evidence"
DISCOVERY_STATUS_OBVIOUS_IRRELEVANT = "obvious_irrelevant"
DISCOVERY_STATUS_DUPLICATE = "duplicate"
DISCOVERY_STATUS_BLOCKED = "blocked"
DISCOVERY_STATUSES = frozenset(
    {
        DISCOVERY_STATUS_CANDIDATE,
        DISCOVERY_STATUS_NEEDS_MORE_EVIDENCE,
        DISCOVERY_STATUS_OBVIOUS_IRRELEVANT,
        DISCOVERY_STATUS_DUPLICATE,
        DISCOVERY_STATUS_BLOCKED,
    }
)

QUALIFICATION_STATUS_QUALIFIED = "qualified"
QUALIFICATION_STATUS_MANUAL_REVIEW = "manual_review"
QUALIFICATION_STATUS_NEED_ENRICHMENT = "need_enrichment"
QUALIFICATION_STATUS_REJECTED = "rejected"
QUALIFICATION_STATUSES = frozenset(
    {
        QUALIFICATION_STATUS_QUALIFIED,
        QUALIFICATION_STATUS_MANUAL_REVIEW,
        QUALIFICATION_STATUS_NEED_ENRICHMENT,
        QUALIFICATION_STATUS_REJECTED,
    }
)

QUALIFICATION_STATUS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    QUALIFICATION_STATUS_MANUAL_REVIEW: frozenset(
        {
            QUALIFICATION_STATUS_QUALIFIED,
            QUALIFICATION_STATUS_NEED_ENRICHMENT,
            QUALIFICATION_STATUS_REJECTED,
        }
    ),
    QUALIFICATION_STATUS_NEED_ENRICHMENT: frozenset(
        {
            QUALIFICATION_STATUS_MANUAL_REVIEW,
            QUALIFICATION_STATUS_QUALIFIED,
            QUALIFICATION_STATUS_REJECTED,
        }
    ),
    QUALIFICATION_STATUS_QUALIFIED: frozenset(),
    QUALIFICATION_STATUS_REJECTED: frozenset(),
}

KEYWORD_STATUS_NEW = "new"
KEYWORD_STATUS_TESTING = "testing"
KEYWORD_STATUS_EFFECTIVE = "effective"
KEYWORD_STATUS_COOLING = "cooling"
KEYWORD_STATUS_LOW_YIELD = "low_yield"
KEYWORD_STATUS_DISABLED = "disabled"
KEYWORD_STATUSES = frozenset(
    {
        KEYWORD_STATUS_NEW,
        KEYWORD_STATUS_TESTING,
        KEYWORD_STATUS_EFFECTIVE,
        KEYWORD_STATUS_COOLING,
        KEYWORD_STATUS_LOW_YIELD,
        KEYWORD_STATUS_DISABLED,
    }
)

HUMAN_REVIEW_ACTION_TARGETS: Mapping[str, str] = {
    "approve": QUALIFICATION_STATUS_QUALIFIED,
    "reject": QUALIFICATION_STATUS_REJECTED,
    "request_enrichment": QUALIFICATION_STATUS_NEED_ENRICHMENT,
    "complete_enrichment": QUALIFICATION_STATUS_MANUAL_REVIEW,
}


def validate_job_user_status(status: str) -> str:
    if status not in JOB_USER_STATUSES:
        raise ValueError(f"Unknown pipeline job user status: {status}")
    return status


def validate_discovery_status(status: str) -> str:
    if status not in DISCOVERY_STATUSES:
        raise ValueError(f"Unknown discovery status: {status}")
    return status


def validate_qualification_status(status: str) -> str:
    if status not in QUALIFICATION_STATUSES:
        raise ValueError(f"Unknown qualification status: {status}")
    return status


def validate_keyword_status(status: str) -> str:
    if status not in KEYWORD_STATUSES:
        raise ValueError(f"Unknown acquisition keyword status: {status}")
    return status


def validate_qualification_transition(current: str, target: str) -> bool:
    if (
        current not in QUALIFICATION_STATUSES
        or target not in QUALIFICATION_STATUSES
        or target not in QUALIFICATION_STATUS_TRANSITIONS[current]
    ):
        raise ValueError(
            f"Invalid qualification transition: {current} -> {target}"
        )
    return True


def validate_human_review_action(action: str, target_status: str) -> bool:
    expected_target = HUMAN_REVIEW_ACTION_TARGETS.get(action)
    if expected_target is None or expected_target != target_status:
        raise ValueError(
            f"Invalid review action/target combination: "
            f"{action} -> {target_status}"
        )
    return True


def legacy_job_user_status(qualification_status: str) -> str:
    """Map the new review state to the legacy PipelineJobUser status."""
    validate_qualification_status(qualification_status)
    if qualification_status == QUALIFICATION_STATUS_QUALIFIED:
        return JOB_USER_STATUS_QUALIFIED
    if qualification_status == QUALIFICATION_STATUS_REJECTED:
        return JOB_USER_STATUS_REJECTED
    return JOB_USER_STATUS_PENDING

JOB_STATUS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    JOB_STATUS_QUEUED: frozenset(
        {JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED}
    ),
    JOB_STATUS_RUNNING: frozenset(
        {
            JOB_STATUS_WAITING_DECISION,
            JOB_STATUS_SUCCEEDED,
            JOB_STATUS_PARTIAL_FAILED,
            JOB_STATUS_FAILED,
            JOB_STATUS_CANCELLING,
            JOB_STATUS_INTERRUPTED,
        }
    ),
    JOB_STATUS_WAITING_DECISION: frozenset(
        {
            JOB_STATUS_RUNNING,
            JOB_STATUS_CANCELLED,
            JOB_STATUS_INTERRUPTED,
        }
    ),
    JOB_STATUS_CANCELLING: frozenset(
        {JOB_STATUS_CANCELLED, JOB_STATUS_INTERRUPTED}
    ),
    JOB_STATUS_SUCCEEDED: frozenset(),
    JOB_STATUS_PARTIAL_FAILED: frozenset(),
    JOB_STATUS_FAILED: frozenset(),
    JOB_STATUS_CANCELLED: frozenset(),
    JOB_STATUS_INTERRUPTED: frozenset(),
}

STAGE_STATUS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    STAGE_STATUS_PENDING: frozenset(
        {
            STAGE_STATUS_RUNNING,
            STAGE_STATUS_SKIPPED,
            STAGE_STATUS_CANCELLED,
        }
    ),
    STAGE_STATUS_RUNNING: frozenset(
        TERMINAL_STAGE_STATUSES | {STAGE_STATUS_WAITING_DECISION}
    ),
    STAGE_STATUS_WAITING_DECISION: frozenset(
        {
            STAGE_STATUS_RUNNING,
            STAGE_STATUS_FAILED,
            STAGE_STATUS_CANCELLED,
        }
    ),
    STAGE_STATUS_FAILED: frozenset({STAGE_STATUS_RUNNING}),
    STAGE_STATUS_SUCCEEDED: frozenset(),
    STAGE_STATUS_SKIPPED: frozenset(),
    STAGE_STATUS_CANCELLED: frozenset(),
}


def validate_job_status(status: str) -> str:
    if status not in JOB_STATUSES:
        raise ValueError(f"Unknown pipeline job status: {status}")
    return status


def validate_stage_status(status: str) -> str:
    if status not in STAGE_STATUSES:
        raise ValueError(f"Unknown pipeline stage status: {status}")
    return status


def validate_pipeline_stage(stage: str) -> str:
    if stage not in KNOWN_PIPELINE_STAGES:
        raise ValueError(f"Unknown pipeline stage: {stage}")
    return stage


def validate_job_transition(current: str, target: str) -> bool:
    validate_job_status(current)
    validate_job_status(target)
    if target not in JOB_STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Invalid pipeline job transition: {current} -> {target}"
        )
    return True


def validate_stage_transition(current: str, target: str) -> bool:
    validate_stage_status(current)
    validate_stage_status(target)
    if target not in STAGE_STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Invalid pipeline stage transition: {current} -> {target}"
        )
    return True
