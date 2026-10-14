"""Managed terminal acceptance: actual ANSI paints, fixed cursor, async tools."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid

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
        terminal.send('\x15/tools_extra\r')
        terminal.wait('Unknown command')
        terminal.wait('Finished in')
        terminal.send('\x15/exit\r')
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert termios.tcgetattr(terminal.slave) == terminal.original
        assert not (installed / 'called').exists()
    finally: terminal.close()


def test_editor_file_reader_browses_filters_previews_and_opens_without_moving_input(installed, tmp_path):
    folder = tmp_path / 'alpha'
    folder.mkdir()
    (folder / 'zeta file.py').write_text('def banana():\n    return 42\n')
    (folder / 'another.py').write_text('print("other")\n')
    outside = tmp_path.parent / (tmp_path.name + '-outside.py')
    outside.write_text('secret outside workspace\n')
    (tmp_path / 'escape.py').symlink_to(outside)
    terminal = ManagedTerminal(installed, tmp_path, extra_env={'TMUX': ''})
    try:
        terminal.wait('Ready')
        row = terminal.screen.cursor.y
        terminal.send('/ed')
        terminal.wait('Browse workspace files')
        terminal.send('\t')  # Completing the slash command opens its live file menu.
        terminal.wait('Files · ./')
        terminal.send('\x15')
        terminal.send('/editor')
        terminal.wait('Files · ./')
        assert terminal.screen.cursor.y == row
        terminal.send(' alpha')
        terminal.wait('▸ alpha/')
        assert terminal.screen.cursor.y == row
        terminal.send(b'\x1b[C')  # Right enters the selected directory.
        terminal.wait('Files · alpha/')
        assert terminal.screen.cursor.y == row
        terminal.send(b'\x1b[B')  # Select second file to update the preview.
        terminal.wait('Preview · zeta file.py')
        terminal.wait('def banana():')
        terminal.send(b'\x1b[D')  # Left returns to workspace root.
        terminal.wait('Files · ./')
        terminal.send('alpha')
        terminal.wait('▸ alpha/')
        terminal.send('\t')
        terminal.wait('Files · alpha/')
        terminal.send('zeta')
        terminal.wait('Preview · zeta file.py')
        terminal.send('\r')
        terminal.wait('BopBop: Connected')
        terminal.send(':q!\r')
        terminal.wait('Back from Neovim')
        assert terminal.screen.cursor.y == row
        terminal.send('/editor escape')
        terminal.wait('No matches')  # Workspace-exiting symlink is absent.
        terminal.send('\x1b')
        terminal.wait('Back from Neovim', absent='No matches')
        terminal.send('\x15/help\r')
        terminal.wait('◆ editor browser')
        terminal.wait('<leader>ba apply')
        terminal.wait('Drag in a reader')
        terminal.send('/exit\r'); terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
    finally: terminal.close()


def test_editor_file_reader_accepts_typed_paths_and_paging(installed, tmp_path):
    folder = tmp_path / 'many files'
    folder.mkdir()
    for index in range(25):
        (folder / f'file{index:02}.py').write_text(f'print({index})\n')
    terminal = ManagedTerminal(installed, tmp_path, columns=70, rows=18, extra_env={'TMUX': ''})
    try:
        terminal.wait('Ready')
        row = terminal.screen.cursor.y
        terminal.send('/editor many files/')
        terminal.wait('Files · many files/')
        terminal.send(b'\x1b[6~')  # PageDown scrolls candidates, not the transcript.
        terminal.wait('file06.py')
        assert terminal.screen.cursor.y == row
        terminal.send('\x1b')
        terminal.wait('Ready', absent='Files · many files/')
        terminal.send('\x15/editor "many files/file24.py"\r')
        terminal.wait('BopBop: Connected')
        terminal.send(':q!\r')
        terminal.wait('Back from Neovim')
        terminal.send('/exit\r'); terminal.process.wait(timeout=5)
    finally: terminal.close()


def test_mouse_drag_and_y_copy_conversation_to_system_clipboard(installed, tmp_path):
    clipboard = tmp_path / 'copied.txt'
    commands = tmp_path / 'commands'
    commands.mkdir()
    helper = commands / 'xclip'
    helper.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                      'open(os.environ["TEST_CLIPBOARD_PATH"], "wb").write(sys.stdin.buffer.read())\n')
    helper.chmod(0o755)
    terminal = ManagedTerminal(installed, tmp_path, extra_env={
        'TMUX': '', 'DISPLAY': ':99', 'PATH': str(commands) + os.pathsep + os.environ['PATH'],
        'TEST_CLIPBOARD_PATH': str(clipboard)})
    try:
        terminal.wait('Ready')
        terminal.send(b'\x1b[<0;1;2M\x1b[<32;8;2M\x1b[<0;8;2m')
        terminal.wait('copied to system clipboard')
        assert clipboard.read_text().startswith('Type /')
        terminal.send(b'\x1b[<0;1;2M')  # A new click clears the selected range.
        terminal.send('y')
        terminal.wait('Type / for commands.', absent='7 bytes copied')
        assert clipboard.read_text().startswith('Type / for commands.')
        terminal.send('\x1b')
        terminal.send('/exit\r'); terminal.process.wait(timeout=5)
    finally: terminal.close()


def test_tmux_editor_split_connects_and_mouse_drag_copies(installed, tmp_path):
    if not shutil.which('tmux') or not shutil.which('nvim'):
        pytest.skip('tmux and Neovim are required')
    (tmp_path / 'example.py').write_text('print(42)\n')
    clipboard = tmp_path / 'copied.txt'
    commands = tmp_path / 'commands'
    commands.mkdir()
    helper = commands / 'xclip'
    helper.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                      'open(os.environ["TEST_CLIPBOARD_PATH"], "wb").write(sys.stdin.buffer.read())\n')
    helper.chmod(0o755)
    name = 'bopbop-test-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='bop-ed-') as bridge:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 140, 0, 0))
        env = {key: val for key, val in os.environ.items() if key != 'TMUX' and not key.startswith('BOPBOP_')}
        env.update(TERM='xterm-256color', BOPBOP_EDITOR_DIR=bridge, DISPLAY=':99',
                   PATH=str(commands) + os.pathsep + env['PATH'], TEST_CLIPBOARD_PATH=str(clipboard))
        command = shlex.join([sys.executable, str(ROOT / 'bin/bopbop-local.py'),
            '--ui', 'single', '--banner', 'off', '--workspace', str(tmp_path),
            '--context-dir', str(installed.parents[1]), '--packs', 'test'])
        process = subprocess.Popen(['tmux', '-L', name, '-f', '/dev/null', 'new-session',
                                    '-s', 'ride', command], stdin=slave, stdout=slave,
                                   stderr=slave, env=env)
        def tmux(*args):
            return subprocess.check_output(['tmux', '-L', name, *args], env=env, text=True,
                                           stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            ready = False
            while time.monotonic() < deadline:
                try:
                    ready = 'bopbop>' in tmux('capture-pane', '-p', '-t', 'ride:0.0')
                except subprocess.CalledProcessError:
                    pass  # The isolated tmux server is still starting.
                if ready:
                    break
                time.sleep(.05)
            assert ready
            tmux('set-option', '-g', 'mouse', 'on')
            tmux('send-keys', '-t', 'ride:0.0', '-l', '/editor example.py')
            tmux('send-keys', '-t', 'ride:0.0', 'Enter')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                panes = tmux('list-panes', '-t', 'ride', '-F', '#{pane_current_command}')
                if 'nvim' in panes:
                    break
                time.sleep(.05)
            assert 'nvim' in panes
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                right = tmux('capture-pane', '-p', '-t', 'ride:0.1')
                if 'print(42)' in right and '● bopbop · connected' in right:
                    break
                time.sleep(.05)
            assert 'print(42)' in right and '● bopbop · connected' in right
            assert 'Press ENTER or type command to continue' not in right
            os.write(master, b'\x1b[<0;1;2M\x1b[<32;8;2M\x1b[<0;8;2m')
            deadline = time.monotonic() + 3
            while not clipboard.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert clipboard.read_text() == 'Type / '
            assert 'copied to system clipboard' in tmux('capture-pane', '-p', '-t', 'ride:0.0')
        finally:
            subprocess.run(['tmux', '-L', name, 'kill-server'], env=env, capture_output=True)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            os.close(master); os.close(slave)


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


@pytest.mark.parametrize('columns', [65, 140])
def test_vim_reader_navigation_scrolls_viewport_and_preserves_chat_input(installed, tmp_path, columns):
    (tmp_path / 'long.txt').write_text(''.join(f'row {n:03}\n' for n in range(1, 101)))
    terminal = ManagedTerminal(installed, tmp_path, mode='split', columns=columns)
    try:
        terminal.wait('Ready')
        terminal.send('/inspect file long.txt\r')
        terminal.wait('File snapshot ready')
        terminal.send('draft hjkl gg G')
        terminal.wait('draft hjkl gg G')
        terminal.send(b'\x1b[17~')  # F6: reader gets focus
        terminal.wait('Inspector focused')
        # First Ctrl-D must move visible content, not just the cursor within it.
        terminal.send('\x04')
        terminal.wait('row 025')
        pane_start = 0 if columns < 100 else 76
        assert 'File · long.txt' not in terminal.screen.display[2][pane_start:]
        assert terminal.process.poll() is None  # never the input's Ctrl-D exit
        terminal.send('\x15')
        terminal.wait('File · long.txt')
        terminal.send('G')
        terminal.wait('Snapshot metadata (not attachment lines)')
        terminal.send('gg')
        terminal.wait('File · long.txt')
        terminal.send('jjjjjj')  # second numbered body line, after file header
        terminal.send(b'\x1b[19~')
        terminal.wait('Attached File · long.txt lines 2–2')
        terminal.send('\x1b')
        terminal.wait('draft hjkl gg G')
        terminal.send('\x15')  # Ctrl-U in input still clears the draft
        terminal.send('chat hjkl gg G')
        terminal.wait('bopbop> chat hjkl gg G')
        terminal.send('\x15/exit\r')
        terminal.process.wait(timeout=5)
        assert termios.tcgetattr(terminal.slave) == terminal.original
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
