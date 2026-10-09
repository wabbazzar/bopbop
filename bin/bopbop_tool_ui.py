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
import unicodedata

from bopbop_packs import strict_json, validate_arguments


def tool_entries(runtime):
    """The runtime provides enabled declarations; this UI knows no tool names."""
    return runtime.tool_definitions


def find_tool(runtime, name):
    for definition, pack in tool_entries(runtime):
        if definition["name"] == name:
            return definition, pack
    raise ValueError(f"Tool {name!r} is not enabled. Use /tools to inspect your selection.")


def parse_call(text, runtime):
    """NAME [JSON object] [:: question]; delimiters within JSON are data."""
    if len(text.encode()) > 65536:
        raise ValueError("Tool request exceeds 64 KB")
    text = text.strip()
    if not text:
        raise ValueError('Usage: /call TOOL {"argument":"value"} [:: question]')
    head = re.match(r'([a-z][a-z0-9_]*)(?=\s|[\[{]|::|$)', text)
    if not head:
        raise ValueError('Usage: /call TOOL {"argument":"value"} [:: question]')
    name = head[1]
    definition, pack = find_tool(runtime, name)
    rest = text[head.end():].lstrip()
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


def cell_width(char):
    if unicodedata.category(char) in {"Mn", "Me", "Cf", "Cc"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def text_width(value):
    return sum(cell_width(char) for char in value)


def clip_text(value, columns):
    width, end = 0, 0
    for index, char in enumerate(value):
        width += cell_width(char)
        if width > columns:
            break
        end = index + 1
    return value[:end]


def draft_view(value, cursor, columns):
    """One visible input row, keeping the editing cursor inside the terminal."""
    columns = max(3, columns)
    start, used = cursor, 0
    while start > 0 and used + cell_width(value[start - 1]) <= columns - 2:
        start -= 1
        used += cell_width(value[start])
    marker = "‹" if start else ""
    rest = value[start:]
    space = columns - len(marker)
    clipped = text_width(rest) > space
    visible = marker + clip_text(rest, space - int(clipped)) + ("›" if clipped else "")
    return visible, len(marker) + used


def clip_line(row, columns):
    """Clip presentation text without counting our ANSI color sequences."""
    visible, pieces = 0, []
    for piece in re.split(r"(\x1b\[[0-9;]*m)", row):
        if piece.startswith("\x1b["):
            pieces.append(piece)
        else:
            available = max(0, columns - visible)
            part = clip_text(piece, available)
            pieces.append(part)
            visible += text_width(part)
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


def call_tool_name(runtime, draft):
    """Identify only the tool; unfinished JSON and question text are irrelevant."""
    head = re.match(r'/call\s+([a-z][a-z0-9_]*)(?=\s|[\[{]|::|$)', draft, re.I)
    if runtime is None or head is None:
        return None
    try:
        definition, _ = find_tool(runtime, head[1])
    except ValueError:
        return None
    return definition["name"]


def argument_focus(draft, cursor=None):
    """Tolerant scan of flat JSON at the cursor, including unfinished strings.

    This never interprets delimiters inside strings as keys or question markers.
    Nested values remain opaque: protocol v1 tools have scalar parameters.
    """
    prefix = draft if cursor is None else draft[:cursor]
    head = re.match(r'/call\s+[a-z][a-z0-9_]*\s*', prefix, re.I)
    if not head or not prefix[head.end():].startswith('{'):
        return None
    text = prefix[head.end() + 1:]
    index, key, state, used = 0, '', 'key', set()

    def focus(mode, value=''):
        return mode, key, value, frozenset(used)

    def string_at(start):
        end, escaped = start + 1, False
        while end < len(text):
            char = text[end]
            if char == '"' and not escaped:
                try:
                    return json.loads(text[start:end + 1]), end + 1, True
                except ValueError:
                    return text[start + 1:end], end + 1, True
            escaped = not escaped if char == '\\' else False
            end += 1
        raw = text[start:]
        try:
            value = json.loads(raw + '"')
        except ValueError:
            value = raw[1:]
        return value, end, False

    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return focus('key' if state == 'key' else 'value')
        if text[index] == '}':
            return None
        if state == 'key':
            if text[index] != '"':
                return focus('key', text[index:].strip())
            value, index, closed = string_at(index)
            if not closed:
                return focus('key', value)
            key = value
            state = 'colon'
        elif state == 'colon':
            if text[index] != ':':
                return focus('key', key)
            index += 1
            state = 'value'
        else:
            if text[index] == '"':
                value, index, closed = string_at(index)
                if not closed:
                    return focus('value', value)
            else:
                start, depth, quoted, escaped = index, 0, False, False
                while index < len(text):
                    char = text[index]
                    if not quoted and depth == 0 and char in ',}':
                        break
                    if char == '"' and not escaped:
                        quoted = not quoted
                    if not quoted:
                        depth += int(char in '[{') - int(char in ']}')
                    escaped = not escaped if char == '\\' else False
                    index += 1
                value = text[start:index].strip()
            while index < len(text) and text[index].isspace():
                index += 1
            if index == len(text):
                return focus('value', value)
            if text[index] == '}':
                return None
            if text[index] != ',':
                return focus('value', value)
            used.add(key)
            key, state = '', 'key'
            index += 1


def call_help_lines(runtime, name, display, width, draft=None, cursor=None):
    """Compact live reference; expanded help shares reference_lines below."""
    definition, _ = find_tool(runtime, name)
    schema = definition["parameters"]
    lines = [display.ink(f"◆ {name} · call help", "brand")]
    focus = argument_focus(draft, cursor) if draft is not None else None
    props = schema.get('properties', {})
    if focus and props:
        mode, key, prefix, used = focus
        def value_prefix(value):
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        names = ([key] if key in props else []) if mode == 'value' else [
            name for name in props if name.startswith(prefix) and name not in used]
        if len(names) == 1:
            key = names[0]
            prop = props[key]
            required = key in schema.get('required', [])
            lines.append(display.ink(key, 'tool') + ': ' + display.ink(prop['type'], 'model')
                         + display.ink(' · required' if required else ' · optional', 'strong'))
            if prop.get('description'):
                lines.extend(textwrap.wrap(safe_text(prop['description']), max(12, width)))
            if 'enum' in prop:
                options = [value for value in prop['enum'] if mode != 'value' or
                           value_prefix(value).startswith(prefix)]
                label = 'Matching values' if mode == 'value' and prefix else 'Allowed values'
                values = ' | '.join(safe_text(json.dumps(value, ensure_ascii=False)) for value in options)
                lines.extend(textwrap.wrap(f'{label}: {values or "no matches"}', max(12, width)))
            for field in ('minimum', 'maximum', 'maxLength', 'default'):
                if field in prop:
                    lines.append(f'{field}: {safe_text(json.dumps(prop[field], ensure_ascii=False))}')
            examples = [example for example in definition.get('examples', []) if key in example['arguments']
                        and (mode != 'value' or not prefix or
                             value_prefix(example['arguments'][key]).startswith(prefix))]
            for example in examples[:3]:
                lines.extend(textwrap.wrap('Example: /call ' + name + ' '
                    + safe_text(json.dumps(example['arguments'], ensure_ascii=False)), max(12, width)))
        elif names:
            lines.append(display.ink('Parameters' + (f' matching {prefix!r}' if prefix else ' available'), 'strong'))
            for key in names:
                prop = props[key]
                lines.append(display.ink(key, 'tool') + ': ' + prop['type']
                             + (' · required' if key in schema.get('required', []) else ' · optional')
                             + (' · ' + safe_text(prop['description']) if prop.get('description') else ''))
        else:
            lines.append('No matching parameters.' if prefix or mode == 'value' else 'All declared parameters supplied.')
        return [clip_line(line, width) for line in lines]
    description = textwrap.wrap(safe_text(definition["description"]), max(12, width))
    lines.extend(description[:2])
    if len(description) > 2:
        lines[-1] = clip_text(lines[-1], max(0, width - 1)) + "…"
    required = schema.get("required", [])
    for key, prop in schema.get("properties", {}).items():
        line = (display.ink(key, "tool") + ": " + display.ink(prop["type"], "model")
                + display.ink(" · required" if key in required else " · optional", "strong"))
        constraints = []
        for field in ("enum", "minimum", "maximum", "maxLength", "default"):
            if field in prop:
                constraints.append(f"{field}={json.dumps(prop[field], ensure_ascii=False)}")
        if constraints:
            line += " · " + safe_text(" · ".join(constraints))
        lines.append(line)
    if not schema.get("properties"):
        lines.append("No arguments.")
    example = json.dumps(example_arguments(definition), ensure_ascii=False)
    lines.append(display.ink("Example", "ok") + "  " + safe_text(f"/call {name} {example}"))
    result = []
    for line in lines:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        result.append(clip_line(line, max(0, width - 1)) + "…"
                      if text_width(plain) > width else line)
    return result


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
