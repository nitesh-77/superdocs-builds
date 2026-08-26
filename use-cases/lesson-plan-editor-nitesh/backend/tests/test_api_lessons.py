"""Tests for the FastAPI wrapper: POST /lessons and GET /lessons."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from conftest import load_fixture
from superdocs_api.app import create_app


def test_post_lessons_happy_path_encodes_title_in_upload_filename(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    payload: dict[str, Any] = load_fixture("upload_response.json")
    route = router.post("/v1/documents/upload").respond(json=payload)

    response = test_client.post(
        "/lessons",
        data={
            "class_name": "Class 8",
            "subject": "Science",
            "chapter": "Ch. 7: Photosynthesis",
        },
        files={"file": ("lesson draft.md", b"<h1>Photosynthesis</h1>", "text/html")},
    )

    assert response.status_code == 201
    assert response.json() == {
        "session_id": "lesson-grade8-science-photosynthesis",
        "filename": "grade8-science-photosynthesis-draft.md",
        "chunks_count": 12,
        "version_id": "v_xyz789",
    }
    # Never leak the chunked HTML (or anything else beyond the contract).
    assert "html" not in response.json()

    # The assembled title must ride upstream as the multipart filename.
    request = route.calls.last.request
    body_text = request.content.decode("utf-8")
    assert 'filename="Class 8 :: Science :: Ch. 7: Photosynthesis.md"' in body_text


def test_post_lessons_defaults_suffix_to_html_when_filename_has_none(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    payload: dict[str, Any] = load_fixture("upload_response.json")
    route = router.post("/v1/documents/upload").respond(json=payload)

    response = test_client.post(
        "/lessons",
        data={"class_name": "Class 9", "subject": "History", "chapter": "Ch. 1"},
        files={"file": ("noext", b"<p>x</p>", "application/octet-stream")},
    )

    assert response.status_code == 201
    body_text = route.calls.last.request.content.decode("utf-8")
    assert 'filename="Class 9 :: History :: Ch. 1.html"' in body_text


def test_post_lessons_missing_form_field_returns_422(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, _router = api

    response = test_client.post(
        "/lessons",
        data={"class_name": "Class 8", "subject": "Science"},  # chapter missing
        files={"file": ("lesson.html", b"<p>x</p>", "text/html")},
    )

    assert response.status_code == 422


def test_post_lessons_rejects_delimiter_in_class_name(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, _router = api

    response = test_client.post(
        "/lessons",
        data={
            "class_name": "Class :: 8",
            "subject": "Science",
            "chapter": "Ch. 7",
        },
        files={"file": ("lesson.html", b"<p>x</p>", "text/html")},
    )

    assert response.status_code == 422
    assert "class_name" in response.json()["detail"]


def test_post_lessons_maps_superdocs_error_to_502_without_key_leak(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.post("/v1/documents/upload").respond(
        status_code=500, json={"detail": "boom"}
    )

    response = test_client.post(
        "/lessons",
        data={"class_name": "Class 8", "subject": "Science", "chapter": "Ch. 7"},
        files={"file": ("lesson.html", b"<p>x</p>", "text/html")},
    )

    assert response.status_code == 502
    assert "boom" not in response.text  # upstream detail is not passed through
    assert "sk_test_key" not in response.text


def test_get_lessons_groups_parses_sorts_and_skips_foreign_titles(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    canned: dict[str, Any] = {
        "documents": [
            {
                "document_id": "doc_b",
                "title": "Class 8 :: Science :: Ch. 7: Photosynthesis",
                "session_count": 2,
            },
            {
                "document_id": "doc_a",
                "title": "Class 8 :: Maths :: Ch. 1: Algebra",
                "session_count": 0,
            },
            {
                "document_id": "doc_c",
                "title": "Class 7 :: Science :: Ch. 2: Matter",
                "session_count": 1,
            },
            # Foreign document: unparseable title must be skipped.
            {"document_id": "doc_d", "title": "Meeting notes", "session_count": 3},
            # Missing title entirely: skipped as well.
            {"document_id": "doc_e", "session_count": 1},
        ],
        "total": 5,
    }
    route = router.get("/v1/documents").respond(json=canned)

    response = test_client.get("/lessons")

    assert response.status_code == 200
    params = route.calls.last.request.url.params
    assert params["limit"] == "50"
    assert params["offset"] == "0"
    assert response.json() == {
        "groups": [
            {
                "class_name": "Class 7",
                "subjects": [
                    {
                        "subject": "Science",
                        "lessons": [
                            {
                                "chapter": "Ch. 2: Matter",
                                "title": "Class 7 :: Science :: Ch. 2: Matter",
                                "document_id": "doc_c",
                                "session_count": 1,
                            }
                        ],
                    }
                ],
            },
            {
                "class_name": "Class 8",
                "subjects": [
                    {
                        "subject": "Maths",
                        "lessons": [
                            {
                                "chapter": "Ch. 1: Algebra",
                                "title": "Class 8 :: Maths :: Ch. 1: Algebra",
                                "document_id": "doc_a",
                                "session_count": 0,
                            }
                        ],
                    },
                    {
                        "subject": "Science",
                        "lessons": [
                            {
                                "chapter": "Ch. 7: Photosynthesis",
                                "title": "Class 8 :: Science :: Ch. 7: Photosynthesis",
                                "document_id": "doc_b",
                                "session_count": 2,
                            }
                        ],
                    },
                ],
            },
        ]
    }


def test_get_lessons_strips_upload_extension_from_title(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    """A stored title carrying the upload suffix must not pollute the chapter.

    SuperDocs derives the document title from the uploaded filename, which
    the app encodes as ``<Class> :: <Subject> :: <Chapter><suffix>``. The
    list endpoint must strip a known extension before parsing so the
    chapter round-trips cleanly (regression for the .md leak).
    """
    test_client, router = api
    canned: dict[str, Any] = {
        "documents": [
            {
                "document_id": "doc_b",
                "title": "Class 8 :: Science :: Ch. 7: Photosynthesis.md",
                "session_count": 2,
            }
        ],
        "total": 1,
    }
    router.get("/v1/documents").respond(json=canned)

    response = test_client.get("/lessons")

    assert response.status_code == 200
    lessons = response.json()["groups"][0]["subjects"][0]["lessons"]
    assert lessons == [
        {
            "chapter": "Ch. 7: Photosynthesis",
            "title": "Class 8 :: Science :: Ch. 7: Photosynthesis",
            "document_id": "doc_b",
            "session_count": 2,
        }
    ]


def test_get_lessons_passes_custom_pagination(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    route = router.get("/v1/documents").respond(json={"documents": [], "total": 0})

    response = test_client.get("/lessons", params={"limit": 10, "offset": 5})

    assert response.status_code == 200
    assert response.json() == {"groups": []}
    params = route.calls.last.request.url.params
    assert params["limit"] == "10"
    assert params["offset"] == "5"


def test_get_lessons_maps_superdocs_error_to_502(
    api: tuple[TestClient, respx.MockRouter],
) -> None:
    test_client, router = api
    router.get("/v1/documents").respond(status_code=401, json={"detail": "nope"})

    response = test_client.get("/lessons")

    assert response.status_code == 502
    assert "sk_test_key" not in response.text


def test_create_app_without_api_key_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPERDOCS_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_app()
