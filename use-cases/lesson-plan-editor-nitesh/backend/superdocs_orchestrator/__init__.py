"""superdocs_orchestrator: Python orchestration client for the SuperDocs REST API."""

from __future__ import annotations

from .client import SuperDocsClient
from .exceptions import JobFailedError, SuperDocsError
from .models import (
    ChangeDecision,
    ExportFormat,
    JobSnapshot,
    PendingChange,
    UploadedDocument,
)
from .titles import DocumentTitle, assemble_title, parse_title

__all__ = [
    "ChangeDecision",
    "DocumentTitle",
    "ExportFormat",
    "JobFailedError",
    "JobSnapshot",
    "PendingChange",
    "SuperDocsClient",
    "SuperDocsError",
    "UploadedDocument",
    "assemble_title",
    "parse_title",
]
