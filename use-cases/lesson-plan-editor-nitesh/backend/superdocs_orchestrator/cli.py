"""Command-line demo driver for the SuperDocs lesson-plan flow.

Runs one full demo scenario headlessly or interactively:

1. Upload a draft document (plus an optional template).
2. Start an async chat job with a verbatim reformat instruction.
3. Poll until the job reaches a terminal state or parks in
   ``awaiting_approval`` with proposed changes.
4. Surface each pending change (id, operation, ``ai_explanation``) and ask
   approve/deny per change — or approve everything via ``--approve-all``.
5. Submit the decisions and repeat while the job re-parks in
   ``awaiting_approval`` (a denial with feedback produces a revised
   proposal round).
6. Export the finished session document to disk.

The SuperDocs API key is read from an untracked dotenv file (default
``.env``, override with ``--env``) or the ``SUPERDOCS_API_KEY``
environment variable. The key is never hardcoded, never echoed, and never
included in any error message.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from .client import API_KEY_ENV_VAR, _TERMINAL_STATUSES, SuperDocsClient
from .exceptions import SuperDocsError
from .models import (
    ChangeDecision,
    ExportFormat,
    JobSnapshot,
    PendingChange,
    parse_export_format,
)

DEFAULT_ENV_PATH = ".env"
MAX_APPROVAL_ROUNDS = 10


# ---------------------------------------------------------------------------
# env loading
# ---------------------------------------------------------------------------


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse simple dotenv format into a mapping.

    Skips blank lines and ``#`` comment lines. Splits each line on the
    FIRST ``=``, strips surrounding whitespace on key/value, and strips one
    layer of matching single or double quotes from the value. Malformed
    lines (no ``=``) are skipped silently.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def load_api_key(env_path: Path) -> str:
    """Resolve the SuperDocs API key from ``env_path`` or the environment.

    Returns ``SUPERDOCS_API_KEY`` from the dotenv file if present and
    non-empty; otherwise falls back to the ``SUPERDOCS_API_KEY``
    environment variable. Raises :class:`SuperDocsError` when neither
    source yields a key. No key material ever appears in error messages.
    """
    key: str | None = None
    try:
        values = _parse_env_file(env_path.read_text(encoding="utf-8"))
        candidate = values.get(API_KEY_ENV_VAR, "")
        if candidate:
            key = candidate
    except OSError:
        pass  # missing/unreadable file: fall through to the environment
    if key is None:
        key = os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise SuperDocsError(
            f"No API key found: set {API_KEY_ENV_VAR} in the {env_path} "
            f"dotenv file or export the {API_KEY_ENV_VAR} environment "
            "variable."
        )
    return key


# ---------------------------------------------------------------------------
# decision seam (pure, testable)
# ---------------------------------------------------------------------------


def _describe_change(index: int, change: PendingChange) -> str:
    """One-line human summary of a pending change for terminal display."""
    old_len = len(change.old_html or "")
    new_len = len(change.new_html or "")
    return (
        f"[{index}] {change.change_id} "
        f"({change.operation}: {old_len} -> {new_len} chars)"
    )


def decide_changes(
    changes: Sequence[PendingChange],
    *,
    ask: Callable[[str], str],
    echo: Callable[[str], None],
    auto_approve: bool,
) -> list[ChangeDecision]:
    """Turn pending changes into per-change decisions.

    With ``auto_approve`` every change is approved without prompting.
    Otherwise each change is echoed (index, id, operation, size delta,
    ``ai_explanation``) and an approve/deny answer is requested; denials
    optionally carry feedback (which triggers a revised proposal round).
    Invalid answers re-prompt until ``a``/``d`` is given.
    """
    if auto_approve:
        auto_decisions = []
        for index, change in enumerate(changes, start=1):
            echo(f"{_describe_change(index, change)} [auto-approved]")
            echo(f"    ai_explanation: {change.ai_explanation}")
            auto_decisions.append(
                ChangeDecision(change_id=change.change_id, approved=True)
            )
        return auto_decisions

    decisions: list[ChangeDecision] = []
    for index, change in enumerate(changes, start=1):
        echo(_describe_change(index, change))
        echo(f"    ai_explanation: {change.ai_explanation}")

        while True:
            answer = ask("[A]pprove / [D]eny? ").strip().lower()
            if answer in ("a", "approve"):
                decisions.append(
                    ChangeDecision(change_id=change.change_id, approved=True)
                )
                break
            if answer in ("d", "deny"):
                feedback = ask(
                    "Feedback for the AI (optional, Enter to skip): "
                ).strip()
                decisions.append(
                    ChangeDecision(
                        change_id=change.change_id,
                        approved=False,
                        feedback=feedback or None,
                    )
                )
                break
            echo("Please answer 'a' or 'd'.")
    return decisions


# ---------------------------------------------------------------------------
# polling
# ---------------------------------------------------------------------------


def poll_until_gate(
    client: SuperDocsClient,
    job_id: str,
    *,
    echo: Callable[[str], None],
    poll_interval: float = 2.0,
    timeout: float = 3600.0,
) -> JobSnapshot:
    """Poll until a terminal status or an actionable approval gate.

    Returns as soon as the job is terminal OR parked in
    ``awaiting_approval`` with non-empty ``pending_changes``. Deliberately
    does NOT use ``SuperDocsClient.wait_for_terminal``, which would skip
    past the gate. Raises :class:`TimeoutError` past ``timeout`` seconds.
    """
    deadline = time.monotonic() + timeout
    while True:
        snapshot = client.get_job(job_id)
        echo(f"status={snapshot.status} progress={snapshot.progress}")
        at_gate = (
            snapshot.status == "awaiting_approval" and snapshot.pending_changes
        )
        if snapshot.status in _TERMINAL_STATUSES or at_gate:
            return snapshot
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Job {job_id!r} did not reach a terminal state within "
                f"{timeout} seconds (last status: {snapshot.status})"
            )
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


def run(
    args: argparse.Namespace,
    *,
    client: SuperDocsClient,
    ask: Callable[[str], str],
    echo: Callable[[str], None],
    err: Callable[[str], None],
) -> int:
    """Drive one full demo scenario. Returns 0 on success, 1 on failure."""
    try:
        uploaded = client.upload_document(Path(args.draft))
        session_id = uploaded.session_id
        if session_id is None:
            err("error: upload response did not include a session_id")
            return 1
        if args.template:
            client.upload_template(Path(args.template))
            echo(f"Uploaded template {args.template}")
        echo(
            f"Uploaded {uploaded.filename} ({uploaded.chunks_count} chunks) "
            f"into session {session_id}"
        )

        job_id = client.start_chat_job(session_id, args.instruction)
        echo(f"Started job {job_id}")

        snapshot: JobSnapshot | None = None
        for round_number in range(1, MAX_APPROVAL_ROUNDS + 1):
            snapshot = poll_until_gate(client, job_id, echo=echo)
            echo(f"--- round {round_number}: status={snapshot.status} ---")
            if snapshot.status in _TERMINAL_STATUSES:
                break
            decisions = decide_changes(
                snapshot.pending_changes,
                ask=ask,
                echo=echo,
                auto_approve=args.approve_all,
            )
            client.submit_decisions(session_id, job_id, decisions)
            approved_count = sum(1 for d in decisions if d.approved)
            denied_count = len(decisions) - approved_count
            echo(f"Submitted {approved_count} approved / {denied_count} denied")
        else:
            err(
                f"error: exceeded maximum of {MAX_APPROVAL_ROUNDS} "
                "approval rounds"
            )
            return 1

        if snapshot is None:
            err("error: job polling produced no snapshot")
            return 1
        if snapshot.status != "completed":
            message = f"error: Job ended with status '{snapshot.status}'"
            if snapshot.error:
                message += f": {snapshot.error}"
            err(message)
            return 1

        out_path = (
            Path(args.output)
            if args.output
            else Path(args.draft).with_name(
                f"{Path(args.draft).stem}-edited.{args.format}"
            )
        )
        output_format: ExportFormat = parse_export_format(args.format)
        written = client.export_document(session_id, output_format, out_path)
        echo(f"Exported to {written}")
        return 0
    except (SuperDocsError, TimeoutError, OSError, httpx.HTTPError) as exc:
        err(f"error: {exc}")
        return 1


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the demo-driver argument parser."""
    parser = argparse.ArgumentParser(
        prog="superdocs-demo",
        description=(
            "Upload a draft, run a reformat job, review proposed changes "
            "(interactively or via --approve-all), and export the result."
        ),
    )
    parser.add_argument("draft", help="Path to the draft document to upload")
    parser.add_argument(
        "instruction",
        help="Reformat instruction sent verbatim to the chat job",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Optional template file to upload before starting the job",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path (default: <draft stem>-edited.<format>)",
    )
    parser.add_argument(
        "--format",
        choices=("docx", "pdf", "html"),
        default="docx",
        help="Export format (default: docx)",
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="Approve every proposed change without interactive prompts",
    )
    parser.add_argument(
        "--env",
        default=DEFAULT_ENV_PATH,
        help=f"Untracked dotenv file holding {API_KEY_ENV_VAR} "
        f"(default: {DEFAULT_ENV_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    try:
        args = build_parser().parse_args(argv)
        try:
            api_key = load_api_key(Path(args.env))
        except SuperDocsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        client = SuperDocsClient(api_key=api_key)
        return run(
            args,
            client=client,
            ask=input,
            echo=print,
            err=lambda message: print(message, file=sys.stderr),
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
