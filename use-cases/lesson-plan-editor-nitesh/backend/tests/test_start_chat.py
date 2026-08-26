"""Tests for SuperDocsClient.start_chat_job."""

from __future__ import annotations

import json
from typing import Any

import json
from typing import Any

from conftest import _make_client

# Odd punctuation proves the instruction is passed through VERBATIM.
WEIRD_INSTRUCTION = (
    "Rewrite section 3!!! ... wait — add GDPR stuff?? (keep tone: playful)"
)


def test_start_chat_job_returns_job_id(fixture_loader: Any) -> None:
    payload: dict[str, Any] = fixture_loader("async_chat_response.json")
    client, router = _make_client()
    with router:
        router.post("/v1/chat/async").respond(json=payload)

        job_id = client.start_chat_job(
            session_id="lesson-grade8-science-photosynthesis",
            instruction="Add an assessment section",
        )

    assert job_id == "job_9f8e7d6c"


def test_request_body_has_verbatim_message_and_approval_mode() -> None:
    payload: dict[str, Any] = {"job_id": "job_x", "status": "pending"}
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/async").respond(json=payload)

        client.start_chat_job(
            session_id="sess-1",
            instruction=WEIRD_INSTRUCTION,
        )

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk_test_key"
    assert json.loads(request.content) == {
        "message": WEIRD_INSTRUCTION,
        "session_id": "sess-1",
        "approval_mode": "ask_every_time",
    }


def test_document_html_absent_when_none_present_when_given() -> None:
    payload: dict[str, Any] = {"job_id": "job_x", "status": "pending"}
    client, router = _make_client()

    with router:
        route_without = router.post("/v1/chat/async").respond(json=payload)
        client.start_chat_job(session_id="sess-1", instruction="go")

        body_without = json.loads(route_without.calls.last.request.content)
        assert "document_html" not in body_without

        route_with = router.post("/v1/chat/async").respond(json=payload)
        client.start_chat_job(
            session_id="sess-1",
            instruction="go",
            document_html="<div data-chunk-id='abc123'><h1>T</h1></div>",
        )

        body_with = json.loads(route_with.calls.last.request.content)
        assert body_with["document_html"] == (
            "<div data-chunk-id='abc123'><h1>T</h1></div>"
        )
