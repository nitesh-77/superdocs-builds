"""Pydantic request schemas for the FastAPI wrapper.

The wrapper is deliberately thin: only request bodies need explicit
schemas; responses are plain dicts matching the documented contracts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class InstructionRequest(BaseModel):
    """Body of ``POST /sessions/{session_id}/instructions``."""

    instruction: str = Field(min_length=1)


class DecisionItem(BaseModel):
    """One per-change decision inside a batch approval submission."""

    change_id: str
    approved: bool
    feedback: str | None = None


class DecisionsRequest(BaseModel):
    """Body of ``POST /sessions/{session_id}/decisions``.

    A single approve/deny is this endpoint with a one-element
    ``decisions`` list.
    """

    job_id: str
    decisions: list[DecisionItem] = Field(min_length=1)


__all__ = ["DecisionItem", "DecisionsRequest", "InstructionRequest"]
