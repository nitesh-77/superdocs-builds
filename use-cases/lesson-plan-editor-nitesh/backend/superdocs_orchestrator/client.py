"""HTTP client for the SuperDocs REST API.

``SuperDocsClient`` wraps the subset of the SuperDocs API needed by the
lesson-plan editor orchestration flow:

* :meth:`SuperDocsClient.upload_document` — upload a document, optionally
  indexing it into a session.
* :meth:`SuperDocsClient.upload_template` — upload a reusable template.
* :meth:`SuperDocsClient.start_chat_job` — kick off an async chat job.
* :meth:`SuperDocsClient.get_job` / :meth:`SuperDocsClient.wait_for_terminal`
  — poll the job until it reaches a terminal state.
* :meth:`SuperDocsClient.approve_change` / :meth:`SuperDocsClient.deny_change`
  / :meth:`SuperDocsClient.submit_decisions` — submit human decisions on
  proposed changes so an ``awaiting_approval`` job resumes.
* :meth:`SuperDocsClient.export_document` — download a session document as
  a binary file (docx / pdf / html).

Human-in-the-loop (HITL) flow
-----------------------------
The async chat pipeline is *not* fire-and-forget: a job may park itself in
``awaiting_approval`` before reaching a terminal state. Callers should
inspect ``JobSnapshot.awaiting_kind`` to decide what kind of gate they are
looking at:

* ``"continue_prompt"`` — the model paused mid-run and needs an explicit
  go-ahead before continuing.
* ``"change_review"`` — the model proposed document edits; the pending
  diffs are exposed as ``JobSnapshot.pending_changes`` and must be
  approved/rejected per change.

``awaiting_approval`` is deliberately **not** treated as terminal by
:meth:`SuperDocsClient.wait_for_terminal`; approval submission via
:meth:`SuperDocsClient.approve_change` / :meth:`SuperDocsClient.deny_change`
resumes the job afterwards.

Transport injection
-------------------
The constructor accepts an optional ``httpx.Client``. When provided it is
used exactly as-is (base URL, transport, timeouts, event hooks all come
from the caller), which lets tests point the client at
``httpx.MockTransport`` or a respx-mocked alternate host without any
network access. When omitted, a plain client is created internally.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from .exceptions import JobFailedError, SuperDocsError
from .models import (
    ChangeDecision,
    ExportFormat,
    JobSnapshot,
    PendingChange,
    UploadedDocument,
    parse_change_operation,
    parse_export_format,
    parse_job_status,
)

DEFAULT_BASE_URL = "https://api.superdocs.app"
API_KEY_ENV_VAR = "SUPERDOCS_API_KEY"

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class SuperDocsClient:
    """Thin, typed wrapper over the SuperDocs REST API.

    Args:
        api_key: Bearer token for the ``Authorization`` header. Falls back
            to the ``SUPERDOCS_API_KEY`` environment variable; a
            :exc:`ValueError` is raised when neither is available.
        base_url: Root URL of the SuperDocs API (no trailing slash).
        http_client: Optional caller-constructed :class:`httpx.Client`,
            used as-is when given. Intended for tests that inject a mock
            transport; the client never closes an injected client.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get(
            API_KEY_ENV_VAR
        )
        if not resolved_key:
            raise ValueError(
                "An API key is required: pass api_key or set the "
                f"{API_KEY_ENV_VAR} environment variable."
            )
        self.api_key = resolved_key
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        if http_client is not None:
            # Injected transport is used as-is: its base_url wins over the
            # constructor's base_url so callers fully control routing.
            self._client = http_client
            if http_client.base_url:
                self.base_url = str(http_client.base_url).rstrip("/")
        else:
            self._client = httpx.Client(base_url=self.base_url)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _request(self, request: httpx.Request) -> httpx.Response:
        request.headers["Authorization"] = f"Bearer {self.api_key}"
        response = self._client.send(request)
        if response.status_code >= 400:
            raise SuperDocsError(
                f"SuperDocs request failed: {request.method} {request.url} "
                f"-> HTTP {response.status_code}"
            )
        return response

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        files: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Request:
        return self._client.build_request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
            files=files,
            data=data,
        )

    def _parse_json_object(self, response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise SuperDocsError("Expected a JSON object from SuperDocs API")
        return payload

    @staticmethod
    def _extract_pending_changes(
        metadata: Mapping[str, Any],
    ) -> tuple[PendingChange, ...]:
        """Pull pending changes out of job ``metadata``.

        Tolerates three payload shapes seen in the wild:

        * ``metadata["pending_changes"]`` as a plain array (the normal case).
        * ``metadata["pending_changes"]`` as a JSON-encoded string — the
          SuperDocs SSE guide shows change content arriving double-encoded,
          so it is ``json.loads``-ed once more before iterating.
        * ``metadata["proposed_change_batch"]`` fallback (used only when
          ``pending_changes`` is absent/``None``): either a dict whose
          ``"content"`` value is a JSON string containing
          ``{"changes": [...]}``, or itself a JSON string of that shape.
        """
        raw_changes: Any = metadata.get("pending_changes")
        if raw_changes is None:
            batch: Any = metadata.get("proposed_change_batch")
            if isinstance(batch, str):
                try:
                    batch = json.loads(batch)
                except json.JSONDecodeError as e:
                    raise SuperDocsError(
                        f"Malformed encoded pending changes from SuperDocs API: {e}"
                    ) from e
            if isinstance(batch, Mapping):
                content = batch.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError as e:
                        raise SuperDocsError(
                            f"Malformed encoded pending changes from SuperDocs API: {e}"
                        ) from e
                if isinstance(content, Mapping):
                    raw_changes = content.get("changes")
                elif "changes" in batch:
                    raw_changes = batch["changes"]
        if isinstance(raw_changes, str):
            try:
                raw_changes = json.loads(raw_changes)
            except json.JSONDecodeError as e:
                raise SuperDocsError(
                    f"Malformed encoded pending changes from SuperDocs API: {e}"
                ) from e
        return tuple(
            PendingChange(
                change_id=change["change_id"],
                operation=parse_change_operation(change["operation"]),
                chunk_id=change.get("chunk_id"),
                old_html=change.get("old_html"),
                new_html=change.get("new_html"),
                ai_explanation=change.get("ai_explanation"),
            )
            for change in (raw_changes or ())
        )

    # ------------------------------------------------------------------
    # documents & templates
    # ------------------------------------------------------------------

    def upload_document(
        self,
        file_path: Path,
        *,
        session_id: str | None = None,
        index: bool = False,
    ) -> UploadedDocument:
        """Upload a document file, optionally indexing it into a session.

        Args:
            file_path: Path of the file to upload.
            session_id: Optional session to attach the upload to.
            index: Whether SuperDocs should index the content (sent as the
                ``index`` query parameter, ``"true"``/``"false"``).

        Returns:
            The parsed upload response as an :class:`UploadedDocument`.
        """
        with file_path.open("rb") as fh:
            request = self._build_request(
                "POST",
                "/v1/documents/upload",
                params={"index": "true" if index else "false"},
                files={"file": fh},
                data={"session_id": session_id} if session_id is not None else None,
            )
            payload = self._parse_json_object(self._request(request))
        return UploadedDocument(
            html=payload["html"],
            session_id=payload.get("session_id"),
            filename=payload.get("filename"),
            chunks_count=int(payload["chunks_count"]),
            version_id=payload.get("version_id"),
        )

    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_preview: bool = False,
        archived: bool = False,
    ) -> dict[str, Any]:
        """List documents from the SuperDocs Files list.

        Args:
            limit: Maximum number of documents to return.
            offset: Pagination offset.
            include_preview: Whether preview URLs are included.
            archived: Whether archived documents are returned instead of
                active ones.

        Returns:
            The parsed JSON object as-is (e.g. ``{"documents": [...],
            "total": n}``).
        """
        request = self._build_request(
            "GET",
            "/v1/documents",
            params={
                "limit": str(limit),
                "offset": str(offset),
                "include_preview": "true" if include_preview else "false",
                "archived": "true" if archived else "false",
            },
        )
        return self._parse_json_object(self._request(request))

    def upload_template(self, file_path: Path) -> dict[str, Any]:
        """Upload a template file; returns the parsed JSON object as-is."""
        with file_path.open("rb") as fh:
            request = self._build_request(
                "POST",
                "/v1/templates/upload",
                files={"file": fh},
            )
            return self._parse_json_object(self._request(request))

    # ------------------------------------------------------------------
    # async chat jobs
    # ------------------------------------------------------------------

    def start_chat_job(
        self,
        session_id: str,
        instruction: str,
        *,
        document_html: str | None = None,
    ) -> str:
        """Start an async chat job and return its ``job_id``.

        The ``instruction`` is sent verbatim in the ``message`` field — it
        is never rewritten or gated by this client. ``approval_mode`` is
        pinned to ``"ask_every_time"`` so every proposed change routes
        through human review.
        """
        body: dict[str, Any] = {
            "message": instruction,
            "session_id": session_id,
            "approval_mode": "ask_every_time",
        }
        if document_html is not None:
            body["document_html"] = document_html
        request = self._build_request("POST", "/v1/chat/async", json_body=body)
        payload = self._parse_json_object(self._request(request))
        return str(payload["job_id"])

    def get_job(self, job_id: str, *, compact: bool = True) -> JobSnapshot:
        """Fetch one job snapshot.

        Never raises on ``failed`` status — callers inspect
        :attr:`JobSnapshot.status` / :attr:`JobSnapshot.error` themselves;
        only :meth:`wait_for_terminal` converts failure into an exception.
        """
        request = self._build_request(
            "GET",
            f"/v1/jobs/{job_id}",
            params={"compact": "true" if compact else "false"},
        )
        payload = self._parse_json_object(self._request(request))
        metadata = payload.get("metadata") or {}
        pending_changes = self._extract_pending_changes(metadata)
        awaiting_kind = metadata.get("awaiting_kind")
        return JobSnapshot(
            job_id=str(payload["job_id"]),
            session_id=str(payload["session_id"]),
            status=parse_job_status(str(payload["status"])),
            progress=int(payload.get("progress") or 0),
            pending_changes=pending_changes,
            awaiting_kind=awaiting_kind if isinstance(awaiting_kind, str) else None,
            error=payload.get("error"),
            raw=payload,
        )

    def wait_for_terminal(
        self,
        job_id: str,
        *,
        poll_interval: float = 2.0,
        timeout: float = 3600.0,
    ) -> JobSnapshot:
        """Poll :meth:`get_job` until the job reaches a terminal status.

        Terminal statuses are ``completed``, ``failed``, and ``cancelled``.
        ``awaiting_approval`` is *not* terminal: the loop continues past it
        (submit approvals via :meth:`approve_change` / :meth:`deny_change` /
        :meth:`submit_decisions`).

        Raises:
            TimeoutError: If ``timeout`` seconds elapse first.
            JobFailedError: If the job ends in ``failed`` status; the
                message is the job's own error text.
        """
        deadline = time.monotonic() + timeout
        while True:
            snapshot = self.get_job(job_id)
            if snapshot.status in _TERMINAL_STATUSES:
                if snapshot.status == "failed":
                    raise JobFailedError(snapshot.error or "job failed")
                return snapshot
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Job {job_id!r} did not reach a terminal state within "
                    f"{timeout} seconds (last status: {snapshot.status})"
                )
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # approvals (HITL change review)
    # ------------------------------------------------------------------

    def _submit_approval(self, session_id: str, body: dict[str, Any]) -> None:
        """POST an ``ApprovalRequest`` body to the session approve endpoint."""
        request = self._build_request(
            "POST",
            f"/v1/chat/{session_id}/approve",
            json_body=body,
        )
        self._parse_json_object(self._request(request))

    def _submit_single_decision(
        self,
        session_id: str,
        job_id: str,
        change_id: str,
        *,
        approved: bool,
        feedback: str | None,
    ) -> None:
        """Build and POST a single-change ``ApprovalRequest`` body."""
        body: dict[str, Any] = {
            "job_id": job_id,
            "change_id": change_id,
            "approved": approved,
        }
        if feedback is not None:
            body["feedback"] = feedback
        self._submit_approval(session_id, body)

    def approve_change(
        self,
        session_id: str,
        job_id: str,
        change_id: str,
        *,
        feedback: str | None = None,
    ) -> None:
        """Approve a single proposed change.

        The top-level ``approved: true`` is required by the API even in
        single-change shape; omitting it yields HTTP 422.
        """
        self._submit_single_decision(
            session_id, job_id, change_id, approved=True, feedback=feedback
        )

    def deny_change(
        self,
        session_id: str,
        job_id: str,
        change_id: str,
        *,
        feedback: str | None = None,
    ) -> None:
        """Deny a single proposed change, optionally with feedback."""
        self._submit_single_decision(
            session_id, job_id, change_id, approved=False, feedback=feedback
        )

    def submit_decisions(
        self,
        session_id: str,
        job_id: str,
        decisions: Sequence[ChangeDecision],
    ) -> None:
        """Submit a batch of per-change decisions in one request.

        The top-level ``approved`` flag acts as the default for entries
        lacking their own ``approved``; it is set to ``True`` only when
        *every* decision approves.

        Raises:
            SuperDocsError: If ``decisions`` is empty.
        """
        if not decisions:
            raise SuperDocsError(
                "submit_decisions requires at least one ChangeDecision"
            )
        changes = []
        for decision in decisions:
            entry: dict[str, Any] = {
                "change_id": decision.change_id,
                "approved": decision.approved,
            }
            if decision.feedback is not None:
                entry["feedback"] = decision.feedback
            changes.append(entry)
        self._submit_approval(
            session_id,
            {
                "job_id": job_id,
                "approved": all(d.approved for d in decisions),
                "changes": changes,
            },
        )

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    def export_document(
        self,
        session_id: str,
        output_format: ExportFormat,
        output_path: Path,
    ) -> Path:
        """Export a session document and write the binary download to disk.

        Args:
            session_id: Session whose document should be exported.
            output_format: One of ``"docx"``, ``"pdf"``, ``"html"``.
            output_path: File the response bytes are written to. Parent
                directories are not created.

        Returns:
            The ``output_path`` that was written.
        """
        parse_export_format(output_format)
        request = self._build_request(
            "POST",
            "/v1/documents/export",
            json_body={"session_id": session_id, "format": output_format},
        )
        response = self._request(request)
        with open(output_path, "wb") as fh:
            fh.write(response.content)
        return output_path


__all__ = ["SuperDocsClient", "PendingChange", "UploadedDocument"]
