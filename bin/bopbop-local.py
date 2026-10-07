#!/usr/bin/env python3
"""Experimental BopBop terminal agent using Ollama's native tool calls.

No Codex/Claude harness or extra Python packages. Runs commands as the current
user, so point --workspace at a directory you trust.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass


class Display:
    """Small ANSI presentation layer; plain text when color is unavailable."""

    def __init__(self, color: bool):
        self.color = color

    def ink(self, value: str, style: str) -> str:
        if not self.color:
            return str(value)
        codes = {
            "brand": "1;36", "model": "36", "tool": "1;33",
            "ok": "1;32", "error": "1;31", "muted": "90", "strong": "1",
        }
        return f"\x1b[{codes[style]}m{value}\x1b[0m"

    def event(self, label: str, detail: str = "", style: str = "muted") -> None:
        print(f"  {self.ink(label, style)}{('  ' + detail) if detail else ''}",
              file=sys.stderr, flush=True)

    def header(self, model: str, context: int, workspace: pathlib.Path) -> None:
        print(self.ink("● bopbop", "brand") + self.ink("  LOCAL AGENT", "muted"))
        print(f"  {self.ink(model, 'strong')}  {self.ink('·', 'muted')}"
              f"  {context:,} token context")
        print(f"  {self.ink('cwd', 'muted')}  {workspace}")
        print(self.ink("  ───────────────────────────────────────────────", "muted"))
        print(self.ink("  /exit to quit  ·  tools run as your user", "muted"), flush=True)

    def answer(self, value: str) -> None:
        print("\n" + self.ink("◆ answer", "ok"))
        print(value or self.ink("(empty response)", "error"), flush=True)


def use_color(option: str) -> bool:
    if option == "never" or "NO_COLOR" in os.environ:
        return False
    if option == "always":
        return True
    return sys.stdout.isatty() and sys.stderr.isatty() and os.environ.get("TERM") != "dumb"


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


def _activity(stop: threading.Event, started: float, progress: dict,
              tty: bool, display: Display) -> None:
    if not tty:
        return
    frames = "|/-\\"
    frame = 0
    while not stop.wait(0.2):
        elapsed = time.monotonic() - started
        line = (f"\r  {display.ink(frames[frame % len(frames)], 'model')}"
                f" {elapsed:5.1f}s"
                f" {display.ink('·', 'muted')} {progress['chunks']}"
                f" {'chunk' if progress['chunks'] == 1 else 'chunks'}"
                f" {display.ink('·', 'muted')} {progress['thinking']} thinking chars")
        print(line, end="", file=sys.stderr, flush=True)
        frame += 1
    print("\r\033[K", end="", file=sys.stderr, flush=True)


def chat(model: str, messages: list[dict], context: int, step: int,
         display: Display) -> tuple[dict, ModelStats]:
    payload = {"model": model, "messages": messages, "tools": TOOLS,
               "stream": True, "think": False,
               "options": {"num_ctx": context, "temperature": 0}, "keep_alive": "10m"}
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    progress = {"chunks": 0, "thinking": 0}
    stop = threading.Event()
    ticker = threading.Thread(target=_activity,
        args=(stop, started, progress, sys.stderr.isatty(), display), daemon=True)
    display.event(f"◇ model {step:02d}", f"{model} · generating", "model")
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


def show_stats(stats: ModelStats, context: int, display: Display) -> None:
    speed = stats.output_tokens / stats.generation_s if stats.generation_s else 0
    first = f"{stats.first_chunk_s:.1f}s" if stats.first_chunk_s is not None else "?"
    display.event("  tokens", (f"{stats.prompt_tokens:,}/{context:,} in"
        f" · {stats.output_tokens:,} out · {speed:.1f} tok/s"
        f" · {stats.cached_tokens:,} cached"))
    display.event("  timing", (f"{stats.wall_s:.1f}s wall · first {first}"
        f" · load {stats.load_s:.1f}s · prompt {stats.prompt_s:.1f}s"
        f" · generate {stats.generation_s:.1f}s"))
    display.event("  stream", (f"{stats.stream_chunks} chunks"
        f" · {stats.thinking_chars:,} thinking chars"))


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


def turn(model: str, messages: list[dict], workspace: pathlib.Path,
         context: int, display: Display) -> str:
    turn_started = time.monotonic()
    total_in = total_out = tool_count = 0
    for step in range(12):
        message, stats = chat(model, messages, context, step + 1, display)
        show_stats(stats, context, display)
        total_in += stats.prompt_tokens
        total_out += stats.output_tokens
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            display.event("└─ turn", (f"{time.monotonic() - turn_started:.1f}s"
                f" · {step + 1} model calls · {tool_count}"
                f" {'tool' if tool_count == 1 else 'tools'}"
                f" · sum {total_in:,} in / {total_out:,} out"), "model")
            return message.get("content", "").strip()
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            display.event(f"├─ → {name}", call_label(name, args), "tool")
            tool_started = time.monotonic()
            result = execute(workspace, name, args)
            tool_count += 1
            display.event("│    ← result", (f"{result_preview(result)}"
                f" ({time.monotonic() - tool_started:.2f}s)"),
                "error" if result.startswith("ERROR:") or
                    (result.startswith("exit=") and not result.startswith("exit=0")) else "ok")
            messages.append({"role": "tool", "tool_name": name, "content": result})
    return "Stopped after 12 model steps without a final reply."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--context", type=int, default=16384)
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error(f"not a directory: {workspace}")
    display = Display(use_color(args.color))
    messages = [{"role": "system", "content": system_prompt(workspace)}]
    display.header(args.model, args.context, workspace)
    if args.prompt:
        prompts = [" ".join(args.prompt)]
    else:
        prompts = iter(lambda: input(display.ink("bopbop", "brand") + "> "), "/exit")
    for prompt in prompts:
        if not prompt.strip():
            continue
        messages.append({"role": "user", "content": prompt})
        display.answer(turn(args.model, messages, workspace, args.context, display))


if __name__ == "__main__":
    main()
