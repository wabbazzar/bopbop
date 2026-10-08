"""Human tool reference and explicit call parsing; no model or handler I/O."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import termios
import textwrap
import tty

from bopbop_packs import strict_json, validate_arguments


WORKSPACE_REFERENCE = {
    "list_files": {
        "documentation": "Omit path to list the workspace root. Paths must remain inside the workspace.",
        "returns": "Sorted names, with / for directories; text capped at 8,000 characters.",
        "examples": [{"arguments": {}}],
    },
    "read_file": {
        "documentation": "Read UTF-8 text. Paths must remain inside the workspace, including symlink targets.",
        "returns": "File text capped at 16,000 characters, or ERROR: followed by the failure.",
        "examples": [{"arguments": {"path": "<path>"}}],
    },
    "write_file": {
        "documentation": "Writes immediately, replacing existing content. The parent directory must exist. Paths must remain inside the workspace.",
        "returns": "written on success, or ERROR: followed by the failure.",
        "examples": [{"arguments": {"path": "<path>", "content": "<content>"}}],
    },
    "run_command": {
        "documentation": "Executes immediately with your permissions. Uses shlex argv splitting without a shell; pipes and shell expansion are not interpreted. Timeout: 30 seconds.",
        "returns": "exit=N followed by the last 12,000 characters of combined stdout/stderr; ERROR: for dispatch failures.",
        "examples": [{"arguments": {"command": "pwd"}}],
    },
}


def tool_entries(runtime):
    entries = []
    for schema in runtime.tools:
        definition = schema["function"]
        name = definition["name"]
        pack = None
        if runtime.registry and name in runtime.registry.handlers:
            pack, definition = runtime.registry.handlers[name]
        if not pack:
            definition = {**definition, **WORKSPACE_REFERENCE.get(name, {})}
        entries.append((definition, pack))
    return entries


def find_tool(runtime, name):
    for definition, pack in tool_entries(runtime):
        if definition["name"] == name:
            return definition, pack
    raise ValueError(f"Tool {name!r} is not enabled. Use /tools to inspect your selection.")


def parse_call(text, runtime):
    """NAME [JSON object] [:: question]; delimiters within JSON are data."""
    if len(text.encode()) > 65536:
        raise ValueError("Tool request exceeds 64 KB")
    pieces = text.strip().split(maxsplit=1)
    if not pieces:
        raise ValueError('Usage: /call TOOL {"argument":"value"} [:: question]')
    name = pieces[0]
    definition, pack = find_tool(runtime, name)
    rest = pieces[1].lstrip() if len(pieces) > 1 else ""
    arguments = {}
    if rest and not rest.startswith("::"):
        # Decode only the JSON prefix, so quoted `::` never splits a value.
        _, end = json.JSONDecoder().raw_decode(rest)
        arguments = strict_json(rest[:end])
        rest = rest[end:].strip()
    question = None
    if rest:
        if not rest.startswith("::") or not rest[2:].strip():
            raise ValueError("After arguments, use :: followed by your question")
        question = rest[2:].strip()
    validate_arguments(definition["parameters"], arguments, pack_protocol=pack is not None)
    return name, arguments, question


def safe_text(value):
    # Pack prose is untrusted terminal content, just as tool output is data.
    return "".join(c if c == "\n" or (ord(c) >= 32 and not 127 <= ord(c) < 160)
                   else f"\\x{ord(c):02x}" for c in str(value))


def clip_line(row, columns):
    """Clip presentation text without counting our ANSI color sequences."""
    visible, pieces = 0, []
    for piece in re.split(r"(\x1b\[[0-9;]*m)", row):
        if piece.startswith("\x1b["):
            pieces.append(piece)
        else:
            available = max(0, columns - visible)
            pieces.append(piece[:available])
            visible += len(piece[:available])
    return "".join(pieces)


def color_json(value, display):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    tokens = r'("(?:[^"\\]|\\.)*")|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    def ink(match):
        style = "ok" if match.group(1) else "model"
        return display.ink(safe_text(match.group()), style)
    return re.sub(tokens, ink, raw)


def example_arguments(definition):
    examples = definition.get("examples", [])
    if examples:
        return examples[0]["arguments"]
    # A template is not executable unless every required value is known.
    values = {}
    props = definition["parameters"].get("properties", {})
    for name in definition["parameters"].get("required", []):
        prop = props[name]
        values[name] = (prop["enum"][0] if prop.get("enum") else
                        prop.get("minimum", 1) if prop["type"] in {"integer", "number"} else
                        False if prop["type"] == "boolean" else f"<{name}>")
    return values


def reference_lines(runtime, name, display, width=None):
    definition, pack = find_tool(runtime, name)
    width = max(32, width or shutil.get_terminal_size((90, 30)).columns - 4)
    lines = []
    def heading(title):
        lines.extend(["", display.ink(title, "brand")])
    def prose(value):
        for paragraph in safe_text(value).splitlines():
            lines.extend(textwrap.wrap(paragraph, width) or [""])
    schema = definition["parameters"]
    required = schema.get("required", [])
    signature = ", ".join(f"{key}: {prop['type']}" + (" (optional)" if key not in required else "")
                          for key, prop in schema.get("properties", {}).items())
    heading(f"◆ {name}")
    prose(f"{name}({signature})")
    prose(f"Pack: {pack.name} ({pack.profile})" if pack else "Workspace tool · runs as your user")
    if pack:
        prose(f"Handler: {definition['handler']} · timeout {definition['timeout_seconds']:g}s"
              f" · result limit {definition['max_output_bytes']:,} bytes")
    prose(definition["description"])
    if definition.get("documentation"):
        heading("Notes")
        prose(definition["documentation"])
    heading("Parameters")
    for key, prop in schema.get("properties", {}).items():
        lines.append(display.ink(key, "tool") + " : " + display.ink(prop["type"], "model")
                     + display.ink(" · required" if key in required else " · optional", "strong"))
        if prop.get("description"):
            prose("  " + prop["description"])
        constraints = []
        for field in ("enum", "minimum", "maximum", "maxLength", "default"):
            if field in prop:
                constraints.append(f"{field}={json.dumps(prop[field], ensure_ascii=False)}")
        if constraints:
            prose("  " + " · ".join(constraints))
    if not schema.get("properties"):
        prose("No arguments.")
    prose("Unknown arguments are rejected. Omit optional values to use the handler's behavior.")
    heading("Returns")
    prose(definition.get("returns", "Bounded JSON object from the pack handler." if pack
                         else "Text result; failures begin with ERROR: or a nonzero exit status."))
    heading("Examples" if definition.get("examples") else "Call template · replace <placeholders>")
    examples = definition.get("examples") or [{"arguments": example_arguments(definition)}]
    for example in examples:
        if example.get("summary"):
            prose(example["summary"])
        prose("/call " + name + " " + json.dumps(example["arguments"], ensure_ascii=False))
    prose("Append :: your question to give the result to the model immediately.")
    heading("Schema · exactly what the model receives")
    lines.extend(color_json({key: definition[key] for key in ("name", "description", "parameters")}, display).splitlines())
    return lines


def show_reference(runtime, name, display):
    print("\n".join(reference_lines(runtime, name, display)), flush=True)


def browse_tools(runtime, display, escape_key):
    """Arrow through tools and scroll their docs. Enter keeps a reference in scrollback."""
    entries = tool_entries(runtime)
    if not entries or not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    selected, offset, keep = 0, 0, False
    try:
        tty.setraw(fd, termios.TCSANOW)
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        while True:
            width, height = shutil.get_terminal_size((90, 30))
            menu_rows = min(6, max(1, height // 4), len(entries))
            start = min(max(0, selected - menu_rows + 1), len(entries) - menu_rows)
            rows = [display.ink(f"TOOLS · {selected + 1}/{len(entries)} · ↑↓ select · ←→ scroll docs · Enter keep · Esc close", "brand")]
            for index in range(start, start + menu_rows):
                definition, pack = entries[index]
                label = f"{'▸' if index == selected else ' '} {definition['name']} · {pack.name if pack else 'workspace'}"
                rows.append(display.ink(label[:max(1, width - 1)], "selected" if index == selected else "muted"))
            rows.append("─" * max(1, width - 1))
            docs = reference_lines(runtime, entries[selected][0]["name"], display, width=max(32, width - 2))
            space = max(1, height - len(rows) - 2)
            offset = min(offset, max(0, len(docs) - space))
            rows.extend(docs[offset:offset + space])
            rows.append(display.ink(f"Lines {offset + 1}–{min(offset + space, len(docs))}/{len(docs)} · viewing only; no calls", "muted"))
            # Hard truncate visible columns while preserving ANSI codes; no row wrapping.
            clipped = [clip_line(row, max(1, width - 1)) for row in rows[:max(1, height - 1)]]
            sys.stdout.write("\x1b[H\x1b[J" + "\r\n".join(clipped))
            sys.stdout.flush()
            key = os.read(fd, 1)
            if not key or key in (b"\x03", b"\x04", b"q"):
                break
            if key in (b"\r", b"\n"):
                keep = True
                break
            if key == b"\x1b":
                action = escape_key(fd)
                if action == "escape":
                    break
                if action in ("up", "down"):
                    selected = (selected + (1 if action == "down" else -1)) % len(entries)
                    offset = 0
                elif action in ("left", "right"):
                    offset = max(0, offset + (space if action == "right" else -space))
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
    if keep:
        show_reference(runtime, entries[selected][0]["name"], display)
