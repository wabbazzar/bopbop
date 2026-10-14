"""Standard-library line editor for the plain terminal; no model execution."""
from __future__ import annotations

import json
import os
import readline
import select
import shutil
import sys
import termios
import tty

from bopbop_tool_ui import (argument_focus, call_help_lines, call_tool_name,
                            clip_line, command_matches, draft_view, example_arguments,
                            find_tool, reference_lines, safe_text)


def read_escape_key(fd: int) -> str:
    """Read a short terminal escape sequence, or treat bare Esc as dismiss."""
    if not select.select([fd], [], [], 0.05)[0]:
        return "escape"
    second = os.read(fd, 1)
    if second == b"O":
        if not select.select([fd], [], [], 0.05)[0]:
            return "ignore"
        return {b"P": "help", b"H": "home", b"F": "end"}.get(os.read(fd, 1), "ignore")
    if second != b"[":
        return "ignore"
    sequence = b""
    while len(sequence) < 8 and select.select([fd], [], [], 0.05)[0]:
        part = os.read(fd, 1)
        if not part:
            break
        sequence += part
        if sequence != b"[" and 64 <= part[0] <= 126:
            break
    return {b"A": "up", b"B": "down", b"C": "right", b"D": "left",
            b"5~": "page_up", b"6~": "page_down", b"11~": "help", b"[A": "help",
            b"H": "home", b"1~": "home", b"F": "end", b"4~": "end",
            b"3~": "delete"}.get(sequence, "ignore")


def _edit_line(fd, prompt, display, commands, runtime, initial="") -> str:
    """Edit a line with tool help, command menus and shared session history."""
    buffer = initial
    cursor = len(buffer)
    selected = 0
    menu_visible = True
    prompt_width = len("bopbop> ")
    help_expanded = False
    help_hidden = False
    help_offset = 0
    help_page = 1
    active_tool = None
    last_size = None
    last_focus = None
    history = [readline.get_history_item(index) for index in range(1, readline.get_current_history_length() + 1)]
    history_index = len(history)
    history_edits = {}

    def matches():
        return command_matches(buffer, commands, runtime)

    def render() -> None:
        nonlocal active_tool, help_hidden, help_offset, help_expanded, help_page, last_size, last_focus
        columns, height = shutil.get_terminal_size((90, 30))
        last_size = (columns, height)
        name = call_tool_name(runtime, buffer)
        if name != active_tool:
            active_tool = name
            help_hidden = False
            help_offset = 0
            help_expanded = False
        focus = argument_focus(buffer, cursor) if name else None
        if focus != last_focus:
            help_offset = 0
            last_focus = focus
        found = matches() if menu_visible else []
        rows = []
        editing_arguments = buffer.startswith("/call ") and " " in buffer[len("/call "):]
        if active_tool and not help_hidden:
            width = max(1, columns - 3)
            if help_expanded:
                docs = reference_lines(runtime, active_tool, display, width=width)
                if docs and not docs[0]:
                    docs = docs[1:]
            else:
                docs = call_help_lines(runtime, active_tool, display, width, buffer, cursor)
            help_page = max(1, min(height - 3, len(docs), height if help_expanded else 10))
            help_offset = min(help_offset, max(0, len(docs) - help_page))
            rows = ["  " + clip_line(line, width) for line in docs[help_offset:help_offset + help_page]]
            mode = "compact" if help_expanded else "full reference"
            footer = f"F1 {mode} · PgUp/PgDn scroll · Esc hide"
            if len(docs) > help_page:
                footer += f" · {help_offset + 1}–{min(help_offset + help_page, len(docs))}/{len(docs)}"
            rows.append("  " + display.ink(clip_line(footer, width), "muted"))
        elif menu_visible and buffer.startswith("/") and not editing_arguments:
            if found:
                limit = min(7, max(1, height - 4))
                start = max(0, selected - limit + 1)
                for index in range(start, min(len(found), start + limit)):
                    name, description = found[index]
                    marker = "▸" if index == selected else " "
                    style = "selected" if index == selected else "strong"
                    row = (f"  {display.ink(marker + ' ' + name.ljust(9), style)}"
                           f"  {display.ink(safe_text(description), 'muted')}")
                    rows.append(clip_line(row, max(1, columns - 1)))
            else:
                rows.append("  " + display.ink("No matching commands", "muted"))
        visible, cursor_column = draft_view(buffer, cursor, columns - prompt_width - 1)
        sys.stdout.write("\r\x1b[J" + prompt + visible)
        if rows:
            sys.stdout.write("\r\n" + "\r\n".join(rows))
            sys.stdout.write(f"\x1b[{len(rows)}A")
        sys.stdout.write("\r" + f"\x1b[{prompt_width + cursor_column}C")
        sys.stdout.flush()

    def finish(value: str) -> str:
        sys.stdout.write("\r\x1b[J" + prompt + value + "\r\n")
        sys.stdout.flush()
        if value.strip() and (not history or history[-1] != value):
            readline.add_history(value)
            if readline.get_current_history_length() > 500:
                readline.remove_history_item(0)
        return value

    render()
    while True:
        if not select.select([fd], [], [], 0.1)[0]:
            if shutil.get_terminal_size((90, 30)) != last_size:
                render()
            continue
        key = os.read(fd, 1)
        if not key:
            raise EOFError
        if key in (b"\r", b"\n"):
            found = matches() if menu_visible else []
            chosen = found[selected][0] if found else buffer
            if chosen == "/call" and found and runtime:
                buffer, cursor, selected = "/call ", len("/call "), 0
                render()
                continue
            if chosen.startswith("/call ") and found:
                # Insert editable arguments; Enter on a suggestion never executes a tool.
                name = chosen.split()[1]
                definition, _ = find_tool(runtime, name)
                buffer = chosen + " " + json.dumps(example_arguments(definition), ensure_ascii=False)
                cursor = len(buffer)
                menu_visible = False
                render()
                continue
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
        elif key == b"\t":
            found = matches()
            if found:
                buffer = found[selected][0]
                if buffer in {"/call", "/tools"}:
                    buffer += " "
                cursor = len(buffer)
                menu_visible = True
        elif key == b"\x1b":
            action = read_escape_key(fd)
            found = matches() if menu_visible else []
            selecting = bool(found) and not (active_tool and not help_hidden)
            if action == "up" and selecting:
                selected = (selected - 1) % len(found)
            elif action == "down" and selecting:
                selected = (selected + 1) % len(found)
            elif action in {"up", "down"}:
                history_edits[history_index] = (buffer, cursor)
                history_index = max(0, min(len(history), history_index + (-1 if action == "up" else 1)))
                buffer, cursor = history_edits.get(history_index, (
                    history[history_index] if history_index < len(history) else "",
                    len(history[history_index]) if history_index < len(history) else 0))
                selected = 0
                menu_visible = False
                help_hidden = False
            elif action == "left":
                cursor = max(0, cursor - 1)
            elif action == "right":
                cursor = min(len(buffer), cursor + 1)
            elif action == "home":
                cursor = 0
            elif action == "end":
                cursor = len(buffer)
            elif action == "delete":
                buffer = buffer[:cursor] + buffer[cursor + 1:]
                selected = 0
            elif action == "help" and active_tool:
                help_expanded = not help_expanded if not help_hidden else True
                help_hidden = False
                help_offset = 0
            elif action in {"page_up", "page_down"} and active_tool and not help_hidden:
                help_offset = max(0, help_offset + (help_page if action == "page_down" else -help_page))
            elif action == "escape":
                menu_visible = False
                help_hidden = True
        elif key == b"\x01":
            cursor = 0
        elif key == b"\x05":
            cursor = len(buffer)
        elif key == b"\x15":
            buffer, cursor, selected = buffer[cursor:], 0, 0
        elif key == b"\x0b":
            buffer, selected = buffer[:cursor], 0
        elif key == b"\x17":
            start = cursor
            while start and buffer[start - 1].isspace():
                start -= 1
            while start and not buffer[start - 1].isspace():
                start -= 1
            buffer, cursor, selected = buffer[:start] + buffer[cursor:], start, 0
        elif 32 <= key[0] < 127:
            buffer = buffer[:cursor] + key.decode("ascii") + buffer[cursor:]
            cursor += 1
            selected = 0
        elif key[0] >= 128:
            count = 2 if key[0] < 224 else 3 if key[0] < 240 else 4
            raw = bytearray(key)
            while len(raw) < count and select.select([fd], [], [], 0.05)[0]:
                raw.extend(os.read(fd, 1))
            value = raw.decode("utf-8", errors="replace")
            buffer = buffer[:cursor] + value + buffer[cursor:]
            cursor += len(value)
            selected = 0
        render()


def prompt_line(display, commands, runtime=None) -> str:
    """One editor for chat and slash commands, including recalled tool drafts."""
    prompt = display.ink("bopbop", "brand") + "> "
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return input(prompt)
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)
        return _edit_line(fd, prompt, display, commands, runtime)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
