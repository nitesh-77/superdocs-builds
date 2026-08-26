"""Tests proving the HTTP transport is injectable into SuperDocsClient."""

from __future__ import annotations

import httpx
import pytest
import respx

from superdocs_orchestrator.client import SuperDocsClient

ALT_BASE_URL = "https://alt.example.internal"
DEFAULT_BASE_URL = "https://api.superdocs.app"


def test_caller_constructed_client_is_used_for_requests() -> None:
    """A caller-built httpx.Client (custom base_url) is used as-is.

    We point the injected client at an alternate base URL and prove the
    request landed there — even though the client's default base_url differs.
    """
    with respx.mock(base_url=ALT_BASE_URL) as router:
        route = router.get("/v1/jobs/job_1").respond(
            json={
                "job_id": "job_1",
                "session_id": "s",
                "status": "completed",
                "progress": 100,
                "result": None,
                "error": None,
                "metadata": None,
            }
        )
        http_client = httpx.Client(base_url=ALT_BASE_URL)
        client = SuperDocsClient(api_key="sk_test_key", http_client=http_client)

        snapshot = client.get_job("job_1")

    assert snapshot.status == "completed"
    assert route.calls.last.request.url.host == "alt.example.internal"
    assert str(route.calls.last.request.url).startswith(ALT_BASE_URL)


def test_missing_api_key_and_env_var_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPERDOCS_API_KEY", raising=False)

    with pytest.raises(ValueError):
        SuperDocsClient()


def test_api_key_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERDOCS_API_KEY", "sk_from_env")

    client = SuperDocsClient()
    assert client.api_key == "sk_from_env"


def test_auth_header_is_bearer_token() -> None:
    with respx.mock(base_url=DEFAULT_BASE_URL) as router:
        route = router.get("/v1/jobs/job_1").respond(
            json={
                "job_id": "job_1",
                "session_id": "s",
                "status": "pending",
                "progress": 0,
                "result": None,
                "error": None,
                "metadata": None,
            }
        )
        http_client = httpx.Client(base_url=DEFAULT_BASE_URL)
        client = SuperDocsClient(api_key="sk_test_key", http_client=http_client)

        client.get_job("job_1")

    auth = route.calls.last.request.headers["Authorization"]
    assert auth == "Bearer sk_test_key"


def test_explicit_api_key_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERDOCS_API_KEY", "sk_from_env")

    client = SuperDocsClient(api_key="sk_test_key")
    assert client.api_key == "sk_test_key"
