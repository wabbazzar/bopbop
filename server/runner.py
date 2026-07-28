"""Spawn the configured agent CLI and normalize its JSONL into BopBop events.

Supported harnesses:
  - Claude Code (``claude -p --output-format stream-json``)
  - Codex CLI (``codex exec --json``)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator

CONTEXT_DIR = os.path.expanduser(
    os.environ.get("BOPBOP_CONTEXT_DIR", "~/.bopbop/context")
)
SUPPORTED_HARNESSES = {"claude", "codex"}


def agent_harness() -> str:
    harness = os.environ.get("BOPBOP_AGENT_HARNESS", "claude").strip().lower()
    if harness not in SUPPORTED_HARNESSES:
        raise ValueError(
            f"unsupported BOPBOP_AGENT_HARNESS={harness!r}; "
            f"expected one of {sorted(SUPPORTED_HARNESSES)}"
        )
    return harness


def agent_model(harness: str | None = None) -> str | None:
    harness = harness or agent_harness()
    if harness == "codex":
        # Empty means "use the authenticated Codex installation's default".
        return os.environ.get("BOPBOP_CODEX_MODEL", "").strip() or None
    return os.environ.get("BOPBOP_CLAUDE_MODEL", "sonnet").strip() or "sonnet"


def _build_args(
    prompt: str, resume_session_id: str | None = None
) -> tuple[str, list[str]]:
    harness = agent_harness()
    model = agent_model(harness)

    if harness == "codex":
        binary = os.environ.get("BOPBOP_CODEX_BIN", "codex")
        prompt = (
            "This project may carry shared cross-harness instructions in "
            "CLAUDE.md and BopBop personality/context files. Read and follow "
            "CLAUDE.md and context/personality.md when present, in addition "
            "to AGENTS.md.\n\n"
            + prompt
        )
        if resume_session_id:
            args = [
                binary,
                "exec",
                "resume",
                "--json",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
            ]
        else:
            args = [
                binary,
                "exec",
                "--json",
                "--color",
                "never",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
            ]
        if model:
            args.extend(["--model", model])
        if resume_session_id:
            args.append(resume_session_id)
        args.append(prompt)
        return harness, args

    binary = os.environ.get("BOPBOP_CLAUDE_BIN", "claude")
    args = [
        binary,
        "-p",
        "--model",
        model or "sonnet",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    args.append(prompt)
    return harness, args


def _claude_events(ev: dict) -> list[dict]:
    out: list[dict] = []
    etype = ev.get("type")

    if etype == "system":
        sid = ev.get("session_id")
        if sid:
            out.append({"kind": "session", "session_id": sid})

    elif etype == "assistant":
        msg = ev.get("message", {})
        for block in msg.get("content", []) or []:
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                out.append({"kind": "text", "delta": block["text"]})
            elif btype == "tool_use":
                out.append(
                    {
                        "kind": "tool",
                        "name": block.get("name"),
                        "args": block.get("input"),
                    }
                )

    elif etype == "user":
        msg = ev.get("message", {})
        for block in msg.get("content", []) or []:
            if block.get("type") == "tool_result":
                out.append(
                    {
                        "kind": "tool_result",
                        "name": block.get("tool_use_id"),
                        "ok": not block.get("is_error", False),
                    }
                )

    elif etype == "result":
        out.append(
            {
                "kind": "done",
                "cost_usd": ev.get("total_cost_usd"),
                "duration_ms": ev.get("duration_ms"),
            }
        )
    return out


def _codex_events(ev: dict, elapsed_ms: int) -> list[dict]:
    out: list[dict] = []
    etype = ev.get("type")
    item = ev.get("item") or {}
    item_type = item.get("type")

    if etype == "thread.started" and ev.get("thread_id"):
        out.append({"kind": "session", "session_id": ev["thread_id"]})

    elif etype == "item.started" and item_type != "agent_message":
        out.append(
            {
                "kind": "tool",
                "name": item_type,
                "args": {
                    key: value
                    for key, value in item.items()
                    if key not in {"id", "type", "status"}
                },
            }
        )

    elif etype == "item.completed":
        # Agent messages are buffered by run_turn: Codex emits commentary and
        # final messages with the same item type, and Signal should receive
        # only the last/final one.
        if item_type != "agent_message":
            out.append(
                {
                    "kind": "tool_result",
                    "name": item.get("id") or item_type,
                    "ok": item.get("status") not in {"failed", "error"},
                }
            )

    elif etype == "turn.completed":
        out.append(
            {
                "kind": "done",
                "cost_usd": None,
                "duration_ms": elapsed_ms,
                "usage": ev.get("usage") or {},
            }
        )

    elif etype in {"turn.failed", "error"}:
        message = ev.get("message")
        if not message and isinstance(ev.get("error"), dict):
            message = ev["error"].get("message")
        out.append(
            {
                "kind": "error",
                "message": message or f"codex emitted {etype}",
            }
        )
    return out


async def run_turn(
    prompt: str, resume_session_id: str | None = None
) -> AsyncIterator[dict]:
    """Run one turn and yield harness-neutral BopBop events."""
    harness, args = _build_args(prompt, resume_session_id)
    started = time.monotonic()

    # A single Claude image result can exceed asyncio's 64KB default line
    # limit. Codex JSONL can also contain large tool payloads.
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=CONTEXT_DIR,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024 * 1024,
    )

    emitted_error = False
    codex_last_message: str | None = None
    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            if (
                harness == "codex"
                and ev.get("type") == "item.completed"
                and (ev.get("item") or {}).get("type") == "agent_message"
            ):
                text = (ev.get("item") or {}).get("text")
                if text:
                    codex_last_message = text
                continue
            if (
                harness == "codex"
                and ev.get("type") == "turn.completed"
                and codex_last_message
            ):
                yield {"kind": "text", "delta": codex_last_message}
                codex_last_message = None

            normalized = (
                _codex_events(ev, elapsed_ms)
                if harness == "codex"
                else _claude_events(ev)
            )
            for item in normalized:
                emitted_error = emitted_error or item.get("kind") == "error"
                yield item

        rc = await proc.wait()
        if rc != 0 and not emitted_error:
            stderr_bytes = await proc.stderr.read() if proc.stderr else b""
            detail = stderr_bytes.decode("utf-8", errors="replace")[:500]
            yield {
                "kind": "error",
                "message": f"{harness} exited {rc}: {detail}",
            }

    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
