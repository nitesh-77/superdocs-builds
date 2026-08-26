"""Tests for approval submission: approve_change / deny_change / submit_decisions."""

from __future__ import annotations

import json
from typing import Any

import pytest

from conftest import _make_client
from superdocs_orchestrator.exceptions import SuperDocsError
from superdocs_orchestrator.models import ChangeDecision


def test_approve_change_sends_documented_body() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.approve_change("sess-1", "job_9f8e7d6c", "ch_1")

    request = route.calls.last.request
    assert request.url.path == "/v1/chat/sess-1/approve"
    assert request.headers["Authorization"] == "Bearer sk_test_key"
    assert json.loads(request.content) == {
        "job_id": "job_9f8e7d6c",
        "change_id": "ch_1",
        "approved": True,
    }


def test_approve_change_with_feedback_includes_feedback() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.approve_change(
            "sess-1", "job_9f8e7d6c", "ch_1", feedback="Looks great"
        )

    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    assert body["feedback"] == "Looks great"
    assert body["approved"] is True


def test_deny_change_sends_approved_false() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.deny_change("sess-1", "job_9f8e7d6c", "ch_2")

    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    assert body == {"job_id": "job_9f8e7d6c", "change_id": "ch_2", "approved": False}
    assert "feedback" not in body


def test_deny_change_with_feedback() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.deny_change(
            "sess-1", "job_9f8e7d6c", "ch_2", feedback="Not aligned with objectives"
        )

    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    assert body == {
        "job_id": "job_9f8e7d6c",
        "change_id": "ch_2",
        "approved": False,
        "feedback": "Not aligned with objectives",
    }


def test_submit_decisions_mixed_sets_top_level_approved_false() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.submit_decisions(
            "sess-1",
            "job_9f8e7d6c",
            [
                ChangeDecision(change_id="ch_1", approved=True),
                ChangeDecision(
                    change_id="ch_2",
                    approved=False,
                    feedback="keep original wording",
                ),
            ],
        )

    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    assert body == {
        "job_id": "job_9f8e7d6c",
        "approved": False,
        "changes": [
            {"change_id": "ch_1", "approved": True},
            {"change_id": "ch_2", "approved": False, "feedback": "keep original wording"},
        ],
    }


def test_submit_decisions_all_approved_sets_top_level_true() -> None:
    client, router = _make_client()
    with router:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        client.submit_decisions(
            "sess-1",
            "job_9f8e7d6c",
            [
                ChangeDecision(change_id="ch_1", approved=True),
                ChangeDecision(change_id="ch_2", approved=True),
            ],
        )

    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    assert body["approved"] is True


def test_submit_decisions_rejects_empty_list() -> None:
    client, router = _make_client()
    # Explicit start/stop: `with router:` would fail on exit because the
    # registered route is intentionally never called.
    router.start()
    try:
        route = router.post("/v1/chat/sess-1/approve").respond(json={})

        with pytest.raises(SuperDocsError):
            client.submit_decisions("sess-1", "job_9f8e7d6c", [])
    finally:
        router.stop(quiet=True)

    assert len(route.calls) == 0


def test_approve_non_2xx_raises_superdocs_error() -> None:
    client, router = _make_client()
    with router:
        router.post("/v1/chat/sess-1/approve").respond(
            status_code=422, json={"detail": "missing approved"}
        )

        with pytest.raises(SuperDocsError):
            client.approve_change("sess-1", "job_9f8e7d6c", "ch_1")
