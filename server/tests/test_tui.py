"""Managed terminal acceptance: actual ANSI paints, fixed cursor, async tools."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import pyte
import pytest
from test_cli_tools import ROOT, installed, local, runtime_for
from test_inspector import model_server


class ManagedTerminal:
    def __init__(self, installed, workspace, mode='single', columns=100, rows=28, extra_env=None):
        self.master, self.slave = pty.openpty()
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.resize(columns, rows)
        self.original = termios.tcgetattr(self.slave)
        env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
        env.update(TERM='xterm-256color', BOPBOP_OLLAMA_HOST='http://127.0.0.1:1')
        env.pop('NO_COLOR', None)
        env.update(extra_env or {})
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'bin/bopbop-local.py'),
            '--ui', mode, '--banner', 'off', '--color', 'always', '--workspace', str(workspace),
            '--context-dir', str(installed.parents[1]), '--packs', 'test'],
            stdin=self.slave, stdout=self.slave, stderr=self.slave, env=env)

    @property
    def text(self): return '\n'.join(self.screen.display)

    def resize(self, columns, rows):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        self.screen.resize(rows, columns)

    def send(self, keys): os.write(self.master, keys.encode() if isinstance(keys, str) else keys)

    def wait(self, text, absent=None):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], .08)[0]:
                self.stream.feed(os.read(self.master, 65536).decode('utf-8', errors='replace'))
            if text in self.text and (absent is None or absent not in self.text):
                # Allow a complete repaint before inspecting physical cursor coordinates.
                settle = time.monotonic() + .15
                while time.monotonic() < settle and select.select([self.master], [], [], .03)[0]:
                    self.stream.feed(os.read(self.master, 65536).decode('utf-8', errors='replace'))
                return self.text
        pytest.fail(f'Missing {text!r} / unexpected {absent!r}:\n{self.text}')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        os.close(self.master); os.close(self.slave)


def test_managed_prompt_stays_anchored_and_restores_transcript(installed, tmp_path):
    terminal = ManagedTerminal(installed, tmp_path)
    try:
        terminal.wait('Ready')
        row = terminal.screen.cursor.y
        original = terminal.screen.display[:row-1]
        assert row == 26
        terminal.send('/')
        terminal.wait('Commands')
        assert terminal.screen.cursor.y == row
        terminal.send('ca')
        terminal.wait('/ca')
        assert terminal.screen.cursor.y == row
        terminal.send('\x1b')
        terminal.wait('Ready', absent='Commands')
        assert terminal.screen.cursor.y == row
        assert terminal.screen.display[:row-1] == original
        terminal.send('\x15/call sample_count{"wi')
        terminal.wait('Observation window')
        assert terminal.screen.cursor.y == row
        terminal.send('ndow":"7')
        terminal.wait('Matching values: "7d"')
        assert terminal.screen.cursor.y == row
        terminal.send(b'\x1bOP')
        terminal.wait('Notes')
        assert terminal.screen.cursor.y == row
        terminal.send('\x1b')
        terminal.wait('Ready', absent='call help')
        terminal.send('\x15/exit\r')
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert termios.tcgetattr(terminal.slave) == terminal.original
        assert not (installed / 'called').exists()
    finally: terminal.close()


def test_managed_tool_worker_keeps_draft_and_history_live(installed, tmp_path):
    handler = installed / 'handler.py'
    handler.write_text('import time\ntime.sleep(.6)\n' + handler.read_text())
    terminal = ManagedTerminal(installed, tmp_path)
    try:
        terminal.wait('Ready')
        command = '/call sample_count {"window":"24h"}'
        terminal.send(command + '\r')
        terminal.wait('Working')
        terminal.send('unfinished café')
        terminal.wait('unfinished café')
        terminal.wait('Finished in')
        assert 'unfinished café' in terminal.text
        assert '0 model tokens' in terminal.text
        terminal.send(b'\x1b[A')
        terminal.wait(command)
        terminal.send(b'\x1b[B')
        terminal.wait('unfinished café')
        terminal.resize(48, 14)
        terminal.send('!')
        terminal.wait('café!')
        assert terminal.screen.cursor.y == 12
        terminal.send('\x15/exit\r')
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert (installed / 'called').exists()
    finally: terminal.close()


def test_inspector_pin_history_and_narrow_layout(installed, tmp_path):
    terminal = ManagedTerminal(installed, tmp_path, mode='split', columns=140)
    try:
        terminal.wait('DOCS')
        terminal.send(b'\x1bOR')  # F3
        terminal.wait('No result snapshot')
        terminal.send('/call sample_count {"window":"24h","limit":7}\r')
        terminal.wait('Finished in')
        terminal.wait('Complete returned tool result')
        terminal.send(b'\x1b[18~')  # F7
        terminal.wait('PINNED')
        pane = [line[76:] for line in terminal.screen.display[1:24]]
        terminal.send('/call sample_count {"window":"7d","limit":9}\r')
        terminal.wait('"count": 9')
        terminal.wait('Finished in')
        assert [line[76:] for line in terminal.screen.display[1:24]] == pane
        terminal.send(b'\x1b\x1b[C')  # Alt-Right, explicit newer result while pinned
        terminal.wait('"limit": 9')
        terminal.resize(65, 18); terminal.send('x')
        terminal.wait('PINNED')
        assert terminal.screen.cursor.y == 16
        terminal.send('\x15/exit\r'); terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
    finally: terminal.close()


def test_file_view_only_attaches_selected_lines_to_actual_request(installed, tmp_path, model_server):
    url, requests = model_server
    (tmp_path / 'notes.txt').write_text('private unselected line\nselected evidence\nother unselected line\n')
    terminal = ManagedTerminal(installed, tmp_path, mode='split', columns=140,
                               extra_env={'BOPBOP_OLLAMA_HOST':url})
    try:
        terminal.wait('Ready')
        terminal.send('/inspect file notes.txt\r')
        terminal.wait('File snapshot ready')
        assert not requests
        terminal.send('/attach 2:2\r')
        terminal.wait('1 attachments')
        terminal.send('Summarize the selected evidence\r')
        terminal.wait('◆ answer')
        terminal.send('next draft')
        terminal.wait('next draft')
        terminal.wait('Fixture answer.')
        terminal.wait('Finished in')
        assert len(requests) == 1
        content = requests[0]['messages'][-1]['content']
        assert 'selected evidence' in content and '"lines": [2, 2]' in content
        assert 'private unselected line' not in json.dumps(requests)
        assert 'other unselected line' not in json.dumps(requests)
        terminal.send(b'\x1bOS')  # F4 Context
        terminal.wait('Exact outbound JSON')
        assert 'next draft' in terminal.text
        terminal.send('\x15/exit\r'); terminal.process.wait(timeout=5)
    finally: terminal.close()


def test_plain_engine_loads_without_optional_site_packages(tmp_path):
    result = subprocess.run([sys.executable, '-S', str(ROOT / 'bin/bopbop-local.py'),
                             '--ui', 'plain', '--banner', 'off', '--workspace', str(tmp_path),
                             '--packs', '', '/tools'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert 'read_file' in result.stdout + result.stderr
    assert 'prompt_toolkit' not in result.stderr


def test_inspector_keyboard_selection_attaches_body_line(installed, tmp_path):
    (tmp_path / 'note.txt').write_text('first\nchosen second\nlast\n')
    terminal = ManagedTerminal(installed, tmp_path, mode='split', columns=140)
    try:
        terminal.wait('Ready')
        terminal.send('/inspect file note.txt\r')
        terminal.wait('File snapshot ready')
        terminal.send(b'\x1b[17~')  # F6 focuses inspector
        terminal.send(b'\x1b[B' * 6)  # five header rows, then second body line
        terminal.send(b'\x1b[19~')  # F8 selects current body line
        terminal.wait('Attached File · note.txt lines 2–2')
        terminal.send('\x1b')
        terminal.send('/detach\r'); terminal.wait('Attachments cleared')
        terminal.send('/exit\r'); terminal.process.wait(timeout=5)
    finally: terminal.close()


def test_cancel_reaps_pack_process_group_and_preserves_editable_draft(installed, tmp_path):
    path = installed / 'handler.py'
    path.write_text('import os,time\nfrom pathlib import Path\nPath("pid").write_text(str(os.getpid()))\ntime.sleep(20)\n')
    definition = json.loads((installed / 'tools.json').read_text())
    definition['tools'][0]['timeout_seconds'] = 30
    (installed / 'tools.json').write_text(json.dumps(definition))
    terminal = ManagedTerminal(installed, tmp_path)
    try:
        terminal.wait('Ready')
        terminal.send('/call sample_count {"window":"24h"}\r')
        terminal.wait('Tool: sample_count')
        deadline = time.monotonic() + 2
        while not (installed / 'pid').exists() and time.monotonic() < deadline: time.sleep(.02)
        pid = int((installed / 'pid').read_text())
        terminal.send('keep this draft\x03')
        terminal.wait('Cancelled')
        assert 'keep this draft' in terminal.text
        with pytest.raises(ProcessLookupError): os.kill(pid, 0)
        terminal.send('\x15/exit\r'); terminal.process.wait(timeout=5)
        assert termios.tcgetattr(terminal.slave) == terminal.original
    finally: terminal.close()
