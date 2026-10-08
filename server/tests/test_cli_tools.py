"""Explicit tools: dispatch boundaries, real chat payloads and terminal navigation."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))
from bopbop_packs import Pack, Registry
from bopbop_tool_ui import parse_call, reference_lines


@pytest.fixture
def local():
    spec = importlib.util.spec_from_file_location('cli_tools_local', ROOT / 'bin/bopbop-local.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / 'context/packs/sample'
    root.mkdir(parents=True)
    (root / 'pack.toml').write_text('name="sample"\nprofile="test"\ntools="tools.json"\n')
    (root / 'handler.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        'Path("called").touch()\nr=json.load(sys.stdin)\n'
        'print(json.dumps({"ok":True,"state":"ready","count":r["arguments"].get("limit",5)}))\n'
    )
    definition = {
        'name': 'sample_count', 'description': 'Count observations',
        'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
            'window': {'type': 'string', 'enum': ['24h', '7d'], 'description': 'Observation window'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
            'project': {'type': 'string', 'maxLength': 64}}, 'required': ['window']},
        'handler': 'handler.py', 'timeout_seconds': 1, 'max_output_bytes': 512,
        'documentation': 'An explicit read-only count. Missing evidence is unknown.',
        'returns': 'JSON with ok, state and count.',
        'examples': [{'summary': 'Count seven observations.', 'arguments': {'window': '24h', 'limit': 7}}],
    }
    (root / 'tools.json').write_text(json.dumps({'schema_version': 1, 'tools': [definition]}))
    return root


def runtime_for(local, installed, profile='test'):
    return local.AgentRuntime(Registry(installed.parents[1], ['test']), profile)


@pytest.mark.parametrize('text', [
    '', 'missing {}', 'read_file {"path":"secret"}',
    'sample_count []', 'sample_count {"window":"30d"}',
    'sample_count {"window":"24h","limit":true}',
    'sample_count {"window":"24h","limit":0}',
    'sample_count {"window":"24h","limit":NaN}',
    'sample_count {"window":"24h","extra":1}',
    'sample_count {"window":"24h"} ignored',
    'sample_count {"window":"24h"} ::',
    'sample_count {"window":"24h"',
    'sample_count ' + '[' * 1500 + '0' + ']' * 1500,
])
def test_invalid_manual_calls_do_not_dispatch_or_change_history(local, installed, tmp_path, text, capsys):
    runtime = runtime_for(local, installed)
    messages = [{'role': 'system', 'content': 'instructions'}]
    session = local.SessionStats()
    local.direct_call(text, messages, session, 'unreachable', 8192, tmp_path, local.Display(False), runtime)
    assert messages == [{'role': 'system', 'content': 'instructions'}]
    assert session.turns == 0
    assert not (installed / 'called').exists()
    assert 'Invalid call' in capsys.readouterr().err


def test_parser_preserves_delimiters_and_unicode_in_json(local, installed):
    runtime = runtime_for(local, installed)
    name, args, question = parse_call('sample_count {"window":"24h","project":"café :: crew"} :: résumé', runtime)
    assert name == 'sample_count'
    assert args['project'] == 'café :: crew'
    assert question == 'résumé'
    with pytest.raises(ValueError, match='64 KB'):
        parse_call('sample_count ' + 'x' * 65536, runtime)


def test_reference_is_typed_colorized_and_does_not_execute(local, installed):
    runtime = runtime_for(local, installed)
    output = '\n'.join(reference_lines(runtime, 'sample_count', local.Display(True), 80))
    for expected in ('required', 'optional', 'integer', 'minimum=1', 'maximum=20', 'maxLength=64',
                     '"24h"', 'Handler: handler.py', 'timeout 1s', '512 bytes', 'JSON with ok',
                     'Missing evidence is unknown', '/call sample_count', 'exactly what the model receives'):
        assert expected in output
    assert '\x1b[' in output
    assert not (installed / 'called').exists()
    schema = runtime.registry.schemas[0]['function']
    assert set(schema) == {'name', 'description', 'parameters'}
    assert not any(key in json.dumps(schema) for key in ('documentation', 'examples', 'returns'))


def test_reference_edits_do_not_invalidate_model_schema_fingerprint(installed):
    registry = Registry(installed.parents[1], ['test'])
    fingerprint = registry.fingerprint
    path = installed / 'tools.json'
    document = json.loads(path.read_text())
    document['tools'][0]['documentation'] = 'Revised human reference.'
    document['tools'][0]['examples'] = [{'arguments': {'window': '7d'}}]
    path.write_text(json.dumps(document))
    assert Registry(installed.parents[1], ['test']).fingerprint == fingerprint


def test_manual_workspace_write_accepts_multiline_content(local, tmp_path, monkeypatch):
    monkeypatch.setattr(local, 'chat', lambda *args: pytest.fail('direct write must not call model'))
    runtime = local.AgentRuntime()
    messages = [{'role': 'system', 'content': 'instructions'}]
    session = local.SessionStats()
    content = 'first line\n' + 'x' * 5000 + '\nlast line\tend'
    local.direct_call('write_file ' + json.dumps({'path': 'sample.txt', 'content': content}),
                      messages, session, 'unreachable', 8192, tmp_path, local.Display(False), runtime)
    assert (tmp_path / 'sample.txt').read_text() == content
    assert messages[-1]['content'] == 'written'
    assert session.last.tool_calls == 1


@pytest.mark.parametrize('field,value', [
    ('documentation', []), ('returns', 'x' * 8001), ('examples', {}),
    ('examples', [{'arguments': {'window': '30d'}}]),
    ('examples', [{'arguments': {'window': '24h'}, 'execute': True}]),
])
def test_invalid_reference_metadata_rejected_at_install(installed, field, value):
    path = installed / 'tools.json'
    document = json.loads(path.read_text())
    document['tools'][0][field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        Pack(installed)


def test_direct_calls_and_failures_keep_pairs_and_zero_model_usage(local, installed, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(local, 'chat', lambda *args: pytest.fail('direct call must not call model'))
    runtime = runtime_for(local, installed)
    messages = [{'role': 'system', 'content': 'instructions'}]
    session = local.SessionStats()
    local.direct_call('sample_count {"window":"24h","limit":7}', messages, session,
                      'unreachable', 8192, tmp_path, local.Display(False), runtime)
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'tool']
    assert messages[2]['tool_calls'][0]['function']['arguments']['limit'] == 7
    assert json.loads(messages[3]['content'])['count'] == 7
    assert session.last.tool_calls == 1
    assert session.last.model_calls == session.last.input_tokens == session.last.output_tokens == 0
    output = capsys.readouterr()
    assert '"count": 7' in output.out
    assert '0 model tokens' in output.err
    (installed / 'handler.py').write_text('raise RuntimeError("private failure")')
    local.direct_call('sample_count {"window":"7d"}', messages, session,
                      'unreachable', 8192, tmp_path, local.Display(False), runtime)
    assert json.loads(messages[-1]['content'])['ok'] is False
    assert session.totals.tool_calls == 2
    assert 'private failure' not in messages[-1]['content']
    local.handle_command('/reset', messages, session, 'unreachable', 8192, tmp_path, local.Display(False), runtime)
    assert len(messages) == 1
    assert session.totals is None


@pytest.mark.parametrize('inline', [False, True])
def test_real_cli_sends_prefetched_evidence_in_first_model_request(installed, tmp_path, inline):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'message': {'content': 'Seven observations.'}, 'done': True,
                                        'prompt_eval_count': 30, 'eval_count': 3}).encode() + b'\n')
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
    env['BOPBOP_OLLAMA_HOST'] = f'http://127.0.0.1:{server.server_port}'
    call = '/call sample_count {"window":"24h","limit":7}'
    prompts = call + (' :: Summarize the result\n' if inline else '\nSummarize the result\n') + '/stats\n/exit\n'
    try:
        process = subprocess.run([sys.executable, str(ROOT / 'bin/bopbop-local.py'), '--banner', 'off',
                                  '--color', 'never', '--workspace', str(tmp_path), '--context-dir',
                                  str(installed.parents[1]), '--profile', 'test'], input=prompts,
                                 env=env, text=True, capture_output=True, timeout=8)
        assert process.returncode == 0, process.stderr
        assert len(requests) == 1
        messages = requests[0]['messages']
        assert [m['role'] for m in messages[:4]] == ['system', 'user', 'assistant', 'tool']
        assert json.loads(messages[3]['content'])['count'] == 7
        assert 'Summarize the result' in messages[1 if inline else 4]['content']
        assert 'Seven observations.' in process.stdout
        assert '1 model call' in process.stderr
        assert '1 tool' in process.stderr
        if inline:
            assert '1 turn' in process.stderr
        else:
            assert '2 turns' in process.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_inline_call_counts_toward_profile_budget(local, installed, tmp_path, monkeypatch, capsys):
    runtime = runtime_for(local, installed)
    def chat(*args):
        return {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'sample_count', 'arguments': {'window': '24h'}}}] * 4}, local.ModelStats(0, None, 0, 0, 0, 0, 0, 0, 0, 0)
    monkeypatch.setattr(local, 'chat', chat)
    messages = [{'role': 'system', 'content': 'instructions'}]
    session = local.SessionStats()
    local.direct_call('sample_count {"window":"24h"} :: summarize', messages, session,
                      'fake', 8192, tmp_path, local.Display(False), runtime)
    assert session.last.tool_calls == 1
    assert 'four-tool-call budget' in capsys.readouterr().out


def test_tool_browser_and_call_suggestions_in_real_terminal(installed, tmp_path):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 100, 0, 0))
    original = termios.tcgetattr(slave)
    env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
    env.update(BOPBOP_OLLAMA_HOST='http://127.0.0.1:1', TERM='xterm-256color')
    env.pop('NO_COLOR', None)
    process = subprocess.Popen([sys.executable, str(ROOT / 'bin/bopbop-local.py'), '--banner', 'off',
                                '--color', 'always', '--workspace', str(tmp_path), '--context-dir',
                                str(installed.parents[1]), '--packs', 'test'], stdin=slave, stdout=slave,
                               stderr=slave, env=env)
    output = bytearray()
    def wait_for(value):
        deadline = time.monotonic() + 5
        while value not in output:
            if time.monotonic() > deadline:
                pytest.fail(f'terminal did not show {value!r}: {bytes(output[-2000:])!r}')
            if select.select([master], [], [], 0.1)[0]:
                output.extend(os.read(master, 65536))
        output.clear()
    try:
        wait_for(b'bopbop\x1b[0m>')
        os.write(master, b'/tools\r')
        wait_for(b'TOOLS')
        os.write(master, b'\x1b[B' * 4)
        wait_for(b'Pack: sample')
        assert not (installed / 'called').exists()
        os.write(master, b'\x1b[C')
        wait_for(b'Lines')
        os.write(master, b'\r')
        wait_for(b'Schema')
        os.write(master, b'/call sample\r')
        wait_for(b'"limit": 7')
        assert not (installed / 'called').exists()  # first Enter only inserts editable arguments
        os.write(master, b'\r')
        wait_for(b'0 model tokens')
        assert (installed / 'called').exists()
        os.write(master, b'/exit\r')
        process.wait(timeout=5)
        assert process.returncode == 0
        assert termios.tcgetattr(slave) == original
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)
