"""Optional managed terminal. Engine work lives in a cancellable child process.

Only this module imports prompt_toolkit. No UI state is written to disk.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import multiprocessing
import os
import queue
import re
import signal
import time

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (Layout, HSplit, Window, FloatContainer, Float,
                                   ConditionalContainer)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea, SearchToolbar

from bopbop_tool_ui import (argument_focus, call_help_lines, call_tool_name,
                            example_arguments, find_tool, reference_lines, safe_text,
                            tool_entries)


STYLE = Style.from_dict({
    '': '#e6eaf0 bg:#141b24',
    'brand': '#78becb bold', 'muted': '#82909d', 'rule': '#344350',
    'selected': '#141b24 bg:#78becb bold', 'tool': '#dfbe83 bold',
    'menu': 'bg:#202b38', 'status': '#82909d', 'input': '#e6eaf0',
    'search': '#dfbe83', 'text-area': '#e6eaf0',
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


def run_worker(engine, args, runtime, messages, session, prompt, events):
    os.setsid()
    def stop(signum, frame):
        # Unwind native pack handlers so their own process groups are reaped.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    send = events.put
    writer = EventWriter(send)
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            display = engine.Display(False)
            if not engine.handle_command(prompt, messages, session, args.model, args.context,
                                          args.workspace, display, runtime):
                messages.append({'role': 'user', 'content': prompt})
                answer, usage = engine.turn(args.model, messages, args.workspace, args.context,
                                            display, runtime=runtime)
                session.record(usage)
                display.answer(answer)
    except KeyboardInterrupt:
        send({'kind': 'output', 'text': '\nTurn cancelled.\n'})
    except Exception as exc:
        send({'kind': 'output', 'text': f'\nError: {type(exc).__name__}: {exc}\n'})
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
        self.search = SearchToolbar()
        self.transcript = TextArea(text='Type / for commands. F6 focuses conversation; Ctrl-R searches.\n',
                                   read_only=True, scrollbar=True, wrap_lines=True,
                                   search_field=self.search)
        self.draft = Buffer(multiline=False)
        self.draft.on_text_changed += self.changed
        self.draft.on_cursor_position_changed += self.cursor_changed
        self.input_control = BufferControl(buffer=self.draft)
        self.input_window = Window(self.input_control, height=1, wrap_lines=False,
                                   get_line_prefix=lambda *unused: [('class:brand', 'bopbop> ')])
        self.keys = KeyBindings()
        self.bind_keys()
        self.body = self.transcript
        self.root = FloatContainer(HSplit([
            Window(FormattedTextControl(self.header), height=1), self.body,
            self.search, Window(height=1, char='─', style='class:rule'),
            self.input_window, Window(FormattedTextControl(self.footer), height=1),
        ]), floats=[Float(left=0, right=0, bottom=3, height=self.popup_height,
                         content=ConditionalContainer(Window(
                             FormattedTextControl(self.popup_text), style='class:menu',
                             wrap_lines=False), filter=Condition(lambda: self.popup_height() > 0)))])
        self.app = Application(layout=Layout(self.root, focused_element=self.input_control),
                               key_bindings=self.keys, full_screen=True, mouse_support=False,
                               style=STYLE if self.display.color else Style.from_dict({}),
                               input=input, output=output, refresh_interval=.1)
        self.app.ttimeoutlen = .05

    def header(self):
        return [('class:brand', ' ● bopbop  '), ('class:muted', f'{self.args.model} · local agent')]

    def footer(self):
        state = f'Working {time.monotonic() - self.started:.1f}s · Ctrl-C stop' if self.worker else self.notice
        return [('class:status', f' {state}  · F6 conversation · F1 help · Ctrl-D quit')]

    def matches(self):
        text = self.draft.text
        parts = text.split(maxsplit=1)
        if parts and parts[0] in {'/call', '/tools'} and ' ' in text:
            prefix = parts[1] if len(parts) > 1 else ''
            return [(f'{parts[0]} {definition["name"]}', definition['description'])
                    for definition, _ in tool_entries(self.runtime)
                    if definition['name'].startswith(prefix)]
        return [(name, description) for name, description in self.engine.SLASH_COMMANDS
                if text.startswith('/') and name.startswith(text.lower())]

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

    def popup_lines(self):
        if self.hidden or not self.app.layout.has_focus(self.input_control):
            return []
        width = max(12, self.app.output.get_size().columns - 4)
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
        found = self.matches() if not self.hidden and not self.recalled else []
        if found:
            chosen = found[self.selected][0]
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
        if self.worker:
            self.notice = 'Still working; draft kept. Ctrl-C stops the active turn.'
            return
        if prompt == '/exit':
            self.app.exit()
            return
        self.history.append(self.draft.text)
        self.history = self.history[-500:]
        self.history_index = len(self.history)
        self.history_edits = {}
        self.draft.reset()
        self.append('\n› ' + prompt + '\n')
        if prompt.startswith('/tools'):
            parts = prompt.split(maxsplit=1)
            if len(parts) == 1:
                self.append('\n'.join(f'{d["name"]}: {d["description"]}' for d, _ in tool_entries(self.runtime)) + '\n')
            else:
                try:
                    self.append('\n'.join(reference_lines(self.runtime, parts[1], self.engine.Display(False))) + '\n')
                except ValueError as exc:
                    self.append(str(exc) + '\n')
            return
        self.start_worker(prompt)

    def start_worker(self, prompt):
        ctx = multiprocessing.get_context('fork')
        self.events = ctx.Queue()
        self.worker = ctx.Process(target=run_worker, args=(self.engine, self.args, self.runtime,
                                  self.messages, self.session, prompt, self.events))
        self.worker.start()
        self.started = time.monotonic()

    def receive(self, event):
        if event['kind'] == 'output':
            self.append(event['text'])
        elif event['kind'] == 'finished':
            self.messages, self.session = event['messages'], event['session']
            self.notice = f'Finished in {time.monotonic() - self.started:.1f}s'
            self.cleanup_worker()

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
            self.notice = 'Cancelled'
            self.append('\nStopped. Completed tool actions are not undone.\n')
        else:
            self.draft.reset()
            self.hidden = True

    async def pump(self):
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
                    self.cleanup_worker()
            await asyncio.sleep(.03)

    def bind_keys(self):
        editing = Condition(lambda: self.app.layout.has_focus(self.input_control))
        @self.keys.add('enter', filter=editing)
        def enter(event): self.choose(submit=True)
        @self.keys.add('tab', filter=editing)
        def tab(event): self.choose()
        @self.keys.add('up', filter=editing)
        @self.keys.add('down', filter=editing)
        def arrow(event):
            delta = -1 if event.key_sequence[0].key == 'up' else 1
            found = self.matches() if not self.hidden and not self.recalled and not self.active_tool() else []
            if found:
                self.selected = (self.selected + delta) % len(found)
            else:
                self.history_move(delta)
        @self.keys.add('escape', eager=True)
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
            if self.active_tool() and not self.hidden:
                self.help_offset = max(0, self.help_offset + delta)
            else:
                self.app.layout.focus(self.transcript)
                self.transcript.buffer.cursor_up(count=8) if delta < 0 else self.transcript.buffer.cursor_down(count=8)
        @self.keys.add('f6')
        def focus(event):
            self.app.layout.focus(self.input_control if self.app.layout.has_focus(self.transcript) else self.transcript)
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
