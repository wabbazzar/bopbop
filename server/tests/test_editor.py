"""Real Neovim API/Visual selections and real managed-TUI/socket/model integration."""
import asyncio
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import threading
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_cli_tools import ROOT, installed, local, runtime_for
from test_tui import ManagedTerminal
from bopbop_editor import EditorBridge, validate_request, edit_result, private_dir, sessions

NVIM = shutil.which('nvim')


def request(path, **changes):
    return dict(action='edit', text='old', prompt='replace it', path=str(path), mode='char',
                range=[0, 0, 0, 3], changedtick=1, **changes)


def rpc(bridge, op, client, rid=None, **extra):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(str(bridge.socket))
        sock.sendall(json.dumps(dict(session=bridge.id, client=client, op=op, id=rid, **extra)).encode() + b'\n')
        chunks = b''
        while not chunks.endswith(b'\n'):
            chunks += sock.recv(262144)
        return json.loads(chunks)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    socket_root = tempfile.TemporaryDirectory(prefix='bop-ed-')
    monkeypatch.setenv('BOPBOP_EDITOR_DIR', socket_root.name)
    loop = asyncio.new_event_loop()
    received = []
    holder = {}
    def submit(value):
        received.append(value)
        loop.call_later(.05, lambda: holder['bridge'].complete(edit_result(
            json.dumps({'replacement': 'NEW', 'summary': 'Fixture change'}), value)))
    value = EditorBridge(tmp_path, submit, lambda: None, 'fixture')
    holder['bridge'] = value
    loop.run_until_complete(value.start())
    thread = threading.Thread(target=loop.run_forever); thread.start()
    yield value, received
    loop.call_soon_threadsafe(value.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()
    socket_root.cleanup()


def nvim_script(tmp_path, body, bridge=None, terminal=None, timeout=15):
    if not NVIM:
        pytest.skip('Neovim 0.11+ required for real editor acceptance')
    script = tmp_path / ('test-' + uuid.uuid4().hex + '.lua')
    script.write_text('''
local function eq(expected, actual)
  assert(vim.deep_equal(expected, actual), vim.inspect({expected=expected, actual=actual}))
end
local b = require('bopbop')
local function select_region(mode, first, last)
  vim.cmd('normal! ' .. vim.api.nvim_replace_termcodes('<Esc>', true, false, true))
  vim.api.nvim_win_set_cursor(0, first)
  vim.cmd('normal! ' .. mode)
  vim.api.nvim_win_set_cursor(0, last)
end
local function wait_for(state, timeout)
  assert(vim.wait(timeout or 10000, function() return b.state == state end, 10), b.state .. ': ' .. (b.message or ''))
end
local ok, err = xpcall(function()
''' + body + '''
end, debug.traceback)
if not ok then io.stderr:write(err); vim.cmd('cquit 1') else vim.cmd('qa!') end
''')
    env = os.environ.copy()
    if bridge:
        env['BOPBOP_EDITOR_SESSION'] = bridge.id
        env['BOPBOP_EDITOR_DIR'] = str(bridge.socket.parent)
    else:
        env.pop('BOPBOP_EDITOR_SESSION', None)
    process = subprocess.Popen([NVIM, '--clean', '--headless', '--cmd',
        'set rtp+=' + str(ROOT / 'editor/nvim'), '-l', str(script)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        if terminal and select.select([terminal.master], [], [], .02)[0]:
            terminal.stream.feed(os.read(terminal.master, 65536).decode('utf-8', errors='replace'))
        else:
            time.sleep(.02)
    if process.poll() is None:
        process.kill()
    out, err = process.communicate()
    assert process.returncode == 0, out.decode() + err.decode()


def test_bridge_ownership_retry_bounds_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv('BOPBOP_EDITOR_DIR', str(tmp_path / 'private'))
    calls, cancels = [], []
    b = EditorBridge(tmp_path, calls.append, lambda: cancels.append(True), 'fixture')
    client, rid = uuid.uuid4().hex, uuid.uuid4().hex
    data = dict(session=b.id, client=client, id=rid, op='submit', request=request(tmp_path / 'new.py'))
    assert b.dispatch(data)['status'] == 'working'
    assert b.dispatch(data)['status'] == 'working'
    assert len(calls) == 1
    with pytest.raises(ValueError, match='different editor'):
        b.dispatch(dict(data, client=uuid.uuid4().hex, op='poll'))
    with pytest.raises(ValueError, match='Wrong BopBop'):
        b.dispatch(dict(data, session=uuid.uuid4().hex))
    with pytest.raises(ValueError, match='busy'):
        b.dispatch(dict(data, id=uuid.uuid4().hex))
    assert b.dispatch(dict(data, op='cancel'))['status'] == 'failed'
    assert cancels == [True]
    for _ in range(20):
        b.dispatch(dict(data, id=uuid.uuid4().hex)); b.complete({'answer': 'done'})
    assert len(b.records) == 16
    oversized = uuid.uuid4().hex
    b.dispatch(dict(data, id=oversized))
    b.complete({'answer': 'a' * 262144})
    assert b.dispatch(dict(data, id=oversized, op='poll'))['status'] == 'failed'
    b.root.chmod(0o755)
    with pytest.raises(ValueError, match='0700'):
        private_dir()
    b.root.chmod(0o700)


def test_socket_lifecycle_and_validation(bridge, tmp_path):
    b, received = bridge
    assert b.socket.stat().st_mode & 0o777 == 0o600
    assert b.descriptor.stat().st_mode & 0o777 == 0o600
    assert sessions()[0]['session'] == b.id
    for delta, message in [({'path': '/outside.py'}, 'outside'),
        ({'mode': 'block'}, 'Blockwise'), ({'text': 'x' * 32769}, '32 KiB'),
        ({'range': [1, 0, 0, 0]}, 'ordered'), ({'prompt': ''}, 'explicit')]:
        value = request(tmp_path / 'x.py'); value.update(delta)
        with pytest.raises(ValueError, match=message): validate_request(value, tmp_path)
    symlink = tmp_path / 'link'; symlink.symlink_to('/tmp')
    with pytest.raises(ValueError, match='outside'):
        validate_request(request(symlink / 'outside.py'), tmp_path)
    reply = rpc(b, 'submit', uuid.uuid4().hex, uuid.uuid4().hex, request=request(tmp_path / 'x.py'))
    assert reply['status'] == 'working'
    time.sleep(.1)
    assert received[0]['text'] == 'old'
    assert 'old' not in b.descriptor.read_text()
    b.close()
    assert not b.socket.exists() and not b.descriptor.exists()


def test_real_visual_boundaries_unicode_reverse_exclusive_block_and_maps(tmp_path):
    path = json.dumps(str(tmp_path / 'example.py'))
    nvim_script(tmp_path, f'''
vim.g.mapleader = ' '
vim.keymap.set('x', '<leader>be', ':echo "user"<CR>')
b.setup({{keys = {{edit = '<leader>bx'}}}})
vim.cmd('file ' .. {path})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'aé🙂z', 'second', 'third'}})
select_region('v', {{1, 1}}, {{1, 3}})
local p = b.capture()
eq('é🙂', p.text); eq({{0, 1, 0, 7}}, p.range)
assert(b.hint():find(':BopbopExplain', 1, true))
assert(b.hint():find(' bx edit', 1, true))
assert(vim.fn.maparg('<leader>be', 'x'):find('user', 1, true))
select_region('v', {{1, 3}}, {{1, 1}})
eq('é🙂', b.capture().text)
select_region('v', {{1, 7}}, {{2, 2}})
eq('z\\nsec', b.capture().text); eq({{0, 7, 1, 3}}, b.capture().range)
vim.o.selection = 'exclusive'
select_region('v', {{1, 1}}, {{1, 7}})
eq('é🙂', b.capture().text); eq({{0, 1, 0, 7}}, b.capture().range)
select_region('v', {{1, 7}}, {{1, 1}})
eq('é🙂', b.capture().text)
vim.o.selection = 'inclusive'
select_region('V', {{2, 2}}, {{1, 1}})
eq('aé🙂z\\nsecond', b.capture().text); eq({{0, 0, 2, 0}}, b.capture().range)
select_region('\\22', {{2, 1}}, {{3, 2}})
eq('ec\\nhi', b.capture().text); eq('block', b.capture().mode)
b.request('edit', false, 'replace')
assert(b.pending == nil); assert(b.message:find('use v or V', 1, true))
vim.cmd('normal! ' .. vim.api.nvim_replace_termcodes('<Esc>', true, false, true))
assert(not pcall(b.capture))
eq('buffer', b.capture(true).mode)
assert(vim.fn.maparg('<C-d>', 'n') == '')
''')


@pytest.mark.parametrize('mode,first,last,expected', [
    ('v', [1, 1], [1, 3], ['aNEWz', 'second', 'third']),
    ('v', [2, 2], [1, 7], ['aé🙂NEWond', 'third']),
    ('V', [2, 0], [1, 0], ['NEW', 'third']),
])
def test_real_request_review_apply_undo_unsaved(tmp_path, bridge, mode, first, last, expected):
    b, received = bridge
    path = tmp_path / 'source.py'; path.write_text('disk unchanged\n')
    nvim_script(tmp_path, f'''
b.setup(); wait_for('connected')
vim.cmd('edit ' .. {json.dumps(str(path))})
local source = vim.api.nvim_get_current_buf()
local original = {{'aé🙂z', 'second', 'third'}}
vim.api.nvim_buf_set_lines(0, 0, -1, true, original)
select_region({json.dumps(mode)}, {vim_list(first)}, {vim_list(last)})
local captured = b.capture()
b.request('edit', false, 'replace this')
wait_for('awaiting review')
assert(vim.bo[b.review].filetype == 'diff')
assert(b.hint():find('apply', 1, true))
eq(captured.text, b.pending.text)
assert(b.apply())
eq({vim_list(expected)}, vim.api.nvim_buf_get_lines(source, 0, -1, true))
vim.cmd('undo')
eq(original, vim.api.nvim_buf_get_lines(source, 0, -1, true))
''', b)
    assert path.read_text() == 'disk unchanged\n'
    assert len(received) == 1 and 'disk unchanged' not in received[0]['text']


def vim_list(value):
    return '{' + ','.join(json.dumps(v, ensure_ascii=False) for v in value) + '}'


def test_stale_multi_buffer_reject_discard_and_reconnect(tmp_path, bridge):
    b, received = bridge
    nvim_script(tmp_path, f'''
b.setup(); wait_for('connected')
vim.cmd('file ' .. {json.dumps(str(tmp_path / 'one.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'original'}})
select_region('V', {{1, 0}}, {{1, 0}})
b.request('edit', false, 'change it')
local source = b.pending.buf
vim.api.nvim_buf_set_lines(source, 0, -1, true, {{'new work'}})
wait_for('awaiting review')
assert(not b.apply()); assert(b.message:find('Source changed', 1, true))
b.discard(); eq({{'new work'}}, vim.api.nvim_buf_get_lines(source, 0, -1, true))
vim.cmd('enew'); vim.cmd('file ' .. {json.dumps(str(tmp_path / 'two.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'other buffer'}})
local other = vim.api.nvim_get_current_buf()
select_region('V', {{1, 0}}, {{1, 0}})
b.socket = '/tmp/bopbop-nonexistent-' .. tostring(vim.uv.hrtime())
b.request('edit', false, 'retain me')
wait_for('retry available'); eq('other buffer', b.pending.text); eq('retain me', b.pending.prompt)
b.connect({json.dumps(b.id)}); vim.wait(100)
b.retry(); wait_for('awaiting review'); assert(b.apply())
eq({{'NEW'}}, vim.api.nvim_buf_get_lines(other, 0, -1, true))
eq({{'new work'}}, vim.api.nvim_buf_get_lines(source, 0, -1, true))
''', b)
    assert len(received) == 2


@pytest.fixture
def editor_model():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(payload)
            if 'SLOW_TEST' in payload['messages'][-1]['content']:
                time.sleep(2)
            answer = json.dumps({'replacement': 'return 42', 'summary': 'Return the requested constant'}) if payload.get('format') else 'It returns a value.'
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'message': {'content': answer}, 'done': True,
                'prompt_eval_count': 42, 'eval_count': 12, 'eval_duration': 10000000}).encode() + b'\n')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    yield f'http://127.0.0.1:{server.server_port}', received
    server.shutdown(); server.server_close(); thread.join()


def test_managed_tui_real_nvim_model_shared_context_and_review(tmp_path, installed, editor_model):
    url, received = editor_model
    socket_root = tempfile.TemporaryDirectory(prefix='bop-ed-')
    directory = Path(socket_root.name)
    term = ManagedTerminal(installed, tmp_path, extra_env={
        'BOPBOP_OLLAMA_HOST': url, 'BOPBOP_EDITOR_DIR': str(directory)})
    try:
        term.wait('bopbop>')
        term.send('Remember the project is named ORCHID.\r'); term.wait('Finished in')
        descriptor = json.loads(next(directory.glob('*.json')).read_text())
        class Endpoint:
            id = descriptor['session']
            socket = directory / (id + '.sock')
        nvim_script(tmp_path, f'''
b.setup(); wait_for('connected')
vim.cmd('file ' .. {json.dumps(str(tmp_path / 'function.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'return 1', 'private unselected line'}})
select_region('V', {{1, 0}}, {{1, 0}})
b.request('explain', false, 'What does this return?')
wait_for('awaiting review')
assert(b.pending.result.answer:find('returns a value', 1, true))
b.discard()
vim.api.nvim_set_current_buf(vim.fn.bufnr({json.dumps(str(tmp_path / 'function.py'))}))
select_region('V', {{1, 0}}, {{1, 0}})
b.request('edit', false, 'Return 42 instead')
wait_for('awaiting review'); assert(b.apply())
eq({{'return 42', 'private unselected line'}}, vim.api.nvim_buf_get_lines(0, 0, -1, true))
''', Endpoint, term)
        assert len(received) == 3
        for payload in received[1:]:
            assert payload['tools'] == []
            assert 'ORCHID' in json.dumps(payload['messages'])
            assert 'private unselected line' not in json.dumps(payload)
            assert payload['model'] == received[0]['model']
        assert 'What does this return?' in json.dumps(received[2]['messages'])
        term.send('/inspect context\r'); term.wait('replacement')
        term.send('Continue ordinary chat\r')
        deadline = time.monotonic() + 5
        while len(received) < 4 and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(received) == 4 and received[-1]['tools']
        assert 'format' not in received[-1]
    finally:
        term.send(b'\x04')
        time.sleep(.2)
        term.close()
    assert not list(directory.glob('*.sock')) and not list(directory.glob('*.json'))
    socket_root.cleanup()


def test_editor_tool_guard_and_malformed_replacement(local, tmp_path, monkeypatch):
    runtime = local.AgentRuntime(review_only=True)
    assert runtime.tools == []
    with pytest.raises(ValueError, match='cannot execute'):
        runtime.execute(tmp_path, 'write_file', {'path': 'oops', 'content': 'oops'})
    with pytest.raises(ValueError):
        edit_result('```code```', request(tmp_path / 'x'))
    with pytest.raises(ValueError):
        edit_result('{"replacement":42,"summary":"bad"}', request(tmp_path / 'x'))
    monkeypatch.setattr(local, 'chat', lambda *a: ({'role': 'assistant', 'tool_calls': [
        {'function': {'name': 'write_file', 'arguments': {'path': 'oops', 'content': 'oops'}}}]},
        local.ModelStats(0, None, 0, 0, 0, 0, 0, 0, 0, 0, None, False, False)))
    with pytest.raises(ValueError, match='no tool was executed'):
        local.turn('test', [{'role': 'user', 'content': 'edit'}], tmp_path, 2048,
                   local.Display(False), runtime=runtime)
    assert not (tmp_path / 'oops').exists()


def test_real_nvim_cancel_recovers_without_applying(tmp_path, installed, editor_model):
    url, received = editor_model
    with tempfile.TemporaryDirectory(prefix='bop-ed-') as socket_root:
        directory = Path(socket_root)
        term = ManagedTerminal(installed, tmp_path, extra_env={
            'BOPBOP_OLLAMA_HOST': url, 'BOPBOP_EDITOR_DIR': str(directory)})
        try:
            term.wait('bopbop>')
            descriptor = json.loads(next(directory.glob('*.json')).read_text())
            class Endpoint:
                id = descriptor['session']
                socket = directory / (id + '.sock')
            nvim_script(tmp_path, f''' 
b.setup(); wait_for('connected')
vim.cmd('file ' .. {json.dumps(str(tmp_path / 'cancel.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'unchanged'}})
select_region('V', {{1, 0}}, {{1, 0}})
b.request('edit', false, 'SLOW_TEST')
vim.wait(300)
b.cancel(); wait_for('retry available')
eq('unchanged', b.pending.text); eq('SLOW_TEST', b.pending.prompt)
eq({{'unchanged'}}, vim.api.nvim_buf_get_lines(0, 0, -1, true))
assert(b.pending.id == nil)
b.pending.prompt = 'Return 42'; b.retry(); wait_for('awaiting review')
b.discard()
eq({{'unchanged'}}, vim.api.nvim_buf_get_lines(0, 0, -1, true))
''', Endpoint, term)
            assert len(received) == 2
        finally:
            term.send(b'\x04'); time.sleep(.2); term.close()


def test_real_block_explanation_and_explicit_buffer(tmp_path, bridge):
    b, received = bridge
    nvim_script(tmp_path, f'''
b.setup(); wait_for('connected')
vim.cmd('file ' .. {json.dumps(str(tmp_path / 'block.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'abcd', 'efgh'}})
select_region('\\22', {{1, 1}}, {{2, 2}})
b.request('explain', false, 'Explain the two fragments')
wait_for('awaiting review'); b.discard()
b.request('explain', true, 'Explain this whole buffer explicitly')
wait_for('awaiting review'); b.discard()
''', b)
    assert received[0]['text'] == 'bc\nfg'
    assert received[0]['mode'] == 'block' and len(received[0]['spans']) == 2
    assert received[1]['text'] == 'abcd\nefgh' and received[1]['mode'] == 'buffer'


@pytest.mark.skipif(os.environ.get('BOPBOP_LIVE_EDITOR_TEST') != '1', reason='Opt-in real local model acceptance')
def test_live_gpt_oss_explain_edit_apply_undo(tmp_path, installed):
    source = tmp_path / 'example.py'
    source.write_text('def increment(x):\n    return x + 1\n')
    with tempfile.TemporaryDirectory(prefix='bop-ed-') as socket_root:
        directory = Path(socket_root)
        term = ManagedTerminal(installed, tmp_path, columns=150, rows=40, extra_env={
            'BOPBOP_OLLAMA_HOST': 'http://127.0.0.1:11434', 'BOPBOP_EDITOR_DIR': str(directory)})
        try:
            term.wait('bopbop>')
            descriptor = json.loads(next(directory.glob('*.json')).read_text())
            class Endpoint:
                id = descriptor['session']
                socket = directory / (id + '.sock')
            nvim_script(tmp_path, f'''
b.setup(); wait_for('connected')
vim.cmd('edit ' .. {json.dumps(str(source))})
local source = vim.api.nvim_get_current_buf()
select_region('V', {{1, 0}}, {{2, 0}})
b.request('explain', false, 'Explain this function in one short sentence.')
wait_for('awaiting review', 120000)
assert(#b.pending.result.answer > 10)
vim.fn.writefile({{vim.json.encode(b.pending.result)}}, {json.dumps(str(tmp_path / 'explanation.json'))})
b.discard()
vim.api.nvim_set_current_buf(source)
select_region('V', {{1, 0}}, {{2, 0}})
b.request('edit', false, 'Change increment(x) to return the constant 42 for every input. Keep its name and parameter. Only replace these two selected lines.')
wait_for('awaiting review', 120000)
assert(b.pending.result.diff:find('+', 1, true))
vim.fn.writefile({{vim.json.encode(b.pending.result)}}, {json.dumps(str(tmp_path / 'edit.json'))})
assert(b.apply())
vim.fn.writefile(vim.api.nvim_buf_get_lines(source, 0, -1, true), {json.dumps(str(tmp_path / 'accepted.py'))})
vim.cmd('undo')
eq({{'def increment(x):', '    return x + 1'}}, vim.api.nvim_buf_get_lines(source, 0, -1, true))
''', Endpoint, term, timeout=250)
            code = (tmp_path / 'accepted.py').read_text()
            namespace = {}
            exec(compile(code, 'accepted.py', 'exec'), namespace)
            assert namespace['increment'](7) == namespace['increment'](-1) == 42
            assert source.read_text() == 'def increment(x):\n    return x + 1\n'
            (tmp_path / 'telemetry.txt').write_text(term.text)
            print('Live editor evidence:', tmp_path)
        finally:
            term.send(b'\x04'); time.sleep(.2); term.close()


def test_real_combining_empty_boundaries_and_user_key_invocation(tmp_path, bridge):
    b, received = bridge
    nvim_script(tmp_path, f'''
vim.g.mapleader = ' '
b.setup(); wait_for('connected')
vim.cmd('file ' .. {json.dumps(str(tmp_path / 'unicode.py'))})
vim.api.nvim_buf_set_lines(0, 0, -1, true, {{'aéz', '', 'last'}})
select_region('v', {{1, 1}}, {{1, 1}})
eq('é', b.capture().text); eq({{0, 1, 0, 4}}, b.capture().range)
select_region('v', {{2, 0}}, {{2, 0}})
eq('', b.capture().text); eq({{1, 0, 1, 0}}, b.capture().range)
select_region('V', {{1, 0}}, {{1, 0}})
vim.ui.input = function(opts, cb)
  assert(opts.prompt:find('lines 1–1', 1, true)); cb('Use the installed key')
end
vim.api.nvim_feedkeys(' br', 'xt', false)
wait_for('awaiting review')
local source = b.pending.buf
vim.cmd('wincmd p')
vim.cmd('enew'); vim.cmd('file ' .. {json.dumps(str(tmp_path / 'untouched.py'))})
local other = vim.api.nvim_get_current_buf()
vim.api.nvim_buf_set_lines(other, 0, -1, true, {{'independent work'}})
vim.cmd('wincmd p')
assert(vim.api.nvim_get_current_buf() == b.review)
vim.api.nvim_feedkeys(' ba', 'xt', false)
eq({{'NEW', '', 'last'}}, vim.api.nvim_buf_get_lines(source, 0, -1, true))
eq({{'independent work'}}, vim.api.nvim_buf_get_lines(other, 0, -1, true))
''', b)
    assert received[0]['prompt'] == 'Use the installed key'
