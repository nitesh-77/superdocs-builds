"""Typed models returned by :mod:`superdocs_orchestrator`.

All models are frozen dataclasses: safe to hand across threads and to
compare in tests. ``JobSnapshot.raw`` keeps the full parsed API response
so callers can read fields this package does not model yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from .exceptions import SuperDocsError

JobStatus = Literal[
    "pending",
    "in_progress",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
]
"""Lifecycle states an async job can report."""

ChangeOperation = Literal["edit", "create", "delete"]
"""Operations a pending change can apply to a chunk."""

_JOB_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "in_progress",
        "awaiting_approval",
        "completed",
        "failed",
        "cancelled",
    }
)
_CHANGE_OPERATIONS: frozenset[str] = frozenset({"edit", "create", "delete"})

ExportFormat = Literal["docx", "pdf", "html"]
"""Document formats supported by :meth:`SuperDocsClient.export_document`."""

_EXPORT_FORMATS: frozenset[str] = frozenset({"docx", "pdf", "html"})


def parse_job_status(value: str) -> JobStatus:
    """Validate a raw status string, returning the typed literal.

    Raises:
        SuperDocsError: If ``value`` is not a known job status.
    """
    if value in _JOB_STATUSES:
        return cast(JobStatus, value)
    raise SuperDocsError(f"Unknown job status from SuperDocs API: {value!r}")


def parse_change_operation(value: str) -> ChangeOperation:
    """Validate a raw operation string, returning the typed literal.

    Raises:
        SuperDocsError: If ``value`` is not a known change operation.
    """
    if value in _CHANGE_OPERATIONS:
        return cast(ChangeOperation, value)
    raise SuperDocsError(
        f"Unknown change operation from SuperDocs API: {value!r}"
    )


def parse_export_format(value: str) -> ExportFormat:
    """Validate a raw export format string, returning the typed literal.

    Raises:
        SuperDocsError: If ``value`` is not a supported export format.
    """
    if value in _EXPORT_FORMATS:
        return cast(ExportFormat, value)
    raise SuperDocsError(
        f"Unknown export format: {value!r}. Expected one of: docx, pdf, html"
    )


@dataclass(frozen=True)
class UploadedDocument:
    """Result of uploading a document to SuperDocs.

    Attributes:
        html: Chunked HTML representation of the uploaded document.
        session_id: Session the document was indexed into, if any.
        filename: Name SuperDocs recorded for the upload, if any.
        chunks_count: Number of chunks the document was split into.
        version_id: Version identifier for this revision, if any.
    """

    html: str
    session_id: str | None
    filename: str | None
    chunks_count: int
    version_id: str | None


@dataclass(frozen=True)
class PendingChange:
    """A single change awaiting human approval during a change-review round.

    Unknown keys from the API payload (e.g. ``insert_after_chunk_id``) are
    intentionally ignored; they remain accessible via ``JobSnapshot.raw``.

    Attributes:
        change_id: Server-side identifier of the proposed change.
        operation: One of ``"edit"``, ``"create"``, or ``"delete"``.
        chunk_id: Target chunk, or ``None`` for pure insertions.
        old_html: Previous HTML, if any.
        new_html: Proposed HTML, if any.
        ai_explanation: Model-supplied rationale shown to the reviewer.
    """

    change_id: str
    operation: ChangeOperation
    chunk_id: str | None
    old_html: str | None
    new_html: str | None
    ai_explanation: str | None


@dataclass(frozen=True)
class ChangeDecision:
    """One per-change decision inside a batch approval submission."""

    change_id: str
    approved: bool
    feedback: str | None = None


@dataclass(frozen=True)
class JobSnapshot:
    """Point-in-time view of an async job.

    Attributes:
        job_id: Identifier of the polled job.
        session_id: Session the job belongs to.
        status: One of pending / in_progress / awaiting_approval /
            completed / failed / cancelled.
        progress: Percentage complete (0-100).
        pending_changes: Changes awaiting review; empty unless the job is
            in an ``awaiting_approval`` change-review round.
        awaiting_kind: From ``metadata.awaiting_kind`` — distinguishes a
            ``continue_prompt`` gate from a change-review gate.
        error: Error text when ``status == "failed"``, else ``None``.
        raw: The full parsed response, unmodified.
    """

    job_id: str
    session_id: str
    status: JobStatus
    progress: int
    pending_changes: tuple[PendingChange, ...]
    awaiting_kind: str | None
    error: str | None
    raw: Mapping[str, Any]
