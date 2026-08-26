"""Exception hierarchy for the SuperDocs orchestrator."""

from __future__ import annotations


class SuperDocsError(Exception):
    """Base error for all SuperDocs client failures.

    Raised for non-2xx HTTP responses and malformed payloads. Never leaks
    the API key or other secrets in its message.
    """


class JobFailedError(SuperDocsError):
    """Raised by :meth:`SuperDocsClient.wait_for_terminal` when a job ends in ``failed`` status.

    The exception message is the job's own error text (e.g. ``"Model timeout"``).
    """
