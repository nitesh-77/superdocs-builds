"""superdocs_api: thin FastAPI wrapper over superdocs_orchestrator."""

from __future__ import annotations

from .app import create_app, get_client

__all__ = ["create_app", "get_client"]
