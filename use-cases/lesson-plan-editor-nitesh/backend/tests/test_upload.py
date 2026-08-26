"""Tests for SuperDocsClient.upload_document / upload_template."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import BASE_URL, _make_client
from superdocs_orchestrator.models import UploadedDocument


def test_upload_document_returns_uploaded_document(
    tmp_path: Path, fixture_loader: Any
) -> None:
    payload: dict[str, Any] = fixture_loader("upload_response.json")
    client, router = _make_client()
    with router:
        router.post("/v1/documents/upload").respond(json=payload)
        doc_file = tmp_path / "lesson.md"
        doc_file.write_text("# Photosynthesis\n...", encoding="utf-8")

        result = client.upload_document(doc_file)

    assert isinstance(result, UploadedDocument)
    assert result.html == payload["html"]
    assert result.chunks_count == 12
    assert result.session_id == "lesson-grade8-science-photosynthesis"
    assert result.filename == "grade8-science-photosynthesis-draft.md"
    assert result.version_id == "v_xyz789"


def test_upload_document_multipart_and_index_query(
    tmp_path: Path, fixture_loader: Any
) -> None:
    payload: dict[str, Any] = fixture_loader("upload_response.json")
    client, router = _make_client()
    with router:
        route = router.post("/v1/documents/upload").respond(json=payload)
        doc_file = tmp_path / "lesson.md"
        doc_file.write_bytes(b"<p>binary-ish content</p>")

        client.upload_document(doc_file, index=True)

    request = route.calls.last.request
    assert request.url.params["index"] == "true"
    assert request.headers["Authorization"] == "Bearer sk_test_key"
    body = request.content.decode("utf-8")
    assert 'name="file"' in body
    assert "<p>binary-ish content</p>" in body


def test_upload_template_posts_multipart_and_returns_dict_unchanged(
    tmp_path: Path,
) -> None:
    canned: dict[str, Any] = {
        "template_id": "tpl_123",
        "name": "exit-slip",
        "html": "<h2>Exit slip</h2>",
    }
    client, router = _make_client()
    with router:
        route = router.post("/v1/templates/upload").respond(json=canned)
        tpl_file = tmp_path / "template.html"
        tpl_file.write_text("<h2>Exit slip</h2>", encoding="utf-8")

        result = client.upload_template(tpl_file)

    assert result == canned
    request = route.calls.last.request
    assert str(request.url) == f"{BASE_URL}/v1/templates/upload"
    assert request.headers["Authorization"] == "Bearer sk_test_key"
    assert 'name="file"' in request.content.decode("utf-8")


@pytest.mark.parametrize("bad_status", [401, 413, 500])
def test_upload_document_raises_superdocs_error_on_http_error(
    tmp_path: Path, bad_status: int
) -> None:
    from superdocs_orchestrator.exceptions import SuperDocsError

    client, router = _make_client()
    with router:
        router.post("/v1/documents/upload").respond(
            status_code=bad_status, json={"detail": "boom"}
        )
        doc_file = tmp_path / "lesson.md"
        doc_file.write_text("content", encoding="utf-8")

        with pytest.raises(SuperDocsError):
            client.upload_document(doc_file)
