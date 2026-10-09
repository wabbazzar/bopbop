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


class ManagedTerminal:
    def __init__(self, installed, workspace, mode='single', columns=100, rows=28, extra_env=None):
        self.master, self.slave = pty.openpty()
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.resize(columns, rows)
        self.original = termios.tcgetattr(self.slave)
        env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
        env.update(TERM='xterm-256color', BOPBOP_OLLAMA_HOST='http://127.0.0.1:1')
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
