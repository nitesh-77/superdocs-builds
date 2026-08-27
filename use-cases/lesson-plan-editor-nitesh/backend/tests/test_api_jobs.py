"""Tests for the FastAPI wrapper: instructions, jobs, decisions, export."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from conftest import load_fixture
from superdocs_api.app import create_app


# ----------------------------------------------------------------------
# POST /sessions/{session_id}/instructions
# ----------------------------------------------------------------------


def test_post_instructions_returns_202_pending(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    payload: dict[str, Any] = load_fixture("async_chat_response.json")
    route = router.post("/v1/chat/async").respond(json=payload)

    response = test_client.post(
        "/sessions/sess-1/instructions",
        json={"instruction": "Add a GDPR compliance section"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "job_9f8e7d6c",
        "session_id": "sess-1",
        "status": "pending",
    }
    upstream = json.loads(route.calls.last.request.content)
    assert upstream["message"] == "Add a GDPR compliance section"
    assert upstream["session_id"] == "sess-1"


def test_post_instructions_empty_body_rejected(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, _router = api

    response = test_client.post("/sessions/sess-1/instructions", json={"instruction": ""})

    assert response.status_code == 422


def test_post_instructions_maps_superdocs_error_to_502(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.post("/v1/chat/async").respond(status_code=500, json={"detail": "boom"})

    response = test_client.post(
        "/sessions/sess-1/instructions", json={"instruction": "do things"}
    )

    assert response.status_code == 502
    assert "sk_test_key" not in response.text


# ----------------------------------------------------------------------
# GET /jobs/{job_id}
# ----------------------------------------------------------------------


def test_get_job_serializes_snapshot_without_raw(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    payload: dict[str, Any] = load_fixture("job_awaiting_approval.json")
    route = router.get("/v1/jobs/job_9f8e7d6c").respond(json=payload)

    response = test_client.get("/jobs/job_9f8e7d6c")

    assert response.status_code == 200
    body = response.json()
    assert "raw" not in body
    assert body["job_id"] == "job_9f8e7d6c"
    assert body["session_id"] == "lesson-grade8-science-photosynthesis"
    assert body["status"] == "awaiting_approval"
    assert body["progress"] == 80
    assert body["awaiting_kind"] == "change_review"
    assert body["error"] is None
    assert body["pending_changes"] == [
        {
            "change_id": "ch_1",
            "operation": "edit",
            "chunk_id": "550e8400-e29b-41d4-a716-446655440000",
            "old_html": "<p>Original section 3 content...</p>",
            "new_html": "<p>Updated content with GDPR compliance...</p>",
            "ai_explanation": "Added GDPR data processing requirements",
        },
        {
            "change_id": "ch_2",
            "operation": "create",
            "chunk_id": None,
            "old_html": None,
            "new_html": "<h2>Assessment</h2><p>Exit slip...</p>",
            "ai_explanation": "Added missing canonical Assessment section",
        },
    ]
    request = route.calls.last.request
    assert request.url.params["compact"] == "true"


def test_get_job_maps_superdocs_error_to_502(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.get("/v1/jobs/job_x").respond(status_code=404, json={"detail": "gone"})

    response = test_client.get("/jobs/job_x")

    assert response.status_code == 502
    assert "sk_test_key" not in response.text


# ----------------------------------------------------------------------
# POST /sessions/{session_id}/decisions
# ----------------------------------------------------------------------


def test_post_decisions_submits_batch_and_counts(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    route = router.post("/v1/chat/sess-1/approve").respond(json={"ok": True})

    response = test_client.post(
        "/sessions/sess-1/decisions",
        json={
            "job_id": "job_9f8e7d6c",
            "decisions": [
                {"change_id": "ch_1", "approved": True},
                {"change_id": "ch_2", "approved": False, "feedback": "too long"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"approved": 1, "denied": 1}
    upstream = json.loads(route.calls.last.request.content)
    assert upstream["job_id"] == "job_9f8e7d6c"
    assert upstream["approved"] is False  # mixed batch → default flag is False
    assert upstream["changes"][0] == {"change_id": "ch_1", "approved": True}
    assert upstream["changes"][1] == {
        "change_id": "ch_2",
        "approved": False,
        "feedback": "too long",
    }


def test_post_decisions_single_change_is_one_element_list(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    route = router.post("/v1/chat/sess-1/approve").respond(json={"ok": True})

    response = test_client.post(
        "/sessions/sess-1/decisions",
        json={
            "job_id": "job_9f8e7d6c",
            "decisions": [{"change_id": "ch_1", "approved": True}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"approved": 1, "denied": 0}
    upstream = json.loads(route.calls.last.request.content)
    assert upstream["changes"] == [{"change_id": "ch_1", "approved": True}]


def test_post_decisions_empty_list_rejected(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, _router = api

    response = test_client.post(
        "/sessions/sess-1/decisions",
        json={"job_id": "job_9f8e7d6c", "decisions": []},
    )

    assert response.status_code == 422


def test_post_decisions_maps_superdocs_error_to_502(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.post("/v1/chat/sess-1/approve").respond(status_code=409, json={"detail": "x"})

    response = test_client.post(
        "/sessions/sess-1/decisions",
        json={"job_id": "job_9f8e7d6c", "decisions": [{"change_id": "ch_1", "approved": True}]},
    )

    assert response.status_code == 502
    assert "sk_test_key" not in response.text


# ----------------------------------------------------------------------
# GET /sessions/{session_id}/export
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "media_type"),
    [
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", "application/pdf"),
        ("html", "text/html"),
    ],
)
def test_export_streams_file_with_proper_headers(
    api: tuple[TestClient, respx.MockRouter], fmt: str, media_type: str
) -> None:
    test_client, router = api
    route = router.post("/v1/documents/export").respond(content=b"BINARY-BYTES")

    response = test_client.get("/sessions/sess-1/export", params={"format": fmt})

    assert response.status_code == 200
    assert response.content == b"BINARY-BYTES"
    # Starlette appends a charset to text/* media types.
    assert response.headers["content-type"].split(";")[0].strip() == media_type
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f'filename="sess-1-edited.{fmt}"' in disposition
    upstream = json.loads(route.calls.last.request.content)
    assert upstream == {"session_id": "sess-1", "format": fmt}


def test_export_invalid_format_rejected(api: tuple[TestClient, respx.MockRouter]) -> None:
    test_client, _router = api

    response = test_client.get("/sessions/sess-1/export", params={"format": "xlsx"})

    assert response.status_code == 422


def test_export_maps_superdocs_error_to_502(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.post("/v1/documents/export").respond(status_code=500, json={"detail": "x"})

    response = test_client.get("/sessions/sess-1/export", params={"format": "pdf"})

    assert response.status_code == 502
    assert "sk_test_key" not in response.text
