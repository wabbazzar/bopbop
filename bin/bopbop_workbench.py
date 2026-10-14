"""Workbench navigation and bounded view transport; Neovim owns live buffers."""
from __future__ import annotations

import os
import subprocess

from bopbop_inspector import Snapshot
from bopbop_tool_ui import tool_entries


class Workbench:
    def __init__(self, ui):
        self.ui = ui
        self.pane = None
        self.parked = False
        self.connected = False
        self.revision = 0
        self.view = {'kind': 'source'}
        self.text = ''

    @property
    def active(self):
        return self.connected and (self.pane is not None or self.ui.editor_task is not None)

    def tmux(self, *args):
        return subprocess.run(['tmux', *args], capture_output=True, text=True, timeout=3, check=True)

    def alive(self):
        if not self.pane:
            return False
        try:
            if self.tmux('display-message', '-p', '-t', self.pane, '#{pane_id}').stdout.strip() == self.pane:
                return True
            raise subprocess.CalledProcessError(1, 'pane no longer exists')
        except subprocess.CalledProcessError:
            self.pane = None
            self.connected = self.parked = False
            return False

    def reveal(self, focus=True):
        if not self.alive():
            return False
        if self.parked:
            self.tmux('join-pane', '-h', '-s', self.pane, '-t', os.environ['TMUX_PANE'])
            self.parked = False
        if focus:
            self.tmux('select-pane', '-t', self.pane)
        return True

    def toggle(self):
        if not self.alive():
            return False
        if self.parked:
            self.reveal()
        else:
            # A detached tmux window preserves Neovim, undo, selections and dirty buffers.
            self.tmux('break-pane', '-d', '-s', self.pane, '-n', 'bopbop-workbench')
            self.parked = True
            self.tmux('select-pane', '-t', os.environ['TMUX_PANE'])
        return True

    def publish(self, kind, **data):
        self.revision += 1
        self.view = {'kind': kind, **data}

    def snapshot(self):
        ui = self.ui
        item = ui.store.items.get(ui.inspector_id)
        self.text = ui.inspector.text
        self.publish('snapshot', snapshot=ui.inspector_id, category=ui.inspector_mode,
                     title=item.title if item else ui.inspector_mode,
                     body_start=ui.inspector_body_start,
                     body_lines=len(item.content().splitlines()) if item else 0,
                     path=item.metadata.get('path') if item and item.kind == 'file' else None,
                     pinned=bool(ui.store.pinned))

    def attachment(self, data):
        item = self.ui.store.items.get(data.get('snapshot'))
        if not item:
            raise ValueError('Snapshot expired; open the view again')
        first, last = data.get('first'), data.get('last')
        if type(first) is not int or type(last) is not int:
            raise ValueError('Select numbered body lines')
        return item.attachment(first, last)

    def request(self, data):
        ui = self.ui
        action = data.get('action')
        if action == 'sync':
            self.connected = True
            ui.inspector_visible = False
            ui.app.invalidate()
            return ({'revision': self.revision, 'view': self.view}
                    if data.get('after') != self.revision else {'revision': self.revision})
        if action == 'read':
            if data.get('revision') != self.revision:
                raise ValueError('View changed during loading; select it again')
            offset = data.get('offset', 0)
            if type(offset) is not int or not 0 <= offset <= len(self.text):
                raise ValueError('Invalid view offset')
            # Character chunks stay below the wire bound even with JSON escaping.
            end = min(offset + 16384, len(self.text))
            return {'text': self.text[offset:end], 'next': end if end < len(self.text) else None}
        if action == 'tools':
            return {'tools': [{'name': d['name'], 'description': d['description'][:240]}
                              for d, _ in tool_entries(ui.runtime)]}
        if action == 'reviewing':
            self.publish('review')
            return {'status': 'ok'}
        if action == 'restore':
            item = ui.store.items.get(data.get('snapshot'))
            if not item:
                raise ValueError('Snapshot expired; use F5 to return to source')
            ui.select_snapshot(item)
            return {'status': 'ok'}
        if action == 'source':
            self.publish('source')
            return {'status': 'ok'}
        if action == 'docs':
            ui.show_docs(data.get('name', ''))
        elif action == 'attach':
            ui.add_attachment(self.attachment(data))
        elif action == 'prepare':
            if data.get('request', {}).get('action') != 'explain':
                raise ValueError('Open the source file to request an edit')
            request = data['request']
            region = request.get('range')
            if not isinstance(region, list) or len(region) != 4 or any(type(n) is not int for n in region):
                raise ValueError('Select snapshot body lines')
            attachment = self.attachment({'snapshot': request.get('snapshot'),
                                          'first': region[0] + 1, 'last': region[2]})
            prompt = request.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 8192:
                raise ValueError('Enter a question of at most 8 KiB')
            return dict(action='explain', mode='evidence', path=attachment['source'],
                        text=attachment['content'], prompt=prompt, range=region,
                        snapshot=attachment['snapshot'], sha256=attachment['sha256'])
        elif action == 'attach_source':
            from bopbop_editor import validate_request
            request = validate_request(data.get('request'), ui.args.workspace)
            item = Snapshot('file', 'Unsaved selection · ' + request['path'], request['text'], request)
            ui.store.add(item)
            ui.add_attachment(item.attachment(1, len(item.content().splitlines())))
        elif action == 'open':
            path = data.get('path')
            if not isinstance(path, str):
                raise ValueError('Choose a workspace file')
            ui.open_editor(path)
        elif action == 'older' or action == 'newer':
            ui.cycle_snapshot(-1 if action == 'older' else 1)
        else:
            raise ValueError('Unknown workbench action')
        return {'status': 'ok', 'message': ui.notice}
