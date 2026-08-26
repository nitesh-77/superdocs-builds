"""Tests for the superdocs_orchestrator.cli demo driver."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import _make_client
from superdocs_orchestrator.cli import (
    _parse_env_file,
    build_parser,
    decide_changes,
    load_api_key,
    main,
    run,
)
from superdocs_orchestrator.exceptions import SuperDocsError
from superdocs_orchestrator.models import ChangeDecision, PendingChange


def _make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """A fully-populated CLI args namespace pointing into tmp_path."""
    defaults: dict[str, Any] = {
        "draft": str(tmp_path / "draft.md"),
        "instruction": "Restructure to our standard template",
        "template": None,
        "output": str(tmp_path / "out.docx"),
        "format": "docx",
        "approve_all": True,
        "env": str(tmp_path / ".env"),
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ------------------------------------------------------------------ env loading


def test_parse_env_file_handles_comments_quotes_and_blanks() -> None:
    text = "\n".join(
        [
            "# a comment line",
            "",
            "SUPERDOCS_API_KEY=\"sk_live_quoted\"",
            "SINGLE='abc'",
            "PLAIN=  spaced value  ",
            "EQUALS_IN_VALUE=a=b=c",
            "not a valid line",
            "   ",
        ]
    )
    parsed = _parse_env_file(text)
    assert parsed == {
        "SUPERDOCS_API_KEY": "sk_live_quoted",
        "SINGLE": "abc",
        "PLAIN": "spaced value",
        "EQUALS_IN_VALUE": "a=b=c",
    }


def test_load_api_key_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('SUPERDOCS_API_KEY="sk_live_x"\n', encoding="utf-8")

    assert load_api_key(env_file) == "sk_live_x"


def test_load_api_key_falls_back_to_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERDOCS_API_KEY", "sk_env_fallback")

    assert load_api_key(tmp_path / "missing.env") == "sk_env_fallback"


def test_load_api_key_missing_raises_readable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPERDOCS_API_KEY", raising=False)

    with pytest.raises(SuperDocsError) as excinfo:
        load_api_key(tmp_path / ".env")

    message = str(excinfo.value)
    assert ".env" in message
    assert "SUPERDOCS_API_KEY" in message
    # Never leak any key material.
    assert "sk_" not in message


# --------------------------------------------------------------- decide_changes


def _two_changes() -> list[PendingChange]:
    return [
        PendingChange(
            change_id="ch_1",
            operation="edit",
            chunk_id="chunk-1",
            old_html="<p>old</p>",
            new_html="<p>new</p>",
            ai_explanation="Tightened wording",
        ),
        PendingChange(
            change_id="ch_2",
            operation="create",
            chunk_id=None,
            old_html=None,
            new_html="<h2>Assessment</h2>",
            ai_explanation="Added missing Assessment section",
        ),
    ]


def test_decide_changes_interactive_mixed_decisions() -> None:
    answers = iter(["a", "d", "trim the intro"])
    echo_lines: list[str] = []

    decisions = decide_changes(
        _two_changes(),
        ask=lambda _prompt: next(answers),
        echo=echo_lines.append,
        auto_approve=False,
    )

    assert decisions == [
        ChangeDecision(change_id="ch_1", approved=True),
        ChangeDecision(change_id="ch_2", approved=False, feedback="trim the intro"),
    ]
    echoed = "\n".join(echo_lines)
    assert "ch_1" in echoed and "ch_2" in echoed
    assert "edit" in echoed and "create" in echoed
    assert "Tightened wording" in echoed
    assert "Added missing Assessment section" in echoed


def test_decide_changes_reprompts_on_invalid_answer() -> None:
    answers = iter(["maybe", "A"])

    decisions = decide_changes(
        _two_changes()[:1],
        ask=lambda _prompt: next(answers),
        echo=lambda _line: None,
        auto_approve=False,
    )

    assert decisions == [ChangeDecision(change_id="ch_1", approved=True)]


def test_decide_changes_auto_approve_never_prompts() -> None:
    def fail_ask(_prompt: str) -> str:
        raise AssertionError("ask must not be called with auto_approve=True")

    decisions = decide_changes(
        _two_changes(),
        ask=fail_ask,
        echo=lambda _line: None,
        auto_approve=True,
    )

    assert decisions == [
        ChangeDecision(change_id="ch_1", approved=True),
        ChangeDecision(change_id="ch_2", approved=True),
    ]


# ------------------------------------------------------------------ run() flows


def _register_demo_routes(router: Any, fixture_loader: Any) -> tuple[Any, Any]:
    """Register upload/chat/job/approve/export routes; return (approve, export)."""
    upload_payload: dict[str, Any] = fixture_loader("upload_response.json")
    chat_payload: dict[str, Any] = fixture_loader("async_chat_response.json")
    router.post("/v1/documents/upload").respond(json=upload_payload)
    router.post("/v1/chat/async").respond(json=chat_payload)
    approve_route = router.post(
        "/v1/chat/lesson-grade8-science-photosynthesis/approve"
    ).respond(json={})
    export_route = router.post("/v1/documents/export").respond(
        200, content=b"PK demo"
    )
    return approve_route, export_route


def test_run_happy_path_exports_file(
    tmp_path: Path, fixture_loader: Any
) -> None:
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    awaiting: dict[str, Any] = fixture_loader(
        "job_awaiting_approval_double_encoded.json"
    )
    completed: dict[str, Any] = fixture_loader("job_completed.json")

    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n\nPhotosynthesis.", encoding="utf-8")
    out = tmp_path / "out.docx"

    client, router = _make_client()
    args = _make_args(tmp_path, output=str(out))
    echo_lines: list[str] = []
    err_lines: list[str] = []

    with router:
        approve_route, _export_route = _register_demo_routes(router, fixture_loader)
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=awaiting),
                httpx.Response(200, json=completed),
            ]
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=echo_lines.append,
            err=err_lines.append,
        )

    assert rc == 0
    assert out.exists()
    assert out.read_bytes() == b"PK demo"

    assert approve_route.call_count == 1
    body: dict[str, Any] = json.loads(approve_route.calls.last.request.content)
    assert body["approved"] is True
    assert len(body["changes"]) == 2

    echoed = "\n".join(echo_lines)
    assert "ch_1" in echoed and "ch_2" in echoed
    assert "Added GDPR data processing requirements" in echoed
    assert "Added missing canonical Assessment section" in echoed
    assert err_lines == []


def test_run_handles_multiple_rounds_after_denial(
    tmp_path: Path, fixture_loader: Any
) -> None:
    awaiting: dict[str, Any] = fixture_loader(
        "job_awaiting_approval_double_encoded.json"
    )
    completed: dict[str, Any] = fixture_loader("job_completed.json")

    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path, approve_all=False)
    echo_lines: list[str] = []
    err_lines: list[str] = []
    # Round 1: approve ch_1, deny ch_2 WITH feedback. Round 2: approve both.
    answers = iter(["a", "d", "keep original wording", "a", "a"])

    with router:
        approve_route, export_route = _register_demo_routes(router, fixture_loader)
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=awaiting),
                httpx.Response(200, json=awaiting),  # revised proposal
                httpx.Response(200, json=completed),
            ]
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: next(answers),
            echo=echo_lines.append,
            err=err_lines.append,
        )

    assert rc == 0
    assert approve_route.call_count == 2

    first_body: dict[str, Any] = json.loads(approve_route.calls[0].request.content)
    assert first_body["approved"] is False
    assert first_body["changes"][0] == {"change_id": "ch_1", "approved": True}
    assert first_body["changes"][1] == {
        "change_id": "ch_2",
        "approved": False,
        "feedback": "keep original wording",
    }

    second_body: dict[str, Any] = json.loads(approve_route.calls[1].request.content)
    assert second_body["approved"] is True
    assert second_body["changes"] == [
        {"change_id": "ch_1", "approved": True},
        {"change_id": "ch_2", "approved": True},
    ]

    assert export_route.call_count == 1
    assert err_lines == []


def test_run_surfaces_failed_job_without_traceback(
    tmp_path: Path, fixture_loader: Any
) -> None:
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    failed: dict[str, Any] = fixture_loader("job_failed.json")

    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path)
    err_lines: list[str] = []

    with router:
        # Job fails before any approval round: no approve/export routes.
        router.post("/v1/documents/upload").respond(
            json=fixture_loader("upload_response.json")
        )
        router.post("/v1/chat/async").respond(json=fixture_loader("async_chat_response.json"))
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=failed),
            ]
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=lambda _line: None,
            err=err_lines.append,
        )

    assert rc == 1
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert "Model timeout" in err_lines[0]


def test_run_surfaces_http_error_readably(
    tmp_path: Path, fixture_loader: Any
) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path)
    err_lines: list[str] = []

    with router:
        # Only the upload route exists and it 500s; nothing else registered.
        router.post("/v1/documents/upload").respond(status_code=500)

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=lambda _line: None,
            err=err_lines.append,
        )

    assert rc == 1
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert "HTTP 500" in err_lines[0]


def test_main_missing_key_returns_1_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SUPERDOCS_API_KEY", raising=False)
    argv = [
        "--env",
        str(tmp_path / "nonexistent.env"),
        str(tmp_path / "draft.md"),
        "Restructure the lesson",
    ]

    rc = main(argv)

    assert rc == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert ".env" in combined
    assert "sk_" not in combined


def test_output_default_derived_from_draft_and_format(
    tmp_path: Path, fixture_loader: Any
) -> None:
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    completed: dict[str, Any] = fixture_loader("job_completed.json")

    draft = tmp_path / "myplan.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(
        tmp_path, draft=str(draft), output=None, format="pdf"
    )
    err_lines: list[str] = []

    with router:
        # No approval round: approve route never called, so not registered.
        router.post("/v1/documents/upload").respond(
            json=fixture_loader("upload_response.json")
        )
        router.post("/v1/chat/async").respond(json=fixture_loader("async_chat_response.json"))
        export_route = router.post("/v1/documents/export").respond(
            200, content=b"PK demo"
        )
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=completed),
            ]
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=lambda _line: None,
            err=err_lines.append,
        )

    assert rc == 0
    expected = tmp_path / "myplan-edited.pdf"
    assert expected.exists()


def test_build_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["draft.md", "Reformat it"])

    assert args.template is None
    assert args.output is None
    assert args.format == "docx"
    assert args.approve_all is False
    assert args.env == ".env"


def test_run_surfaces_connect_error_readably(
    tmp_path: Path, fixture_loader: Any
) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path)
    err_lines: list[str] = []

    with router:
        router.post("/v1/documents/upload").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=lambda _line: None,
            err=err_lines.append,
        )

    assert rc == 1
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert "connection refused" in err_lines[0]


def test_main_handles_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import superdocs_orchestrator.cli as cli_module

    monkeypatch.setenv("SUPERDOCS_API_KEY", "sk_test_key")
    monkeypatch.setattr(
        cli_module,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    argv = [
        "--env",
        str(tmp_path / "nonexistent.env"),
        str(tmp_path / "draft.md"),
        "Restructure the lesson",
    ]

    rc = main(argv)

    assert rc == 130
    captured = capsys.readouterr()
    assert "Interrupted." in captured.err


def test_run_stops_after_round_cap_when_job_never_leaves_gate(
    tmp_path: Path, fixture_loader: Any
) -> None:
    from superdocs_orchestrator.cli import MAX_APPROVAL_ROUNDS

    awaiting: dict[str, Any] = fixture_loader(
        "job_awaiting_approval_double_encoded.json"
    )
    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path)
    echo_lines: list[str] = []
    err_lines: list[str] = []

    with router:
        router.post("/v1/documents/upload").respond(
            json=fixture_loader("upload_response.json")
        )
        router.post("/v1/chat/async").respond(json=fixture_loader("async_chat_response.json"))
        approve_route = router.post(
            "/v1/chat/lesson-grade8-science-photosynthesis/approve"
        ).respond(json={})
        # Always re-park in awaiting_approval — never terminal.
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=lambda _request: httpx.Response(200, json=awaiting)
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=echo_lines.append,
            err=err_lines.append,
        )

    assert rc == 1
    assert approve_route.call_count == MAX_APPROVAL_ROUNDS
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert str(MAX_APPROVAL_ROUNDS) in err_lines[0]


def test_run_uploads_template_when_given(
    tmp_path: Path, fixture_loader: Any
) -> None:
    in_progress: dict[str, Any] = fixture_loader("job_in_progress.json")
    completed: dict[str, Any] = fixture_loader("job_completed.json")

    draft = tmp_path / "draft.md"
    draft.write_text("# Lesson Plan\n", encoding="utf-8")
    template = tmp_path / "template.html"
    template.write_text("<h2>Template</h2>", encoding="utf-8")

    client, router = _make_client()
    args = _make_args(tmp_path, template=str(template))
    echo_lines: list[str] = []
    err_lines: list[str] = []

    with router:
        router.post("/v1/documents/upload").respond(
            json=fixture_loader("upload_response.json")
        )
        template_route = router.post("/v1/templates/upload").respond(json={})
        router.post("/v1/chat/async").respond(json=fixture_loader("async_chat_response.json"))
        router.post("/v1/documents/export").respond(200, content=b"PK demo")
        router.get("/v1/jobs/job_9f8e7d6c").mock(
            side_effect=[
                httpx.Response(200, json=in_progress),
                httpx.Response(200, json=completed),
            ]
        )

        rc = run(
            args,
            client=client,
            ask=lambda _prompt: "a",
            echo=echo_lines.append,
            err=err_lines.append,
        )

    assert rc == 0
    assert template_route.call_count == 1
    echoed = "\n".join(echo_lines)
    assert "template" in echoed.lower()
    assert err_lines == []
