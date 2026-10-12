"""Optional managed terminal. Engine work lives in a cancellable child process.

Only this module imports prompt_toolkit. No UI state is written to disk.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import multiprocessing
import os
import queue
import re
import signal
import shlex
import time
import pathlib
import subprocess
import shutil

from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.scroll import (
    scroll_half_page_down, scroll_half_page_up, scroll_forward, scroll_backward,
)
from prompt_toolkit.layout import (Layout, HSplit, VSplit, Window, FloatContainer, Float,
                                   ConditionalContainer, DynamicContainer, Dimension)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.widgets import TextArea, SearchToolbar

from bopbop_tool_ui import (argument_focus, call_help_lines, call_tool_name,
                            clip_text, example_arguments, find_tool, reference_lines, safe_text,
                            tool_entries)
from bopbop_inspector import (InspectorStore, Snapshot, request_snapshot, result_snapshot,
                              file_snapshot, attachment_block)
from bopbop_editor import EditorBridge, editor_prompt, edit_result

UI_COMMANDS = (
    ('/editor', 'Browse workspace files and open one in connected Neovim'),
    ('/inspect', 'Show docs TOOL, result, context, file PATH, or diff PATH'),
    ('/attach', 'Attach inspector body lines: /attach 1:20'),
    ('/detach', 'Clear pending evidence attachments'),
    ('/pane', 'Toggle inspector (F9)'),
    ('/pin', 'Keep the current inspector snapshot (F7)'),
)


class InspectorLexer(Lexer):
    def lex_document(self, document):
        def line(index):
            text = document.lines[index]
            parts = re.split(r'("(?:[^"\\]|\\.)*"|\b(?:true|false|null|\d+)\b)', text)
            return [('class:tool' if part.startswith('"') else 'class:brand' if
                     re.fullmatch(r'true|false|null|\d+', part) else '', part) for part in parts]
        return line


class TranscriptLexer(Lexer):
    def lex_document(self, document):
        def line(index):
            text = document.lines[index]
            stripped = text.strip()
            style = ('brand' if stripped.startswith(('›', '◇', '└')) else
                     'answer' if stripped.startswith('◆ answer') else
                     'tool' if stripped.startswith(('├', '◆')) else
                     'muted' if stripped.startswith(('│', 'tokens', 'timing', 'stream', 'context')) else '')
            return [('class:' + style if style else '', text)]
        return line


STYLE = Style.from_dict({
    '': '#e6eaf0 bg:#141b24',
    'brand': '#78becb bold', 'muted': '#82909d', 'rule': '#344350',
    'selected': '#141b24 bg:#78becb bold', 'tool': '#dfbe83 bold',
    'menu': 'bg:#202b38', 'status': '#82909d', 'input': '#e6eaf0',
    'search': '#dfbe83', 'text-area': '#e6eaf0', 'answer': '#9dccad bold',
})


class EventWriter(io.TextIOBase):
    """Capture legacy presentation in the worker, never process-wide in the UI."""
    def __init__(self, send):
        self.send = send

    def write(self, text):
        if text:
            self.send({'kind': 'output', 'text': text})
        return len(text)

    def flush(self):
        pass


def run_worker(engine, args, runtime, messages, session, prompt, events, editor=None):
    os.setsid()
    def stop(signum, frame):
        # Unwind native pack handlers so their own process groups are reaped.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    send = events.put
    streamed = [False]
    def observe(event):
        if event['kind'] == 'request':
            streamed[0] = False
        elif event['kind'] == 'stream' and event.get('delta'):
            streamed[0] = True
        send(event)
    runtime.observer = observe
    writer = EventWriter(send)
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            class WorkerDisplay(engine.Display):
                def answer(self, value):
                    if not streamed[0]:
                        super().answer(value)
            display = WorkerDisplay(False)
            if editor:
                runtime.review_only = True
                runtime.response_format = ({'type': 'object', 'properties': {
                    'replacement': {'type': 'string'}, 'summary': {'type': 'string'}},
                    'required': ['replacement', 'summary']} if editor['action'] == 'edit' else None)
                messages.append({'role': 'user', 'content': editor_prompt(editor)})
                answer, usage = engine.turn(args.model, messages, args.workspace, args.context,
                                            display, runtime=runtime)
                session.record(usage)
                send({'kind': 'editor_result', 'result': edit_result(answer, editor)})
            elif not engine.handle_command(prompt, messages, session, args.model, args.context,
                                          args.workspace, display, runtime):
                messages.append({'role': 'user', 'content': prompt})
                answer, usage = engine.turn(args.model, messages, args.workspace, args.context,
                                            display, runtime=runtime)
                session.record(usage)
                display.answer(answer)
    except KeyboardInterrupt:
        send({'kind': 'output', 'text': '\nTurn cancelled.\n'})
        if editor:
            send({'kind': 'editor_result', 'error': 'Cancelled; selection and prompt retained'})
    except Exception as exc:
        send({'kind': 'output', 'text': f'\nError: {type(exc).__name__}: {exc}\n'})
        if editor:
            send({'kind': 'editor_result', 'error': str(exc)})
    finally:
        send({'kind': 'finished', 'messages': messages, 'session': session})


class TerminalUI:
    def __init__(self, engine, args, runtime, messages, session, *, input=None, output=None):
        self.engine, self.args, self.runtime = engine, args, runtime
        self.messages, self.session = messages, session
        self.args.workspace = args.workspace.expanduser().resolve()
        self.display = engine.Display(engine.use_color(args.color))
        self.history, self.history_edits, self.history_index = [], {}, 0
        self.selected = 0
        self.hidden = False
        self.expanded = False
        self.help_offset = 0
        self.last_focus = None
        self.recalled = False
        self.worker = None
        self.events = None
        self.started = None
        self.notice = 'Ready'
        self.evicted_lines = 0
        self.store = InspectorStore()
        self.inspector_visible = args.ui == 'split'
        self.inspector_mode = 'docs'
        self.inspector_id = None
        self.inspector_ratio = .46
        self.attachments = []
        self.inspector_body_start = 0
        self.last_request_id = None
        self.stream_started = False
        self.stream_activity = ''
        self.doc_name = None
        self.file_task = None
        self.pending_tools = {}
        self.editor_bridge = None
        self.editor_task = None
        self.editor_listing_cache = None
        self.editor_preview_cache = None
        self.inspector_search = SearchToolbar()
        self.inspector = TextArea(text='F2 Docs · F3 Result · F4 Context · F5 File\n'
                                  'Use /inspect file PATH to read a workspace file.\n',
                                  read_only=True, scrollbar=True, focus_on_click=True,
                                  wrap_lines=Condition(lambda: self.inspector_mode != 'file'),
                                  search_field=self.inspector_search, lexer=InspectorLexer())
        self.search = SearchToolbar()
        self.transcript = TextArea(text='Type / for commands. F6 changes focus; Ctrl-R searches a reader.\n',
                                   read_only=True, scrollbar=True, wrap_lines=True, focus_on_click=True,
                                   search_field=self.search, lexer=TranscriptLexer())
        self.draft = Buffer(multiline=False)
        self.draft.on_text_changed += self.changed
        self.draft.on_cursor_position_changed += self.cursor_changed
        self.input_control = BufferControl(buffer=self.draft)
        self.input_window = Window(self.input_control, height=1, wrap_lines=False,
                                   get_line_prefix=lambda *unused: [('class:brand', 'bopbop> ')])
        self.keys = KeyBindings()
        self.bind_keys()
        self.inspector_container = HSplit([
            Window(FormattedTextControl(self.inspector_header), height=1, style='class:menu'),
            self.inspector, self.inspector_search,
            Window(FormattedTextControl(lambda: [('class:muted',
                ' Ctrl-D/U scroll · hjkl · gg/G · F7 pin · F8 attach')]), height=1),
        ])
        self.split = VSplit([self.transcript, Window(width=1, char='│', style='class:rule'),
                             HSplit([self.inspector_container], width=self.pane_width)])
        self.body = DynamicContainer(self.body_layout)
        self.root = FloatContainer(HSplit([
            Window(FormattedTextControl(self.header), height=1), self.body,
            self.search, Window(height=1, char='─', style='class:rule'),
            self.input_window, Window(FormattedTextControl(self.footer), height=1),
        ]), floats=[Float(left=0, right=0, bottom=3, height=self.popup_height,
                         content=ConditionalContainer(Window(
                             FormattedTextControl(self.popup_text), style='class:menu',
                             wrap_lines=False), filter=Condition(lambda: self.popup_height() > 0)))])
        self.app = Application(layout=Layout(self.root, focused_element=self.input_control),
                               key_bindings=self.keys, full_screen=True, mouse_support=True,
                               style=STYLE if self.display.color else Style.from_dict({}),
                               input=input, output=output, refresh_interval=.1,
                               before_render=self.before_render)
        self.app.ttimeoutlen = .05
        self.app.timeoutlen = .3
        self.bind_reader_mouse(self.transcript)
        self.bind_reader_mouse(self.inspector)

    def bind_reader_mouse(self, reader):
        original = reader.control.mouse_handler
        def handle(event):
            # BufferControl focuses on mouse-up by default; focus first so the
            # first drag in a reader can select text immediately.
            if event.event_type == MouseEventType.MOUSE_DOWN:
                self.app.layout.focus(reader)
            result = original(event)
            if event.event_type == MouseEventType.MOUSE_UP and reader.buffer.selection_state:
                self.copy_reader(reader, selected_only=True)
            return result
        reader.control.mouse_handler = handle

    def clipboard_write(self, value):
        data = value.encode('utf-8')
        if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-copy'):
            command = ['wl-copy']
        elif os.environ.get('DISPLAY') and shutil.which('xclip'):
            command = ['xclip', '-selection', 'clipboard', '-i']
        elif shutil.which('pbcopy'):
            command = ['pbcopy']
        else:
            # OSC 52 works in many local and remote terminals, including tmux
            # when clipboard passthrough is enabled.
            if len(data) > 100000:
                raise ValueError('Selection is too large for terminal clipboard; select fewer lines')
            self.app.output.write_raw('\x1b]52;c;' + base64.b64encode(data).decode('ascii') + '\x07')
            self.app.output.flush()
            return 'sent to terminal clipboard'
        subprocess.run(command, input=data, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=2, check=True)
        return 'copied to system clipboard'

    def copy_reader(self, reader, selected_only=False):
        doc = reader.buffer.document
        if doc.selection:
            start, end = doc.selection_range()
            value = doc.text[start:end]
        elif selected_only:
            return
        else:
            value = doc.current_line
        if not value:
            return
        try:
            destination = self.clipboard_write(value)
            self.notice = f'{len(value.encode()):,} bytes {destination}'
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.notice = f'Copy failed: {exc}'

    def pane_width(self):
        return Dimension.exact(int(self.app.output.get_size().columns * self.inspector_ratio))

    def before_render(self, app):
        if (self.inspector_visible and app.output.get_size().columns < 100
                and app.layout.has_focus(self.transcript)):
            app.layout.focus(self.inspector)

    def body_layout(self):
        if not self.inspector_visible:
            return self.transcript
        app = getattr(self, 'app', None)
        return self.split if app is None or app.output.get_size().columns >= 100 else self.inspector_container

    def inspector_header(self):
        pin = ' · PINNED' if self.store.pinned else ''
        return [('class:brand', f' {self.inspector_mode.upper()}{pin}  '),
                ('class:muted', 'F2 docs  F3 result  F4 context  F5 file')]

    def header(self):
        bridge = (f' · Neovim {self.editor_bridge.state}'
                  if self.editor_bridge and self.editor_bridge.connected else ' · /editor Neovim')
        return [('class:brand', ' ● bopbop  '), ('class:muted', f'{self.args.model} · local agent{bridge}')]

    def footer(self):
        if self.app.layout.has_focus(self.inspector) or self.app.layout.has_focus(self.transcript):
            reader = 'Inspector' if self.app.layout.has_focus(self.inspector) else 'Conversation'
            return [('class:status', f' {reader} focused · {self.notice} · drag to copy · y line/selection · Ctrl-D/U scroll · hjkl · Esc input')]
        state = (f'Working {time.monotonic() - self.started:.1f}s · {self.stream_activity} · Ctrl-C stop'
                 if self.worker else self.notice)
        if self.editor_navigation_active():
            return [('class:status', f' {state} · ↑↓ files · →/Tab enter · ← parent · Enter open · Esc close')]
        attachment = f' · {len(self.attachments)} attachments / {sum(a["bytes"] for a in self.attachments)} B' if self.attachments else ''
        retained = f' · {self.store.notice}' if self.store.notice else ''
        return [('class:status', f' {state}{attachment}{retained}  · F6 focus · F9 pane · Ctrl-D quit')]

    def matches(self):
        text = self.draft.text
        parts = text.split(maxsplit=1)
        if parts and parts[0] in {'/call', '/tools'} and ' ' in text:
            prefix = parts[1] if len(parts) > 1 else ''
            return [(f'{parts[0]} {definition["name"]}', definition['description'])
                    for definition, _ in tool_entries(self.runtime)
                    if definition['name'].startswith(prefix)]
        return [(name, description) for name, description in (*self.engine.SLASH_COMMANDS, *UI_COMMANDS)
                if text.startswith('/') and name.startswith(text.lower())]

    def editor_navigation_active(self):
        return (not self.hidden and not self.recalled and
                self.app.layout.has_focus(self.input_control) and
                (self.draft.text == '/editor' or self.draft.text.startswith('/editor ')))

    def editor_navigation(self):
        """One directory at a time; typed suffix filters names, including unsaved paths."""
        raw = self.draft.text[len('/editor'):].strip()
        if raw.startswith(('"', "'")) or '\\ ' in raw:
            try:
                parts = shlex.split(raw)
                raw = parts[0] if len(parts) == 1 else raw
            except ValueError:
                raw = raw[1:]  # permit a partially typed quoted path
        prefix, _, query = raw.rpartition('/')
        if raw.endswith('/'):
            prefix, query = raw[:-1], ''
        base = (self.args.workspace / prefix).resolve() if prefix else self.args.workspace
        if not base.is_relative_to(self.args.workspace):
            return base, query, [], 'Outside this workspace'
        if not base.is_dir():
            return base, query, [], 'Directory does not exist; Enter opens the typed path'
        try:
            modified = base.stat().st_mtime_ns
            cached = self.editor_listing_cache
            if cached and cached[:2] == (base, modified):
                entries = cached[2]
            else:
                entries = []
                with os.scandir(base) as directory:
                    for entry in directory:
                        try:
                            path = pathlib.Path(entry.path)
                            if path.resolve().is_relative_to(self.args.workspace):
                                entries.append((entry.name, path, entry.is_dir()))
                        except OSError:
                            continue
                entries.sort(key=lambda item: (not item[2], item[0].casefold(), item[0]))
                self.editor_listing_cache = (base, modified, entries)
        except OSError as exc:
            return base, query, [], f'Cannot read directory: {exc.strerror or exc}'
        choices = [item for item in entries if (not item[0].startswith('.') or query.startswith('.'))
                   and query.casefold() in item[0].casefold()]
        return base, query, choices, None

    def editor_draft_for(self, path, directory=False):
        relative = path.relative_to(self.args.workspace).as_posix()
        return '/editor ' + shlex.quote(relative + ('/' if directory else ''))

    def editor_preview(self, path, width):
        try:
            stat = path.stat()
            key = (path, stat.st_mtime_ns, stat.st_size)
            if self.editor_preview_cache and self.editor_preview_cache[0] == key:
                lines = self.editor_preview_cache[1]
            else:
                with path.open('rb') as source:
                    raw = source.read(2048)
                lines = ['Binary file'] if b'\0' in raw else raw.decode('utf-8', 'replace').splitlines()[:3]
                self.editor_preview_cache = (key, lines)
            return [clip_text(safe_text(line), width - 4) for line in lines]
        except (OSError, ValueError):
            return ['Preview unavailable']

    def editor_popup_lines(self, width):
        base, query, choices, error = self.editor_navigation()
        self.selected = min(self.selected, max(0, len(choices) - 1))
        location = base.relative_to(self.args.workspace).as_posix() if base.is_relative_to(self.args.workspace) else 'outside workspace'
        rows = [self.display.ink(clip_text(f'Files · {location}/' + (f' · filter {query}' if query else ''), width), 'brand')]
        capacity = max(1, min(9, self.app.output.get_size().rows - 11))
        start = max(0, self.selected - capacity + 1)
        for index in range(start, min(len(choices), start + capacity)):
            name, _, directory = choices[index]
            label = f'{"▸" if directory else "·"} {name}{"/" if directory else ""}'
            rows.append(self.display.ink(clip_text(('› ' if index == self.selected else '  ') + safe_text(label), width),
                                         'selected' if index == self.selected else 'tool' if directory else 'strong'))
        if error or not choices:
            rows.append(self.display.ink(error or 'No matches · Enter opens the typed path', 'muted'))
        elif choices[self.selected][2]:
            rows.append(self.display.ink('Folder · → / Tab / Enter to browse', 'muted'))
        else:
            path = choices[self.selected][1]
            rows.append(self.display.ink(clip_text('Preview · ' + path.name, width), 'muted'))
            rows += [self.display.ink('  ' + line, 'muted') for line in self.editor_preview(path, width)]
        rows.append(self.display.ink('↑↓ choose · →/Tab browse · ← parent · Enter open · Esc close', 'muted'))
        return rows

    def choose_editor(self, submit=False):
        _, _, choices, _ = self.editor_navigation()
        if not choices:
            if submit:
                self.submit()
            return
        _, path, directory = choices[min(self.selected, len(choices) - 1)]
        chosen = self.editor_draft_for(path, directory)
        self.draft.document = Document(chosen, len(chosen))
        if submit and not directory:
            self.submit()

    def editor_parent(self):
        base, _, _, _ = self.editor_navigation()
        parent = base.parent if base != self.args.workspace else self.args.workspace
        if not parent.is_relative_to(self.args.workspace):
            parent = self.args.workspace
        chosen = '/editor ' if parent == self.args.workspace else self.editor_draft_for(parent, True)
        self.draft.document = Document(chosen, len(chosen))

    def active_tool(self):
        return call_tool_name(self.runtime, self.draft.text)

    def changed(self, unused):
        self.selected = 0
        self.hidden = False
        self.recalled = False
        self.cursor_changed(unused)

    def cursor_changed(self, unused):
        focus = (self.active_tool(), argument_focus(self.draft.text, self.draft.cursor_position))
        if focus != self.last_focus:
            self.help_offset = 0
            self.last_focus = focus
        name = self.active_tool()
        if name and name != self.doc_name and self.inspector_mode == 'docs' and not self.store.pinned:
            self.show_docs(name, reveal=False)

    def popup_lines(self):
        if self.hidden or not self.app.layout.has_focus(self.input_control):
            return []
        width = max(12, self.app.output.get_size().columns - 4)
        if self.editor_navigation_active():
            return self.editor_popup_lines(width)
        name = self.active_tool()
        if name:
            docs = (reference_lines(self.runtime, name, self.display, width) if self.expanded else
                    call_help_lines(self.runtime, name, self.display, width,
                                    self.draft.text, self.draft.cursor_position))
            capacity = max(1, min(self.app.output.get_size().rows - 6, 18 if self.expanded else 8))
            self.help_offset = min(self.help_offset, max(0, len(docs) - capacity))
            return docs[self.help_offset:self.help_offset + capacity] + [
                self.display.ink('F1 full/compact · PgUp/PgDn scroll · Esc close', 'muted')]
        found = self.matches()
        if not found or self.recalled:
            return []
        capacity = max(1, min(6, self.app.output.get_size().rows - 6))
        start = max(0, self.selected - capacity + 1)
        rows = [self.display.ink('Commands', 'brand')]
        for index in range(start, min(len(found), start + capacity)):
            name, description = found[index]
            row = f'{"›" if index == self.selected else " "} {name:<20} {safe_text(description)}'
            rows.append(self.display.ink(row, 'selected' if index == self.selected else 'strong'))
        return rows + [self.display.ink('↑↓ select · Tab complete · Enter choose · Esc close', 'muted')]

    def popup_height(self):
        return min(len(self.popup_lines()), max(0, self.app.output.get_size().rows - 4))

    def popup_text(self):
        return ANSI('\n'.join('  ' + line for line in self.popup_lines()))

    def append(self, text):
        # Keep terminal controls out of transcript data, including external tool output.
        text = safe_text(re.sub(r'\x1b\[[0-9;]*m', '', text))
        buffer = self.transcript.buffer
        following = not self.app.layout.has_focus(self.transcript)
        combined = buffer.text + text
        if len(combined) > 240000:
            removed = len(combined) - 200000
            self.evicted_lines += combined[:removed].count('\n')
            combined = f'[Older display text evicted; {self.evicted_lines} lines]\n' + combined[removed:]
        cursor = len(combined) if following else min(buffer.cursor_position, len(combined))
        buffer.set_document(Document(combined, cursor), bypass_readonly=True)

    def history_move(self, delta):
        self.history_edits[self.history_index] = (self.draft.text, self.draft.cursor_position)
        self.history_index = max(0, min(len(self.history), self.history_index + delta))
        default = self.history[self.history_index] if self.history_index < len(self.history) else ''
        text, cursor = self.history_edits.get(self.history_index, (default, len(default)))
        self.draft.document = Document(text, cursor)
        self.recalled = True

    def choose(self, submit=False):
        if self.editor_navigation_active():
            self.choose_editor(submit)
            return
        found = self.matches() if not self.hidden and not self.recalled else []
        if found:
            chosen = found[self.selected][0]
            if chosen == '/editor':
                self.draft.document = Document('/editor ', len('/editor '))
                return
            if chosen in {'/call', '/tools'}:
                self.draft.document = Document(chosen + ' ', len(chosen) + 1)
                return
            if chosen.startswith('/call '):
                definition, _ = find_tool(self.runtime, chosen.split()[1])
                chosen += ' ' + json.dumps(example_arguments(definition), ensure_ascii=False)
                self.draft.document = Document(chosen, len(chosen))
                return
            if not submit:
                self.draft.document = Document(chosen, len(chosen))
                return
            self.draft.document = Document(chosen, len(chosen))
        if submit:
            self.submit()

    def submit(self):
        prompt = self.draft.text.strip()
        if not prompt:
            return
        if self.worker and prompt.split()[0] not in dict(UI_COMMANDS) and not prompt.startswith('/tools') and prompt not in {'/exit', '/help'}:
            self.notice = 'Still working; draft kept. Ctrl-C stops the active turn.'
            return
        if prompt == '/exit':
            if self.worker:
                self.cancel()
            self.app.exit()
            return
        self.history.append(self.draft.text)
        self.history = self.history[-500:]
        self.history_index = len(self.history)
        self.history_edits = {}
        self.draft.reset()
        self.append('\n› ' + prompt + '\n')
        if self.ui_command(prompt):
            return
        if prompt.startswith('/tools'):
            parts = prompt.split(maxsplit=1)
            if len(parts) == 1:
                self.append('\n'.join(f'{d["name"]}: {d["description"]}' for d, _ in tool_entries(self.runtime)) + '\n')
            else:
                try:
                    self.show_docs(parts[1])
                except ValueError as exc:
                    self.append(str(exc) + '\n')
            return
        if prompt == '/help':
            self.append('◆ commands\n' + '\n'.join(
                f'  {name:<9} {description}' for name, description in (*self.engine.SLASH_COMMANDS, *UI_COMMANDS))
                + '\n\n◆ editor browser\n'
                '  /editor · ↑↓ files · →/Tab enter folder · ← parent · Enter open · Esc close\n'
                '  Neovim v/V select · <leader>be explain · <leader>br edit\n'
                '  Review <leader>ba apply · <leader>bd discard\n'
                '◆ inspector\n'
                '  F2 Docs · F3 Result · F4 Context · F5 File · F6 focus · F7 pin · F8 attach · F9 pane\n'
                '  Drag in a reader to select and copy · y copies a selection or current line\n'
                '  Alt-Left/Right snapshots · Alt-−/+ resize · Ctrl-Space select body lines\n')
            return
        if prompt == '/reset':
            self.attachments = []
        question = not prompt.startswith('/')
        if prompt.startswith('/call '):
            from bopbop_tool_ui import parse_call
            try:
                question = parse_call(prompt[6:], self.runtime)[2] is not None
            except ValueError:
                question = False
        if question and self.attachments:
            prompt += attachment_block(self.attachments)
            self.append(f'[Attached {len(self.attachments)} selected evidence block(s)]\n')
            self.attachments = []
        self.start_worker(prompt)

    def show_docs(self, name, reveal=True):
        lines = reference_lines(self.runtime, name, self.engine.Display(False), width=76)
        self.doc_name = name
        self.select_snapshot(Snapshot('docs', name, '\n'.join(lines),
                                      {'scope': 'Tool-owned documentation; viewing does not execute'}), reveal)

    def select_snapshot(self, item, reveal=True):
        key = self.store.add(item)
        if key:
            self.store.pinned = None
            self.inspector_id = key
            self.inspector_mode = item.kind
            self.inspector_visible |= reveal
            self.render_inspector()
        else:
            self.notice = self.store.notice

    def record_snapshot(self, item):
        key = self.store.add(item)
        if key and not self.store.pinned and self.inspector_mode == item.kind:
            self.inspector_id = key
            self.render_inspector()
        return key

    def render_inspector(self):
        item = self.store.items.get(self.inspector_id)
        if not item:
            text = f'No {self.inspector_mode} snapshot yet.\n'
            if self.inspector_mode == 'docs': text += 'Use /tools TOOL to open its reference.'
            if self.inspector_mode == 'file': text += 'Use /inspect file PATH or /inspect diff PATH.'
            self.inspector_body_start = 0
        else:
            header = f'{item.title}\n{item.id} · {item.created}\n{item.size:,} bytes · SHA256 {item.digest[:16]}\n'
            meta = item.metadata
            if item.kind == 'result':
                telemetry = meta.get('telemetry', {})
                header += (f'{"OK" if meta.get("ok") else "Incomplete / failed"} · {meta.get("duration_ms", "?")} ms'
                           f' · state {telemetry.get("state") or "unreported"}'
                           f' · truncated {json.dumps(telemetry["truncated"]) if telemetry.get("truncated") is not None else "unreported"}\n')
                header += 'Arguments: ' + safe_text(json.dumps(meta.get('arguments', {}), ensure_ascii=False)) + '\n'
                header += ('No completed result received.\n' if meta.get('complete') is False else
                           'Complete returned tool result; source omissions remain omitted.\n')
            elif item.kind == 'context':
                header += f'Exact outbound JSON · {len(meta.get("removed_messages", []))} messages pruned\n'
                usage = meta.get('usage')
                if isinstance(usage, dict):
                    header += f'Measured tokens: {usage["prompt_tokens"]} in / {usage["output_tokens"]} out · {usage["wall_s"]:.2f}s\n'
                else: header += 'Usage pending; no token estimates.\n'
            elif item.kind == 'file':
                header += f'{meta.get("file_bytes", 0)} file bytes · snapshot only; not attached\n'
                if 'base_commit' in meta: header += 'Base HEAD: ' + meta['base_commit'][:12] + '\n'
            header = safe_text(header) + '\n'
            self.inspector_body_start = header.count('\n')
            lines = item.content().splitlines()
            text = header + '\n'.join(f'{index:4} │ {safe_text(line)}' for index, line in enumerate(lines, 1))
            text += '\n\nSnapshot metadata (not attachment lines)\n' + safe_text(json.dumps(meta, ensure_ascii=False, indent=2))
        if self.store.notice:
            text += '\n\n' + self.store.notice
        self.inspector.buffer.set_document(Document(text, 0), bypass_readonly=True)

    def switch_view(self, kind):
        self.inspector_visible = True
        self.inspector_mode = kind
        self.store.pinned = None
        records = self.store.records(kind)
        self.inspector_id = records[-1].id if records else None
        if kind == 'docs' and not records:
            entries = tool_entries(self.runtime)
            if entries:
                self.show_docs(entries[0][0]['name'])
                return
        self.render_inspector()

    def cycle_snapshot(self, delta):
        records = self.store.records(self.inspector_mode)
        if records:
            ids = [item.id for item in records]
            index = ids.index(self.inspector_id) if self.inspector_id in ids else len(ids) - 1
            self.inspector_id = ids[(index + delta) % len(ids)]
            if self.store.pinned:
                self.store.pinned = self.inspector_id
            self.render_inspector()

    def pin(self):
        self.store.pinned = None if self.store.pinned else self.inspector_id
        self.notice = 'Inspector pinned' if self.store.pinned else 'Inspector follows new snapshots'

    def attach(self, start, end):
        item = self.store.items.get(self.inspector_id)
        if not item:
            raise ValueError('Open a snapshot first')
        attachment = item.attachment(start, end)
        if len(self.attachments) >= 8 or sum(x['bytes'] for x in self.attachments) + attachment['bytes'] > 32000:
            raise ValueError('Pending attachments exceed 8 selections / 32 KB; /detach to clear')
        self.attachments.append(attachment)
        self.notice = f'Attached {item.title} lines {start}–{end} · {attachment["bytes"]} bytes'
        self.append(f'[{self.notice}; next question only]\n')

    async def load_file(self, path, diff):
        try:
            item = await asyncio.to_thread(file_snapshot, self.args.workspace, path, diff)
            self.select_snapshot(item)
            self.notice = 'File snapshot ready; F8 or /attach selects evidence'
        except (OSError, ValueError) as exc:
            self.append(f'Cannot inspect: {exc}\n')
            self.notice = 'File inspection failed'
        finally:
            self.file_task = None

    def ui_command(self, prompt):
        command, _, arg = prompt.partition(' ')
        if command not in dict(UI_COMMANDS):
            return False
        try:
            if command == '/editor': self.open_editor(arg)
            elif command == '/pane': self.toggle_pane()
            elif command == '/pin': self.pin()
            elif command == '/detach':
                self.attachments = []
                self.notice = 'Attachments cleared'
            elif command == '/attach':
                start, end = map(int, arg.split(':'))
                self.attach(start, end)
            elif command == '/inspect':
                parts = shlex.split(arg)
                if len(parts) == 1 and parts[0] in {'docs', 'result', 'context', 'file'}:
                    self.switch_view(parts[0])
                elif len(parts) == 2 and parts[0] == 'docs': self.show_docs(parts[1])
                elif len(parts) == 2 and parts[0] in {'file', 'diff'}:
                    if self.file_task:
                        raise ValueError('A file snapshot is already loading')
                    self.file_task = self.app.create_background_task(self.load_file(parts[1], parts[0] == 'diff'))
                else: raise ValueError('Use /inspect docs TOOL, result, context, file PATH, or diff PATH')
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            self.append(f'{command}: {exc}\n')
        return True

    def toggle_pane(self):
        self.inspector_visible = not self.inspector_visible
        self.app.layout.focus(self.input_control)

    def open_editor(self, arg):
        if not self.editor_bridge:
            raise ValueError('Editor bridge unavailable; see startup message')
        parts = shlex.split(arg) if arg.startswith(('"', "'")) or '\\ ' in arg else ([arg] if arg else [])
        if len(parts) > 1:
            raise ValueError('Use /editor "path with spaces"')
        path = (self.args.workspace / (parts[0] if parts else '.')).resolve()
        if not path.is_relative_to(self.args.workspace):
            raise ValueError('File must be inside this session workspace')
        command = [str(pathlib.Path(__file__).with_name('bopbop')), 'editor', '--connect',
                   self.editor_bridge.id, str(path)]
        if os.environ.get('TMUX'):
            try:
                split = ['tmux', 'split-window', '-h', '-c', str(self.args.workspace)]
                if os.environ.get('TMUX_PANE'):
                    split += ['-t', os.environ['TMUX_PANE']]
                result = subprocess.run([*split, shlex.join(command)],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    self.notice = 'Neovim opened in tmux · select v/V → explain/edit'
                    return
                self.append(f'tmux split failed: {safe_text(result.stderr.strip())}\n')
            except (OSError, subprocess.SubprocessError) as exc:
                self.append(f'tmux split failed: {exc}\n')
        if self.editor_task:
            raise ValueError('An editor is already open in this terminal')
        self.notice = 'Opening Neovim in this terminal · :q returns to BopBop'
        self.editor_task = self.app.create_background_task(self.launch_editor(command))

    async def launch_editor(self, command):
        try:
            result = await run_in_terminal(lambda: subprocess.run(command, cwd=self.args.workspace),
                                           in_executor=True)
            self.notice = ('Back from Neovim' if result.returncode == 0
                           else f'Neovim exited with status {result.returncode}')
        except (OSError, subprocess.SubprocessError) as exc:
            self.notice = f'Could not launch Neovim: {exc}'
            self.append(self.notice + '\n')
        finally:
            self.editor_task = None

    def submit_editor(self, request):
        if self.worker:
            raise ValueError('BopBop is busy; selection and prompt retained. Retry when ready')
        self.record_snapshot(Snapshot('file', 'Neovim selection · ' + request['path'],
                                      request['text'], {k: v for k, v in request.items() if k != 'text'}))
        self.append(f'\n› Neovim {request["action"]}: {request["prompt"]}\n'
                    f'{request["path"]} · {len(request["text"].encode())} B · shared session · no tools\n')
        self.start_worker('', editor=request)

    def start_worker(self, prompt, editor=None):
        self.stream_activity = 'starting'
        ctx = multiprocessing.get_context('fork')
        self.events = ctx.Queue()
        self.worker = ctx.Process(target=run_worker, args=(self.engine, self.args, self.runtime,
                                  self.messages, self.session, prompt, self.events, editor))
        self.worker.start()
        self.started = time.monotonic()

    def receive(self, event):
        if event['kind'] == 'editor_result':
            self.editor_bridge.complete(event.get('result'), event.get('error'))
            result = event.get('result')
            if result:
                self.record_snapshot(Snapshot('result', 'Neovim · awaiting review',
                    result.get('diff', result.get('answer', '')), {'editor': True}))
                self.append('\nNeovim response ready · review in editor; nothing applied.\n')
        elif event['kind'] == 'output':
            self.append(event['text'])
        elif event['kind'] == 'finished':
            self.messages, self.session = event['messages'], event['session']
            self.notice = f'Finished in {time.monotonic() - self.started:.1f}s'
            self.cleanup_worker()
        elif event['kind'] == 'checkpoint':
            self.messages = event['messages']
        elif event['kind'] == 'request':
            self.last_request_id = self.record_snapshot(request_snapshot(event))
            self.stream_started = False
        elif event['kind'] == 'stream':
            self.stream_activity = f'{event["chunks"]} chunks · {event["thinking_chars"]} thinking chars'
            if event['delta']:
                if not self.stream_started:
                    self.append('\n◆ answer\n')
                    self.stream_started = True
                self.append(event['delta'])
        elif event['kind'] == 'model_usage':
            if self.stream_started:
                self.append('\n')
            item = self.store.items.get(self.last_request_id)
            if item:
                from dataclasses import replace
                meta = {**item.metadata, 'usage': {key: val for key, val in event.items() if key != 'kind'}}
                self.store.add(replace(item, metadata=meta))
                if self.inspector_id == item.id and not self.store.pinned:
                    self.render_inspector()
        elif event['kind'] == 'tool_start':
            self.pending_tools[event['call_id']] = event
            self.stream_activity = f'Tool: {event["name"]}'
        elif event['kind'] == 'tool_complete':
            self.pending_tools.pop(event['call_id'], None)
            self.record_snapshot(result_snapshot(event))

    def cleanup_worker(self):
        if self.worker:
            self.worker.join(timeout=.5)
            if self.worker.is_alive():
                self.worker.terminate()
                self.worker.join(timeout=.5)
            if self.worker.is_alive():
                self.worker.kill()
                self.worker.join(timeout=.5)
            self.worker.close()
            self.worker = None
        if self.events:
            self.events.close()
            self.events = None

    def cancel(self):
        if self.worker:
            try:
                os.killpg(self.worker.pid, signal.SIGTERM)
            except ProcessLookupError:
                self.worker.terminate()
            self.cleanup_worker()
            for call in self.pending_tools.values():
                self.record_snapshot(Snapshot('result', call['name'] + ' · interrupted',
                    'Cancelled before a result was received. Side effects may have occurred.',
                    {'call_id': call['call_id'], 'arguments': call['arguments'], 'complete': False}))
            self.pending_tools.clear()
            self.notice = 'Cancelled'
            if self.editor_bridge:
                self.editor_bridge.complete(error='Cancelled; selection and prompt retained')
            self.append('\nStopped. Completed tool actions are not undone.\n')
        else:
            self.draft.reset()
            self.hidden = True

    async def pump(self):
        try:
            self.editor_bridge = EditorBridge(self.args.workspace, self.submit_editor, self.cancel, self.args.model,
                lambda request_id, outcome: self.append(
                    f'\nNeovim review {request_id[:8]}: {outcome}'
                    + (' to unsaved buffer.\n' if outcome == 'applied' else '.\n')))
            await self.editor_bridge.start()
        except (ValueError, OSError) as exc:
            if self.editor_bridge:
                self.editor_bridge.close()
            self.editor_bridge = None
            self.append(f'Editor bridge unavailable: {exc}\n')
        while True:
            if self.events:
                for _ in range(100):
                    try:
                        event = self.events.get_nowait()
                    except queue.Empty:
                        break
                    self.receive(event)
                    if not self.events:
                        break
                if self.worker and not self.worker.is_alive() and self.events.empty():
                    self.append('\nWorker exited before completion.\n')
                    if self.editor_bridge:
                        self.editor_bridge.complete(error='Worker exited; selection and prompt retained')
                    self.cleanup_worker()
            await asyncio.sleep(.03)

    def bind_keys(self):
        editing = Condition(lambda: self.app.layout.has_focus(self.input_control))
        reading = Condition(lambda: self.app.layout.has_focus(self.inspector)
                            or self.app.layout.has_focus(self.transcript))
        def scroll_reader(event, handler):
            window = event.app.layout.current_window
            before = event.current_buffer.document.cursor_position_row
            offset = window.vertical_scroll
            handler(event)
            # Move the viewport with the cursor, even when the cursor's new
            # position would still fit on the old page (Vim Ctrl-D/U behavior).
            moved = event.current_buffer.document.cursor_position_row - before
            window.vertical_scroll = max(0, offset + moved)
        for key, handler in [('c-d', scroll_half_page_down), ('c-u', scroll_half_page_up),
                             ('c-f', scroll_forward), ('c-b', scroll_backward)]:
            self.keys.add(key, filter=reading)(lambda event, handler=handler: scroll_reader(event, handler))
        @self.keys.add('j', filter=reading)
        def down(event): event.current_buffer.cursor_down()
        @self.keys.add('y', filter=reading)
        def copy(event):
            reader = self.inspector if self.app.layout.has_focus(self.inspector) else self.transcript
            self.copy_reader(reader)
        @self.keys.add('k', filter=reading)
        def up(event): event.current_buffer.cursor_up()
        @self.keys.add('h', filter=reading)
        def left(event): event.current_buffer.cursor_left()
        @self.keys.add('l', filter=reading)
        def right(event): event.current_buffer.cursor_right()
        @self.keys.add('g', 'g', filter=reading)
        def top(event): event.current_buffer.cursor_position = 0
        @self.keys.add('G', filter=reading)
        def bottom(event): event.current_buffer.cursor_position = len(event.current_buffer.text)
        @self.keys.add('0', filter=reading)
        def line_start(event):
            event.current_buffer.cursor_position += event.current_buffer.document.get_start_of_line_position()
        @self.keys.add('$', filter=reading)
        def line_end(event):
            event.current_buffer.cursor_position += event.current_buffer.document.get_end_of_line_position()
        @self.keys.add('enter', filter=editing)
        def enter(event): self.choose(submit=True)
        @self.keys.add('tab', filter=editing)
        def tab(event): self.choose()
        @self.keys.add('up', filter=editing)
        @self.keys.add('down', filter=editing)
        def arrow(event):
            delta = -1 if event.key_sequence[0].key == 'up' else 1
            if self.editor_navigation_active():
                choices = self.editor_navigation()[2]
                if choices:
                    self.selected = (self.selected + delta) % len(choices)
                return
            found = self.matches() if not self.hidden and not self.recalled and not self.active_tool() else []
            if found:
                self.selected = (self.selected + delta) % len(found)
            else:
                self.history_move(delta)
        @self.keys.add('right', filter=editing)
        def editor_right(event):
            if self.editor_navigation_active():
                self.choose_editor()
            else:
                event.current_buffer.cursor_right()
        @self.keys.add('left', filter=editing)
        def editor_left(event):
            if self.editor_navigation_active():
                self.editor_parent()
            else:
                event.current_buffer.cursor_left()
        @self.keys.add('escape')
        def escape(event):
            self.hidden = True
            self.app.layout.focus(self.input_control)
        @self.keys.add('f1', filter=editing)
        def help_(event):
            self.expanded = not self.expanded if not self.hidden else True
            self.hidden = False
            self.help_offset = 0
        @self.keys.add('pageup', filter=editing)
        @self.keys.add('pagedown', filter=editing)
        def page(event):
            delta = -6 if event.key_sequence[0].key == 'pageup' else 6
            if self.editor_navigation_active():
                count = len(self.editor_navigation()[2])
                self.selected = max(0, min(count - 1, self.selected + delta)) if count else 0
            elif self.active_tool() and not self.hidden:
                self.help_offset = max(0, self.help_offset + delta)
            else:
                self.app.layout.focus(self.transcript)
                self.transcript.buffer.cursor_up(count=8) if delta < 0 else self.transcript.buffer.cursor_down(count=8)
        @self.keys.add('f6')
        def focus(event):
            if self.app.layout.has_focus(self.input_control):
                self.app.layout.focus(self.inspector if self.inspector_visible else self.transcript)
            elif self.app.layout.has_focus(self.inspector) and self.app.output.get_size().columns >= 100:
                self.app.layout.focus(self.transcript)
            else:
                self.app.layout.focus(self.input_control)
        for key, mode in [('f2', 'docs'), ('f3', 'result'), ('f4', 'context'), ('f5', 'file')]:
            self.keys.add(key)(lambda event, mode=mode: self.switch_view(mode))
        @self.keys.add('f7')
        def pin(event): self.pin()
        @self.keys.add('f8')
        def attach(event):
            doc = self.inspector.buffer.document
            start, end = doc.selection_range() if doc.selection else (doc.cursor_position, doc.cursor_position)
            first = doc.translate_index_to_position(start)[0] - self.inspector_body_start + 1
            last = doc.translate_index_to_position(end)[0] - self.inspector_body_start + 1
            try: self.attach(first, last)
            except ValueError as exc: self.notice = str(exc)
        @self.keys.add('f9')
        def pane(event): self.toggle_pane()
        @self.keys.add('escape', 'left')
        def older(event): self.cycle_snapshot(-1)
        @self.keys.add('escape', 'right')
        def newer(event): self.cycle_snapshot(1)
        @self.keys.add('escape', '-')
        def smaller(event): self.inspector_ratio = max(.3, self.inspector_ratio - .05)
        @self.keys.add('escape', '+')
        def larger(event): self.inspector_ratio = min(.7, self.inspector_ratio + .05)
        @self.keys.add('c-c')
        def cancel(event): self.cancel()
        @self.keys.add('c-d', filter=editing)
        def quit_(event):
            if not self.draft.text:
                self.cancel()
                self.app.exit()
            else:
                self.draft.delete()

    def run(self):
        try:
            self.app.run(pre_run=lambda: self.app.create_background_task(self.pump()))
        finally:
            if self.worker:
                self.cancel()
            if self.editor_bridge:
                self.editor_bridge.close()
