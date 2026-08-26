"""Shared pytest fixtures for the SuperDocs orchestrator test suite."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from superdocs_orchestrator.client import SuperDocsClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"

BASE_URL = "https://api.superdocs.app"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a canned JSON payload from the fixtures directory."""
    with (FIXTURES_DIR / name).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _make_client() -> tuple[SuperDocsClient, respx.MockRouter]:
    """Canonical client-under-test plus a matching respx router."""
    http_client = httpx.Client(base_url=BASE_URL)
    client = SuperDocsClient(api_key="sk_test_key", http_client=http_client)
    return client, respx.mock(base_url=BASE_URL)


@pytest.fixture()
def fixture_loader() -> Callable[[str], dict[str, Any]]:
    return load_fixture


@pytest.fixture()
def http_client() -> httpx.Client:
    """A caller-constructed httpx.Client handed to SuperDocsClient via injection."""
    return httpx.Client(base_url=BASE_URL)
