"""Spawn `claude` CLI subprocess in BOPBOP_CONTEXT_DIR, parse stream-json events,
yield BopBop-shaped events for the channel handlers."""

import asyncio
import json
import os
from typing import AsyncIterator

CLAUDE_BIN = os.environ.get("BOPBOP_CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("BOPBOP_CLAUDE_MODEL", "sonnet")
CONTEXT_DIR = os.path.expanduser(
    os.environ.get("BOPBOP_CONTEXT_DIR", "~/.bopbop/context")
)


async def run_turn(
    prompt: str, resume_session_id: str | None = None
) -> AsyncIterator[dict]:
    """Stream events for one user turn.

    If `resume_session_id` is provided, claude resumes that session (full
    in-memory context preserved). Otherwise a fresh session starts.

    Yields dicts of shape:
      {"kind": "session", "session_id": "..."}    -- emitted once at start
      {"kind": "text", "delta": "..."}
      {"kind": "tool", "name": "...", "args": {...}}
      {"kind": "tool_result", "name": "...", "ok": bool}
      {"kind": "done", "cost_usd": float, "duration_ms": int}
      {"kind": "error", "message": "..."}
    """
    args = [
        CLAUDE_BIN,
        "-p",
        "--model",
        CLAUDE_MODEL,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    args.append(prompt)

    # Default asyncio StreamReader buffer is 64KB. claude's stream-json emits
    # tool_result events on a single line; when the Read tool returns an image,
    # the base64-encoded data URL easily exceeds that. Bump to 64MB so a single
    # large image attachment doesn't crash the subprocess reader with
    # LimitOverrunError.
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=CONTEXT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024 * 1024,
    )

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

            etype = ev.get("type")

            if etype == "system":
                sid = ev.get("session_id")
                if sid:
                    yield {"kind": "session", "session_id": sid}

            elif etype == "assistant":
                msg = ev.get("message", {})
                for block in msg.get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            yield {"kind": "text", "delta": text}
                    elif btype == "tool_use":
                        yield {
                            "kind": "tool",
                            "name": block.get("name"),
                            "args": block.get("input"),
                        }

            elif etype == "user":
                # Tool results echoed back from claude's tool_result blocks
                msg = ev.get("message", {})
                for block in msg.get("content", []) or []:
                    if block.get("type") == "tool_result":
                        yield {
                            "kind": "tool_result",
                            "name": block.get("tool_use_id"),
                            "ok": not block.get("is_error", False),
                        }

            elif etype == "result":
                yield {
                    "kind": "done",
                    "cost_usd": ev.get("total_cost_usd"),
                    "duration_ms": ev.get("duration_ms"),
                }

        rc = await proc.wait()
        if rc != 0:
            stderr_bytes = await proc.stderr.read() if proc.stderr else b""
            yield {
                "kind": "error",
                "message": f"claude exited {rc}: {stderr_bytes.decode('utf-8', errors='replace')[:500]}",
            }

    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
