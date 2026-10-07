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
import readline
import select
import shlex
import subprocess
import sys
import termios
import threading
import time
import tty
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass


SLASH_COMMANDS = (
    ("/help", "Show this list"),
    ("/status", "Show model, workspace, context, and Ollama load state"),
    ("/stats", "Show last-turn and current-session usage"),
    ("/reset", "Clear conversation and stats; keep workspace instructions"),
    ("/exit", "Leave BopBop"),
)


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
            "selected": "1;30;46",
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
        print(self.ink("  /help commands  ·  /exit quit  ·  tools run as your user", "muted"), flush=True)

    def answer(self, value: str) -> None:
        print("\n" + self.ink("◆ answer", "ok"))
        print(value or self.ink("(empty response)", "error"), flush=True)


def use_color(option: str) -> bool:
    if option == "never" or "NO_COLOR" in os.environ:
        return False
    if option == "always":
        return True
    return sys.stdout.isatty() and sys.stderr.isatty() and os.environ.get("TERM") != "dumb"


def _escape_key(fd: int) -> str:
    """Read a short terminal escape sequence, or treat bare Esc as dismiss."""
    if not select.select([fd], [], [], 0.05)[0]:
        return "escape"
    second = os.read(fd, 1)
    if second != b"[":
        return "escape"
    if not select.select([fd], [], [], 0.05)[0]:
        return "escape"
    third = os.read(fd, 1)
    return {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}.get(third, "escape")


def _slash_prompt(fd: int, prompt: str, display: Display) -> str | None:
    """Edit a slash command below the prompt; None returns to normal input."""
    buffer = "/"
    cursor = 1
    selected = 0
    menu_visible = True
    prompt_width = len("bopbop> ")

    def matches() -> list[tuple[str, str]]:
        return [(name, description) for name, description in SLASH_COMMANDS
                if name.startswith(buffer.lower())] if buffer.startswith("/") else []

    def render() -> None:
        found = matches() if menu_visible else []
        rows = []
        if menu_visible and buffer.startswith("/"):
            if found:
                for index, (name, description) in enumerate(found):
                    marker = "▸" if index == selected else " "
                    style = "selected" if index == selected else "strong"
                    rows.append(f"  {display.ink(marker + ' ' + name.ljust(9), style)}"
                                f"  {display.ink(description, 'muted')}")
            else:
                rows.append("  " + display.ink("No matching commands", "muted"))
        sys.stdout.write("\r\x1b[J" + prompt + buffer)
        if rows:
            sys.stdout.write("\r\n" + "\r\n".join(rows))
            sys.stdout.write(f"\x1b[{len(rows)}A")
        sys.stdout.write("\r" + f"\x1b[{prompt_width + cursor}C")
        sys.stdout.flush()

    def finish(value: str) -> str:
        sys.stdout.write("\r\x1b[J" + prompt + value + "\r\n")
        sys.stdout.flush()
        return value

    render()
    while True:
        key = os.read(fd, 1)
        if not key:
            raise EOFError
        if key in (b"\r", b"\n"):
            found = matches() if menu_visible else []
            chosen = found[selected][0] if found else buffer
            return finish(chosen)
        if key == b"\x03":
            sys.stdout.write("\r\x1b[J" + prompt + "^C\r\n")
            sys.stdout.flush()
            raise KeyboardInterrupt
        if key == b"\x04" and not buffer:
            raise EOFError
        if key in (b"\x7f", b"\x08"):
            if cursor:
                buffer = buffer[:cursor - 1] + buffer[cursor:]
                cursor -= 1
                selected = 0
                if not buffer:
                    return None
        elif key == b"\t":
            found = matches()
            if found:
                buffer = found[selected][0]
                cursor = len(buffer)
                menu_visible = True
        elif key == b"\x1b":
            action = _escape_key(fd)
            found = matches() if menu_visible else []
            if action == "up" and found:
                selected = (selected - 1) % len(found)
            elif action == "down" and found:
                selected = (selected + 1) % len(found)
            elif action == "left":
                cursor = max(0, cursor - 1)
            elif action == "right":
                cursor = min(len(buffer), cursor + 1)
            elif action == "escape":
                menu_visible = False
        elif key == b"\x01":
            cursor = 0
        elif key == b"\x05":
            cursor = len(buffer)
        elif 32 <= key[0] < 127:
            buffer = buffer[:cursor] + key.decode("ascii") + buffer[cursor:]
            cursor += 1
            selected = 0
        render()


def prompt_line(display: Display) -> str:
    """Read ordinary text with readline; show a menu as soon as `/` is typed."""
    prompt = display.ink("bopbop", "brand") + "> "
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return input(prompt)
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        tty.setraw(fd)
        first = os.read(fd, 1)
        if first == b"/":
            slash_result = _slash_prompt(fd, prompt, display)
        else:
            slash_result = None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
    if first == b"/" and slash_result is not None:
        return slash_result
    if first in (b"\r", b"\n"):
        sys.stdout.write("\r\n")
        return ""
    if first == b"\x03":
        sys.stdout.write("^C\r\n")
        raise KeyboardInterrupt
    if first == b"\x04" or not first:
        raise EOFError
    initial = "" if first == b"/" else first.decode("utf-8", errors="replace")
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()
    if initial:
        readline.set_startup_hook(lambda: readline.insert_text(initial))
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook(None)


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


@dataclass
class TurnStats:
    wall_s: float = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    generation_s: float = 0


@dataclass
class SessionStats:
    last: TurnStats | None = None
    turns: int = 0
    totals: TurnStats | None = None

    def record(self, turn: TurnStats) -> None:
        self.last = turn
        self.turns += 1
        if self.totals is None:
            self.totals = TurnStats()
        for field in ("wall_s", "model_calls", "tool_calls", "input_tokens",
                      "output_tokens", "cached_tokens", "generation_s"):
            setattr(self.totals, field, getattr(self.totals, field) + getattr(turn, field))


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
    host = os.environ.get("BOPBOP_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    request = urllib.request.Request(f"{host}/api/chat",
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


def show_help(display: Display) -> None:
    print()
    display.event("◆ commands", style="brand")
    for command, description in SLASH_COMMANDS:
        display.event(f"  {command:<8}", description, "strong")


def show_status(model: str, context: int, workspace: pathlib.Path,
                messages: list[dict], display: Display) -> None:
    print()
    display.event("◆ status", style="brand")
    display.event("  model", model, "strong")
    display.event("  workspace", str(workspace), "strong")
    display.event("  context", f"{context:,} tokens", "strong")
    display.event("  history", f"{len(messages) - 1} messages after system instructions", "strong")
    try:
        host = os.environ.get("BOPBOP_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as response:
            loaded = json.load(response).get("models", [])
    except (OSError, ValueError, urllib.error.URLError) as exc:
        display.event("  Ollama", f"unreachable: {exc}", "error")
        return
    active = next((item for item in loaded if item.get("name") == model
                   or item.get("model") == model), None)
    if active is None:
        display.event("  Ollama", "reachable · model not loaded", "muted")
        return
    size_gib = (active.get("size") or 0) / (1024 ** 3)
    vram_gib = (active.get("size_vram") or 0) / (1024 ** 3)
    loaded_context = active.get("context_length")
    details = f"loaded · {size_gib:.1f} GiB resident · {vram_gib:.1f} GiB VRAM"
    if loaded_context:
        details += f" · {loaded_context:,} active context"
    display.event("  Ollama", details, "ok")


def show_session_stats(session: SessionStats, display: Display) -> None:
    print()
    display.event("◆ stats", style="brand")
    if session.last is None or session.totals is None:
        display.event("  no completed turns yet", style="muted")
        return
    for label, usage in (("last", session.last), ("session", session.totals)):
        speed = usage.output_tokens / usage.generation_s if usage.generation_s else 0
        turn_count = (f"{session.turns} {'turn' if session.turns == 1 else 'turns'} · "
                      if label == "session" else "")
        display.event(f"  {label}", (f"{turn_count}{usage.wall_s:.1f}s wall"
            f" · {usage.model_calls} {'model call' if usage.model_calls == 1 else 'model calls'}"
            f" · {usage.tool_calls} {'tool' if usage.tool_calls == 1 else 'tools'}"), "strong")
        display.event("    tokens", (f"{usage.input_tokens:,} in"
            f" · {usage.output_tokens:,} out · {usage.cached_tokens:,} cached"
            f" · {speed:.1f} generation tok/s"))


def handle_command(prompt: str, messages: list[dict], session: SessionStats,
                   model: str, context: int, workspace: pathlib.Path,
                   display: Display) -> bool:
    if not prompt.startswith("/"):
        return False
    command = prompt.split(maxsplit=1)[0].lower()
    if command == "/help":
        show_help(display)
    elif command == "/status":
        show_status(model, context, workspace, messages, display)
    elif command == "/stats":
        show_session_stats(session, display)
    elif command == "/reset":
        messages[:] = messages[:1]
        session.last = None
        session.turns = 0
        session.totals = None
        display.event("↺ reset", "Conversation and stats cleared; workspace instructions kept", "ok")
    else:
        display.event("Unknown command", f"{command} · type /help", "error")
    return True


def turn(model: str, messages: list[dict], workspace: pathlib.Path,
         context: int, display: Display, emit=None) -> tuple[str, TurnStats]:
    turn_started = time.monotonic()
    usage = TurnStats()
    for step in range(12):
        message, stats = chat(model, messages, context, step + 1, display)
        show_stats(stats, context, display)
        if emit:
            emit({"kind": "model", "step": step + 1, "prompt_tokens": stats.prompt_tokens,
                  "cached_tokens": stats.cached_tokens, "output_tokens": stats.output_tokens,
                  "duration_ms": round(stats.wall_s * 1000),
                  "first_chunk_ms": round(stats.first_chunk_s * 1000) if stats.first_chunk_s is not None else None,
                  "load_ms": round(stats.load_s * 1000), "prompt_ms": round(stats.prompt_s * 1000),
                  "generation_ms": round(stats.generation_s * 1000), "stream_chunks": stats.stream_chunks})
        usage.model_calls += 1
        usage.input_tokens += stats.prompt_tokens
        usage.output_tokens += stats.output_tokens
        usage.cached_tokens += stats.cached_tokens
        usage.generation_s += stats.generation_s
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            usage.wall_s = time.monotonic() - turn_started
            display.event("└─ turn", (f"{usage.wall_s:.1f}s"
                f" · {usage.model_calls} model calls · {usage.tool_calls}"
                f" {'tool' if usage.tool_calls == 1 else 'tools'}"
                f" · sum {usage.input_tokens:,} in / {usage.output_tokens:,} out"), "model")
            answer = message.get("content", "").strip()
            if emit:
                emit({"kind": "text", "delta": answer})
                emit({"kind": "done", "duration_ms": round(usage.wall_s * 1000),
                      "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                "cached_tokens": usage.cached_tokens, "model_calls": usage.model_calls,
                                "tool_calls": usage.tool_calls}})
            return answer, usage
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            display.event(f"├─ → {name}", call_label(name, args), "tool")
            if emit:
                emit({"kind": "tool", "name": name, "args": args})
            tool_started = time.monotonic()
            result = execute(workspace, name, args)
            usage.tool_calls += 1
            display.event("│    ← result", (f"{result_preview(result)}"
                f" ({time.monotonic() - tool_started:.2f}s)"),
                "error" if result.startswith("ERROR:") or
                    (result.startswith("exit=") and not result.startswith("exit=0")) else "ok")
            if emit:
                emit({"kind": "tool_result", "name": name, "ok": not result.startswith("ERROR:")
                      and not (result.startswith("exit=") and not result.startswith("exit=0")),
                      "preview": result_preview(result), "duration_ms": round((time.monotonic() - tool_started) * 1000)})
            messages.append({"role": "tool", "tool_name": name, "content": result})
    usage.wall_s = time.monotonic() - turn_started
    if emit:
        emit({"kind": "error", "message": "Stopped after 12 model steps without a final reply."})
    return "Stopped after 12 model steps without a final reply.", usage


def jsonl_session(args, workspace: pathlib.Path) -> None:
    """Server transport for the same local agent loop used by the terminal UI."""
    session_dir = pathlib.Path(os.path.expanduser(os.environ.get(
        "BOPBOP_DATA_DIR", "~/.bopbop/data"))) / "ollama_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir.chmod(0o700)
    session_id = args.session_id or str(uuid.uuid4())
    try:
        session_id = str(uuid.UUID(session_id))
    except ValueError as exc:
        raise ValueError("invalid Ollama session id") from exc
    path = session_dir / f"{session_id}.json"
    messages = [{"role": "system", "content": system_prompt(workspace)}]
    if args.session_id and path.is_file():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("workspace") == str(workspace) and saved.get("model") == args.model:
            messages = saved["messages"]

    def emit(event: dict) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    emit({"kind": "session", "session_id": session_id})
    messages.append({"role": "user", "content": " ".join(args.prompt)})
    class QuietDisplay:
        def event(self, *unused, **kwargs):
            pass
    answer, _ = turn(args.model, messages, workspace, args.context, QuietDisplay(), emit=emit)
    if not answer.startswith("Stopped after 12 model steps"):
        tmp = session_dir / f".{session_id}.{os.getpid()}.tmp"
        with tmp.open("w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump({"workspace": str(workspace), "model": args.model, "messages": messages}, file)
        tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--model", default=os.environ.get("BOPBOP_OLLAMA_MODEL", "gpt-oss:20b"))
    parser.add_argument("--context", type=int, default=int(os.environ.get("BOPBOP_OLLAMA_CONTEXT", "16384")))
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--jsonl", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--session-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error(f"not a directory: {workspace}")
    if args.jsonl:
        if not args.prompt:
            parser.error("--jsonl requires a prompt")
        jsonl_session(args, workspace)
        return
    display = Display(use_color(args.color))
    messages = [{"role": "system", "content": system_prompt(workspace)}]
    session = SessionStats()
    display.header(args.model, args.context, workspace)
    if args.prompt:
        prompts = [" ".join(args.prompt)]
    else:
        def interactive_prompts():
            while True:
                try:
                    yield prompt_line(display)
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    return
        prompts = interactive_prompts()
    for prompt in prompts:
        prompt = prompt.strip()
        if not prompt:
            continue
        if prompt == "/exit":
            break
        if handle_command(prompt, messages, session, args.model, args.context,
                          workspace, display):
            continue
        messages.append({"role": "user", "content": prompt})
        answer, usage = turn(args.model, messages, workspace, args.context, display)
        session.record(usage)
        display.answer(answer)


if __name__ == "__main__":
    main()
