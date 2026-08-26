"""Tests for SuperDocsClient.export_document."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import _make_client
from superdocs_orchestrator.exceptions import SuperDocsError

MIME_BY_FORMAT = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "pdf": "application/pdf",
    "html": "text/html",
}


@pytest.mark.parametrize("output_format", ["docx", "pdf", "html"])
def test_export_writes_binary_response_to_disk(
    tmp_path: Path, output_format: str
) -> None:
    client, router = _make_client()
    out_path = tmp_path / f"out.{output_format}"
    with router:
        route = router.post("/v1/documents/export").respond(
            200,
            content=b"FAKEBYTES",
            headers={"Content-Type": MIME_BY_FORMAT[output_format]},
        )

        result = client.export_document("sess-1", output_format, out_path)  # type: ignore[arg-type]

    assert result == out_path
    assert out_path.exists()
    assert out_path.read_bytes() == b"FAKEBYTES"

    assert json.loads(route.calls.last.request.content) == {
        "session_id": "sess-1",
        "format": output_format,
    }
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk_test_key"


def test_export_rejects_unknown_format_without_http_call(tmp_path: Path) -> None:
    client, router = _make_client()
    # Explicit start/stop: `with router:` would fail on exit because the
    # registered route is intentionally never called.
    router.start()
    try:
        # A 500 here would surface loudly if the route were ever hit.
        route = router.post("/v1/documents/export").respond(status_code=500)

        with pytest.raises(SuperDocsError):
            client.export_document("sess-1", "exe", tmp_path / "out.exe")  # type: ignore[arg-type]
    finally:
        router.stop(quiet=True)

    assert route.call_count == 0
