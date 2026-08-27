"""Tests for SuperDocsClient.list_documents."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import BASE_URL, _make_client
from superdocs_orchestrator.exceptions import SuperDocsError


def _canned_listing() -> dict[str, Any]:
    return {
        "documents": [
            {
                "document_id": "doc_abc",
                "title": "Class 8 :: Science :: Ch. 7: Photosynthesis",
                "session_count": 2,
            }
        ],
        "total": 1,
    }


def test_list_documents_returns_parsed_dict() -> None:
    canned = _canned_listing()
    client, router = _make_client()
    with router:
        route = router.get("/v1/documents").respond(json=canned)

        result = client.list_documents()

    assert result == canned
    request = route.calls.last.request
    assert str(request.url).startswith(f"{BASE_URL}/v1/documents")
    assert request.headers["Authorization"] == "Bearer sk_test_key"


def test_list_documents_default_query_params() -> None:
    client, router = _make_client()
    with router:
        route = router.get("/v1/documents").respond(json=_canned_listing())

        client.list_documents()

    params = route.calls.last.request.url.params
    assert params["limit"] == "50"
    assert params["offset"] == "0"
    assert params["include_preview"] == "false"
    assert params["archived"] == "false"


def test_list_documents_custom_params_passed_through() -> None:
    client, router = _make_client()
    with router:
        route = router.get("/v1/documents").respond(json=_canned_listing())

        client.list_documents(
            limit=10, offset=5, include_preview=True, archived=True
        )

    params = route.calls.last.request.url.params
    assert params["limit"] == "10"
    assert params["offset"] == "5"
    assert params["include_preview"] == "true"
    assert params["archived"] == "true"


@pytest.mark.parametrize("bad_status", [401, 500])
def test_list_documents_raises_superdocs_error_on_http_error(
    bad_status: int,
) -> None:
    client, router = _make_client()
    with router:
        router.get("/v1/documents").respond(
            status_code=bad_status, json={"detail": "boom"}
        )

        with pytest.raises(SuperDocsError):
            client.list_documents()


def test_list_documents_raises_on_non_object_payload() -> None:
    client, router = _make_client()
    with router:
        router.get("/v1/documents").respond(json=[1, 2, 3])

        with pytest.raises(SuperDocsError):
            client.list_documents()
