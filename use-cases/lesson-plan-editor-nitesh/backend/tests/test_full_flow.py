"""Headless end-to-end proof of the HITL lesson-plan flow (issue #4).

One test, one respx router, zero live network:
upload -> start chat job -> poll (awaiting approval) -> approve/deny ->
poll to completion -> export the document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from conftest import _make_client


def test_full_hitl_flow_upload_chat_approve_deny_export(
    tmp_path: Path, fixture_loader: Any
) -> None:
    upload_payload: dict[str, Any] = fixture_loader("upload_response.json")
    chat_payload: dict[str, Any] = fixture_loader("async_chat_response.json")
    awaiting: dict[str, Any] = fixture_loader(
        "job_awaiting_approval_double_encoded.json"
    )
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    completed: dict[str, Any] = fixture_loader("job_completed.json")

    doc_file = tmp_path / "lesson.md"
    doc_file.write_text(
        "# Lesson Plan\n\nPhotosynthesis for grade 8 science.",
        encoding="utf-8",
    )

    client, router = _make_client()
    with router:
        upload_route = router.post("/v1/documents/upload").respond(json=upload_payload)
        chat_route = router.post("/v1/chat/async").respond(json=chat_payload)
        job_route = router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=awaiting),
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=completed),
            ]
        )
        approve_route = router.post("/v1/chat/sess-1/approve").respond(json={})
        export_route = router.post("/v1/documents/export").respond(
            200, content=b"PK\x03\x04 fake docx"
        )

        client.upload_document(doc_file, session_id="sess-1", index=True)
        job_id = client.start_chat_job("sess-1", "Restructure to our standard template")
        assert job_id == "job_9f8e7d6c"

        snapshot = client.get_job(job_id)
        assert snapshot.status == "awaiting_approval"
        assert len(snapshot.pending_changes) == 2

        client.approve_change("sess-1", job_id, "ch_1")
        client.deny_change("sess-1", job_id, "ch_2", feedback="Not aligned with objectives")

        final_snapshot = client.wait_for_terminal(job_id, poll_interval=0)
        out_path = tmp_path / "lesson.docx"
        result_path = client.export_document("sess-1", "docx", out_path)

    # Each route hit exactly once (job polling is a side_effect sequence).
    assert upload_route.call_count == 1
    assert chat_route.call_count == 1
    assert job_route.call_count == 3
    assert approve_route.call_count == 2
    assert export_route.call_count == 1

    approve_calls = approve_route.calls
    first_body: dict[str, Any] = json.loads(approve_calls[0].request.content)
    second_body: dict[str, Any] = json.loads(approve_calls[1].request.content)
    assert first_body["change_id"] == "ch_1"
    assert first_body["approved"] is True
    assert second_body["change_id"] == "ch_2"
    assert second_body["approved"] is False
    assert second_body["feedback"] == "Not aligned with objectives"

    assert final_snapshot.status == "completed"
    assert final_snapshot.progress == 100

    assert result_path == out_path
    assert out_path.read_bytes() == b"PK\x03\x04 fake docx"
