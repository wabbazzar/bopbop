#!/usr/bin/env python3
"""Experimental BopBop terminal agent using Ollama's native tool calls.

No Codex/Claude harness or extra Python packages. Runs commands as the current
user, so point --workspace at a directory you trust.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass


def tool(name: str, description: str, properties: dict, required: list[str]):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [
    tool("list_files", "List files in the working directory, optionally under a relative subdirectory",
         {"path": {"type": "string"}}, []),
    tool("read_file", "Read a UTF-8 file under the working directory",
         {"path": {"type": "string"}}, ["path"]),
    tool("write_file", "Replace a UTF-8 file under the working directory with exact content",
         {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    tool("run_command", "Run an argv command in the working directory, without a shell. Commands have the current user's permissions.",
         {"command": {"type": "string"}}, ["command"]),
]


def confined(workspace: pathlib.Path, rel: str) -> pathlib.Path:
    path = (workspace / rel).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("path is outside workspace")
    return path


def execute(workspace: pathlib.Path, name: str, args: dict) -> str:
    try:
        if name == "list_files":
            path = confined(workspace, args.get("path") or ".")
            if not path.is_dir():
                return "ERROR: not a directory"
            return "\n".join(p.name + ("/" if p.is_dir() else "") for p in sorted(path.iterdir()))[:8000]
        if name == "read_file":
            return confined(workspace, args["path"]).read_text(encoding="utf-8")[:16000]
        if name == "write_file":
            path = confined(workspace, args["path"])
            if not path.parent.is_dir():
                return "ERROR: parent directory does not exist"
            path.write_text(args["content"], encoding="utf-8")
            return "written"
        if name == "run_command":
            argv = shlex.split(args["command"])
            if not argv:
                return "ERROR: empty command"
            proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30)
            return f"exit={proc.returncode}\n{(proc.stdout + proc.stderr)[-12000:]}"
        return f"ERROR: unknown tool {name}"
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        return f"ERROR: {exc}"


@dataclass
class ModelStats:
    wall_s: float
    first_chunk_s: float | None
    load_s: float
    prompt_tokens: int
    cached_tokens: int
    output_tokens: int
    generation_s: float
    prompt_s: float
    stream_chunks: int
    thinking_chars: int


def _seconds(value: int | None) -> float:
    return (value or 0) / 1_000_000_000


def _activity(stop: threading.Event, started: float, progress: dict, tty: bool) -> None:
    if not tty:
        return
    frames = "|/-\\"
    frame = 0
    while not stop.wait(0.2):
        elapsed = time.monotonic() - started
        line = (f"\r  {frames[frame % len(frames)]} model {elapsed:5.1f}s"
                f" · {progress['chunks']} stream chunks"
                f" · {progress['thinking']} thinking chars")
        print(line, end="", file=sys.stderr, flush=True)
        frame += 1
    print("\r\033[K", end="", file=sys.stderr, flush=True)


def chat(model: str, messages: list[dict], context: int, step: int) -> tuple[dict, ModelStats]:
    payload = {"model": model, "messages": messages, "tools": TOOLS,
               "stream": True, "think": False,
               "options": {"num_ctx": context, "temperature": 0}, "keep_alive": "10m"}
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    progress = {"chunks": 0, "thinking": 0}
    stop = threading.Event()
    ticker = threading.Thread(target=_activity,
        args=(stop, started, progress, sys.stderr.isatty()), daemon=True)
    print(f"  model step {step} · waiting for {model}", file=sys.stderr, flush=True)
    ticker.start()
    content, thinking, calls = [], [], []
    first_chunk = None
    final = {}
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            for line in response:
                event = json.loads(line)
                if first_chunk is None:
                    first_chunk = time.monotonic() - started
                piece = event.get("message") or {}
                if piece.get("content"):
                    content.append(piece["content"])
                if piece.get("thinking"):
                    thinking.append(piece["thinking"])
                    progress["thinking"] += len(piece["thinking"])
                if piece.get("tool_calls"):
                    # Ollama may send a complete call in the final chunk.
                    calls = piece["tool_calls"]
                progress["chunks"] += 1
                if event.get("done"):
                    final = event
                    break
    finally:
        stop.set()
        ticker.join(timeout=1)
    wall = time.monotonic() - started
    if not final:
        raise RuntimeError("Ollama stream ended before the final usage record")
    message = {"role": "assistant", "content": "".join(content)}
    if thinking:
        message["thinking"] = "".join(thinking)
    if calls:
        message["tool_calls"] = calls
    stats = ModelStats(wall, first_chunk, _seconds(final.get("load_duration")),
        final.get("prompt_eval_count") or 0,
        final.get("prompt_eval_cached_count") or 0,
        final.get("eval_count") or 0,
        _seconds(final.get("eval_duration")),
        _seconds(final.get("prompt_eval_duration")),
        progress["chunks"], progress["thinking"])
    return message, stats


def show_stats(stats: ModelStats, context: int) -> None:
    speed = stats.output_tokens / stats.generation_s if stats.generation_s else 0
    first = f"{stats.first_chunk_s:.1f}s" if stats.first_chunk_s is not None else "?"
    print((f"  tokens  in {stats.prompt_tokens:,}/{context:,}"
           f" (cached {stats.cached_tokens:,}) · out {stats.output_tokens:,}"
           f" · {speed:.1f} tok/s"), file=sys.stderr)
    print((f"  timing  {stats.wall_s:.1f}s wall · first chunk {first}"
           f" · load {stats.load_s:.1f}s · prompt {stats.prompt_s:.1f}s"
           f" · generate {stats.generation_s:.1f}s"), file=sys.stderr, flush=True)
    print((f"  stream  {stats.stream_chunks} chunks"
           f" · {stats.thinking_chars:,} thinking chars"), file=sys.stderr, flush=True)


def call_label(name: str, args: dict) -> str:
    if name in {"read_file", "write_file", "list_files"}:
        label = args.get("path") or "."
        if name == "write_file":
            label += f" ({len(args.get('content', '')):,} chars)"
        return label
    if name == "run_command":
        return args.get("command", "")
    return json.dumps(args, ensure_ascii=False)[:160]


def result_preview(result: str) -> str:
    lines = result.strip().splitlines()
    preview = " | ".join(lines[:3])[:220]
    if len(lines) > 3 or len(preview) < len(result.strip()):
        preview += " …"
    return preview


def system_prompt(workspace: pathlib.Path) -> str:
    parts = ["You are BopBop, a local assistant. Check files or commands before claiming facts. "
             "Use tools when needed. For code changes, run a relevant test. Give a concise final reply. "
             "You are in a terminal session. Never claim a tool action succeeded unless its result confirms it."]
    for filename in ("AGENTS.md", "CLAUDE.md", "personality.md"):
        path = workspace / filename
        if path.is_file():
            parts.append(f"\n--- {filename} ---\n{path.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def turn(model: str, messages: list[dict], workspace: pathlib.Path, context: int) -> str:
    turn_started = time.monotonic()
    total_in = total_out = tool_count = 0
    for step in range(12):
        message, stats = chat(model, messages, context, step + 1)
        show_stats(stats, context)
        total_in += stats.prompt_tokens
        total_out += stats.output_tokens
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            print((f"  turn    {time.monotonic() - turn_started:.1f}s total"
                   f" · {step + 1} model calls · {tool_count} tool calls"
                   f" · summed {total_in:,} input + {total_out:,} output tokens"),
                  file=sys.stderr, flush=True)
            return message.get("content", "").strip()
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            print(f"  → {name}  {call_label(name, args)}", file=sys.stderr, flush=True)
            tool_started = time.monotonic()
            result = execute(workspace, name, args)
            tool_count += 1
            print(f"  ← {result_preview(result)} ({time.monotonic() - tool_started:.2f}s)",
                  file=sys.stderr, flush=True)
            messages.append({"role": "tool", "tool_name": name, "content": result})
    return "Stopped after 12 model steps without a final reply."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--context", type=int, default=16384)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error(f"not a directory: {workspace}")
    messages = [{"role": "system", "content": system_prompt(workspace)}]
    print(f"BopBop local · {args.model} · {args.context:,} token context", flush=True)
    print(f"Workspace: {workspace}", flush=True)
    print("Tools: list_files, read_file, write_file, run_command", flush=True)
    print("Type /exit to quit.\n", flush=True)
    if args.prompt:
        prompts = [" ".join(args.prompt)]
    else:
        prompts = iter(lambda: input("bopbop> "), "/exit")
    for prompt in prompts:
        if not prompt.strip():
            continue
        messages.append({"role": "user", "content": prompt})
        print(turn(args.model, messages, workspace, args.context), flush=True)


if __name__ == "__main__":
    main()
