"""Tests for SuperDocsClient.get_job and wait_for_terminal."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from conftest import _make_client
from superdocs_orchestrator.exceptions import JobFailedError
from superdocs_orchestrator.models import PendingChange

JOB_FIXTURES = [
    ("job_pending.json", "pending"),
    ("job_in_progress.json", "in_progress"),
    ("job_awaiting_approval.json", "awaiting_approval"),
    ("job_completed.json", "completed"),
    ("job_failed.json", "failed"),
]


# ---------------------------------------------------------------- get_job


@pytest.mark.parametrize(
    ("fixture_name", "expected_status"),
    [
        *JOB_FIXTURES,
        ("job_cancelled.json", "cancelled"),
    ],
)
def test_get_job_maps_status(
    fixture_loader: Any, fixture_name: str, expected_status: str
) -> None:
    payload: dict[str, Any] = fixture_loader(fixture_name)
    client, router = _make_client()
    with router:
        route = router.get(f"/v1/jobs/{payload['job_id']}").respond(json=payload)

        snapshot = client.get_job(payload["job_id"])

    assert snapshot.status == expected_status
    request = route.calls.last.request
    assert request.url.params["compact"] == "true"


def test_get_job_awaiting_approval_parses_pending_changes(
    fixture_loader: Any,
) -> None:
    payload: dict[str, Any] = fixture_loader("job_awaiting_approval.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").respond(json=payload)

        snapshot = client.get_job("job_9f8e7d6c")

    changes = snapshot.pending_changes
    assert isinstance(changes, tuple)
    assert len(changes) == 2
    first, second = changes
    assert isinstance(first, PendingChange)
    assert first == PendingChange(
        change_id="ch_1",
        operation="edit",
        chunk_id="550e8400-e29b-41d4-a716-446655440000",
        old_html="<p>Original section 3 content...</p>",
        new_html="<p>Updated content with GDPR compliance...</p>",
        ai_explanation="Added GDPR data processing requirements",
    )
    # insert_after_chunk_id rides via additionalProperties — ignored, not a field.
    assert second == PendingChange(
        change_id="ch_2",
        operation="create",
        chunk_id=None,
        old_html=None,
        new_html="<h2>Assessment</h2><p>Exit slip...</p>",
        ai_explanation="Added missing canonical Assessment section",
    )
    assert not hasattr(second, "insert_after_chunk_id")
    assert snapshot.awaiting_kind == "change_review"


def test_get_job_awaiting_continue_prompt_has_no_pending_changes(
    fixture_loader: Any,
) -> None:
    payload: dict[str, Any] = fixture_loader("job_awaiting_continue_prompt.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").respond(json=payload)

        snapshot = client.get_job("job_9f8e7d6c")

    assert snapshot.status == "awaiting_approval"
    assert snapshot.awaiting_kind == "continue_prompt"
    assert snapshot.pending_changes == ()


def test_get_job_terminal_statuses_have_empty_pending_changes(
    fixture_loader: Any,
) -> None:
    completed: dict[str, Any] = fixture_loader("job_completed.json")
    failed: dict[str, Any] = fixture_loader("job_failed.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=completed),
                httpx.Response(200, json=failed),
            ]
        )

        done = client.get_job("job_9f8e7d6c")
        dead = client.get_job("job_9f8e7d6c")

    assert done.pending_changes == ()
    assert done.awaiting_kind is None
    assert dead.pending_changes == ()
    assert dead.error == "Model timeout"  # never raises on failed status here


def test_get_job_raw_equals_full_fixture(fixture_loader: Any) -> None:
    payload: dict[str, Any] = fixture_loader("job_awaiting_approval.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").respond(json=payload)

        snapshot = client.get_job("job_9f8e7d6c")

    assert snapshot.raw == payload


def test_get_job_compact_false_passes_query_param(fixture_loader: Any) -> None:
    payload: dict[str, Any] = fixture_loader("job_pending.json")
    client, router = _make_client()
    with router:
        route = router.get("/v1/jobs/job_9f8e7d6c").respond(json=payload)

        client.get_job("job_9f8e7d6c", compact=False)

    assert route.calls.last.request.url.params["compact"] == "false"


# -------------------------------------------------------- wait_for_terminal


def test_wait_for_terminal_walks_to_completed(fixture_loader: Any) -> None:
    pending: dict[str, Any] = fixture_loader("job_pending.json")
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    awaiting: dict[str, Any] = fixture_loader("job_awaiting_approval.json")
    completed: dict[str, Any] = fixture_loader("job_completed.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=pending),
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=awaiting),  # NOT terminal — keep going
                httpx.Response(200, json=completed),
            ]
        )

        snapshot = client.wait_for_terminal("job_9f8e7d6c", poll_interval=0)

    assert snapshot.status == "completed"
    assert snapshot.progress == 100


def test_wait_for_terminal_raises_job_failed_error(fixture_loader: Any) -> None:
    pending: dict[str, Any] = fixture_loader("job_pending.json")
    failed: dict[str, Any] = fixture_loader("job_failed.json")
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=pending),
                httpx.Response(200, json=failed),
            ]
        )

        with pytest.raises(JobFailedError) as excinfo:
            client.wait_for_terminal("job_9f8e7d6c", poll_interval=0)

    assert str(excinfo.value) == "Model timeout"


def test_wait_for_terminal_times_out() -> None:
    client, router = _make_client()
    with router:
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "job_id": "job_9f8e7d6c",
                    "session_id": "s",
                    "status": "in_progress",
                },
            )
        )

        with pytest.raises(TimeoutError):
            client.wait_for_terminal("job_9f8e7d6c", poll_interval=0, timeout=0.05)
