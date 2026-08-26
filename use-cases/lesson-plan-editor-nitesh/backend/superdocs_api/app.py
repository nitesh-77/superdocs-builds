"""Thin FastAPI wrapper over :mod:`superdocs_orchestrator`.

Design rules
------------
* The API layer contains **zero orchestration logic**: it only wires the
  sync :class:`~superdocs_orchestrator.client.SuperDocsClient` to HTTP —
  plus the two thin concerns this app owns (encoding Class/Subject/
  Chapter into uploaded filenames, and grouping listed documents by
  parsed titles).
* All endpoints are plain ``def`` (not ``async def``): the orchestrator
  client is blocking, so FastAPI runs each request in its threadpool.
* The SuperDocs API key never appears in any response body, log line, or
  error message. Upstream failures are mapped to HTTP 502 with only the
  client's safe message text.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from superdocs_orchestrator.client import SuperDocsClient
from superdocs_orchestrator.exceptions import SuperDocsError
from superdocs_orchestrator.models import ChangeDecision, ExportFormat
from superdocs_orchestrator.titles import assemble_title, parse_title

from .schemas import DecisionsRequest, InstructionRequest

# Extensions SuperDocs may carry over from an uploaded filename into the
# stored document title. Stripped before parsing so the chapter field is
# never polluted (e.g. "Ch. 7: Photosynthesis.md" -> "Ch. 7: Photosynthesis").
_TITLE_EXTENSIONS = frozenset({".md", ".html", ".htm", ".docx", ".pdf", ".txt"})


def _strip_title_extension(title: str) -> str:
    """Drop a trailing known file extension from a stored document title."""
    lowered = title.lower()
    for ext in _TITLE_EXTENSIONS:
        if lowered.endswith(ext):
            return title[: -len(ext)]
    return title


EXPORT_MEDIA_TYPES: dict[str, str] = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "pdf": "application/pdf",
    "html": "text/html",
}


def get_client(request: Request) -> SuperDocsClient:
    """FastAPI dependency returning the client stored on ``app.state``."""
    return request.app.state.client


def create_app(client: SuperDocsClient | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        client: Pre-built :class:`SuperDocsClient` (tests inject one backed
            by a mock transport). When omitted, a client is constructed
            from the ``SUPERDOCS_API_KEY`` environment variable; a missing
            key raises :exc:`ValueError` at startup.

    Returns:
        The configured :class:`fastapi.FastAPI` application.
    """
    app = FastAPI(title="SuperDocs Lesson Plan API")
    app.state.client = client if client is not None else SuperDocsClient()

    @app.exception_handler(SuperDocsError)
    async def handle_superdocs_error(
        request: Request, exc: SuperDocsError
    ) -> JSONResponse:
        """Map upstream client failures to 502 without leaking key material."""
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    # ------------------------------------------------------------------
    # lessons: upload & grouped listing
    # ------------------------------------------------------------------

    @app.post("/lessons", status_code=201)
    def create_lesson(
        client: Annotated[SuperDocsClient, Depends(get_client)],
        class_name: Annotated[str, Form()],
        subject: Annotated[str, Form()],
        chapter: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        """Upload a lesson document and index it into a new session.

        The title is *always* assembled from class/subject/chapter — there
        is no title field — and encoded into the uploaded filename so the
        SuperDocs Files list carries it.
        """
        try:
            title = assemble_title(class_name, subject, chapter)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        suffix = Path(file.filename or "").suffix or ".html"
        tmp_dir = tempfile.mkdtemp()
        try:
            path = Path(tmp_dir) / f"{title}{suffix}"
            with open(path, "wb") as fh:
                shutil.copyfileobj(file.file, fh)
            uploaded = client.upload_document(path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "session_id": uploaded.session_id,
            "filename": uploaded.filename,
            "chunks_count": uploaded.chunks_count,
            "version_id": uploaded.version_id,
        }

    @app.get("/lessons")
    def list_lessons(
        client: Annotated[SuperDocsClient, Depends(get_client)],
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List documents grouped by Class › Subject › Chapter.

        Documents whose title does not parse as ``Class :: Subject ::
        Chapter`` (foreign uploads) are excluded from the grouping.
        """
        payload = client.list_documents(limit=limit, offset=offset)
        documents = payload.get("documents")
        if not isinstance(documents, list):
            documents = []
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for item in documents:
            if not isinstance(item, dict):
                continue
            title = _strip_title_extension(str(item.get("title", "")))
            parsed = parse_title(title)
            if parsed is None:
                continue
            lesson: dict[str, Any] = {
                "chapter": parsed.chapter,
                "title": title,
                "document_id": item.get("document_id"),
                "session_count": int(item.get("session_count") or 0),
            }
            if parsed.class_name not in grouped:
                grouped[parsed.class_name] = {}
            subjects = grouped[parsed.class_name]
            if parsed.subject not in subjects:
                subjects[parsed.subject] = []
            subjects[parsed.subject].append(lesson)
        groups: list[dict[str, Any]] = []
        for class_name in sorted(grouped):
            subjects_out: list[dict[str, Any]] = []
            for subject in sorted(grouped[class_name]):
                lessons = grouped[class_name][subject]
                lessons_sorted = sorted(
                    lessons, key=lambda l: str(l["chapter"])
                )
                subjects_out.append(
                    {"subject": subject, "lessons": lessons_sorted}
                )
            groups.append({"class_name": class_name, "subjects": subjects_out})
        return {"groups": groups}

    # ------------------------------------------------------------------
    # async chat jobs & HITL decisions
    # ------------------------------------------------------------------

    @app.post("/sessions/{session_id}/instructions", status_code=202)
    def start_instruction(
        session_id: str,
        body: InstructionRequest,
        client: Annotated[SuperDocsClient, Depends(get_client)],
    ) -> dict[str, str]:
        """Start an async chat job for a session; returns immediately."""
        job_id = client.start_chat_job(session_id, body.instruction)
        return {"job_id": job_id, "session_id": session_id, "status": "pending"}

    @app.get("/jobs/{job_id}")
    def get_job_status(
        job_id: str,
        client: Annotated[SuperDocsClient, Depends(get_client)],
    ) -> dict[str, Any]:
        """Fetch one job snapshot (full pending changes when gated)."""
        snapshot = client.get_job(job_id)
        return {
            "job_id": snapshot.job_id,
            "session_id": snapshot.session_id,
            "status": snapshot.status,
            "progress": snapshot.progress,
            "awaiting_kind": snapshot.awaiting_kind,
            "error": snapshot.error,
            "pending_changes": [
                {
                    "change_id": change.change_id,
                    "operation": change.operation,
                    "chunk_id": change.chunk_id,
                    "old_html": change.old_html,
                    "new_html": change.new_html,
                    "ai_explanation": change.ai_explanation,
                }
                for change in snapshot.pending_changes
            ],
        }

    @app.post("/sessions/{session_id}/decisions")
    def submit_decisions(
        session_id: str,
        body: DecisionsRequest,
        client: Annotated[SuperDocsClient, Depends(get_client)],
    ) -> dict[str, int]:
        """Submit per-change approve/deny decisions for an awaiting job.

        A single approve/deny is this endpoint with a one-element
        ``decisions`` list.
        """
        decisions = [
            ChangeDecision(
                change_id=item.change_id,
                approved=item.approved,
                feedback=item.feedback,
            )
            for item in body.decisions
        ]
        client.submit_decisions(session_id, body.job_id, decisions)
        approved = sum(1 for item in body.decisions if item.approved)
        return {"approved": approved, "denied": len(body.decisions) - approved}

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    @app.get("/sessions/{session_id}/export")
    def export_session(
        session_id: str,
        format: ExportFormat,
        client: Annotated[SuperDocsClient, Depends(get_client)],
    ) -> FileResponse:
        """Export a session document as docx / pdf / html.

        The download is written to a temp file and streamed via
        ``FileResponse``; the file is unlinked in a background task after
        the response has been sent.
        """
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=f".{format}"
        ) as fh:
            output_path = Path(fh.name)
        try:
            client.export_document(session_id, format, output_path)
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        return FileResponse(
            output_path,
            media_type=EXPORT_MEDIA_TYPES[format],
            filename=f"{session_id}-edited.{format}",
            background=BackgroundTask(os.unlink, output_path),
        )

    return app


__all__ = ["create_app", "get_client"]
