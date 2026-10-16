"""Explicit tools: dispatch boundaries, real chat payloads and terminal navigation."""
import fcntl
import importlib.util
import json
import os
import shlex
from pathlib import Path
import pty
import re
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
from bopbop_tool_ui import argument_focus, call_help_lines, call_tool_name, draft_view, parse_call, reference_lines, text_width


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


def test_live_help_reads_new_pack_declarations_without_execution(local, installed):
    document = json.loads((installed / 'tools.json').read_text())
    document['tools'].append({
        'name': 'sample_ping', 'description': 'Fixture documentation owned by the pack.',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
        'handler': 'handler.py', 'examples': [{'arguments': {}}],
    })
    (installed / 'tools.json').write_text(json.dumps(document))
    runtime = runtime_for(local, installed)
    for draft in ('/call sample_ping', '/call sample_ping {', '/call sample_ping {} :: Explain it'):
        assert call_tool_name(runtime, draft) == 'sample_ping'
    for draft in ('/call sample', '/call missing {}', '/call read_file {}', '/tools sample_ping'):
        assert call_tool_name(runtime, draft) is None
    output = '\n'.join(call_help_lines(runtime, 'sample_ping', local.Display(False), 100))
    assert 'Fixture documentation owned by the pack.' in output
    assert 'No arguments.' in output
    assert '/call sample_ping {}' in output
    output = '\n'.join(call_help_lines(runtime, 'sample_count', local.Display(False), 100))
    for value in ('window: string · required', 'limit: integer · optional',
                  'enum=["24h", "7d"]', 'minimum=1', 'maximum=20', '"limit": 7'):
        assert value in output
    assert not (installed / 'called').exists()


def test_builtin_reference_is_owned_by_declaration_and_excluded_from_model(local, monkeypatch, capsys, tmp_path):
    definitions = [dict(definition) for definition in local.WORKSPACE_TOOLS]
    definitions[0]['description'] = 'Fresh declaration description'
    definitions[0]['documentation'] = 'Fresh declaration reference'
    definitions[0]['examples'] = [{'arguments': {'path': 'new-example'}}]
    monkeypatch.setattr(local, 'WORKSPACE_TOOLS', definitions)
    runtime = local.AgentRuntime(context_dir=tmp_path)
    assert 'Fresh declaration reference' in '\n'.join(reference_lines(runtime, 'list_files', local.Display(False)))
    assert 'new-example' in '\n'.join(call_help_lines(runtime, 'list_files', local.Display(False), 100))
    assert all(set(item['function']) == {'name', 'description', 'parameters'} for item in runtime.tools)
    assert 'Fresh declaration reference' not in json.dumps(runtime.tools)
    assert 'Fresh declaration description' in json.dumps(runtime.tools)
    local.show_tools(runtime, local.Display(False))
    assert 'Fresh declaration description' in capsys.readouterr().err


def test_compact_context_and_skill_discovery_do_not_load_large_wrapper_instructions(local, tmp_path):
    (tmp_path / 'BOPBOP.md').write_text('Concise local rules. Follow the requested skill.')
    (tmp_path / 'CLAUDE.md').write_text('Large wrapper instructions.' * 3000)
    (tmp_path / 'AGENTS.md').write_text('Read CLAUDE.md in full.')
    for root in ('.agents/skills', '.claude/skills'):
        skill = tmp_path / root / 'test-skill/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('Skill-owned instructions')
    prompt = local.system_prompt(tmp_path, local.AgentRuntime())
    assert 'Concise local rules' in prompt and 'Large wrapper instructions' not in prompt
    assert prompt.count('test-skill:') == 1
    assert '.agents/skills/test-skill/SKILL.md' in prompt
    assert 'read_file' in prompt
    assert local.execute(tmp_path, 'read_file', {'path': '.agents/skills/test-skill/SKILL.md'}) == 'Skill-owned instructions'
    (tmp_path / 'BOPBOP.md').unlink()
    assert 'Large wrapper instructions' in local.system_prompt(tmp_path, local.AgentRuntime())


@pytest.mark.parametrize('timeout', [0, -1, True, 1.5, 21601])
def test_command_timeout_is_validated_for_model_selected_calls(local, tmp_path, timeout):
    assert local.execute(tmp_path, 'run_command', {'command': 'unused', 'timeout_seconds': timeout}).startswith('ERROR:')


def test_command_timeout_cleans_up_children(local, tmp_path):
    script = tmp_path / 'parent.py'
    script.write_text('import os,signal,subprocess,sys,time\n'
                      'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"])\n'
                      'open("child.pid","w").write(str(child.pid))\n'
                      'signal.signal(signal.SIGTERM,lambda *args: (child.wait(),sys.exit(0)))\n'
                      'time.sleep(20)\n')
    result = local.execute(tmp_path, 'run_command', {'command': shlex.join([sys.executable, str(script)]),
                                                  'timeout_seconds': 1})
    assert result.startswith('ERROR:') and 'timed out' in result
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / 'child.pid').read_text()), 0)


def test_verbose_command_retains_the_selected_tail_and_final_status(local, tmp_path):
    command = shlex.join([sys.executable, '-c', 'print("x"*5000);print("WORKFLOW COMPLETE")'])
    result = local.execute(tmp_path, 'run_command', {'command': command, 'max_output_chars': 1000})
    assert result.startswith('exit=0\n') and result.endswith('WORKFLOW COMPLETE\n')
    assert len(result) == len('exit=0\n') + 1000
    for limit in (0, True, 1000.5, 12001):
        assert local.execute(tmp_path, 'run_command', {'command': command, 'max_output_chars': limit}).startswith('ERROR:')


@pytest.mark.parametrize('cursor', [0, 8, 20, 50, 80])
def test_draft_view_keeps_unicode_cursor_inside_narrow_terminal(cursor):
    draft = '/call sample_count ' + 'café漢字 ' * 12
    visible, column = draft_view(draft, cursor, 30)
    assert text_width(visible) <= 30
    assert 0 <= column < 30
    if cursor:
        # The character immediately left of the cursor remains visible.
        assert draft[cursor - 1] in visible


class PromptTerminal:
    """Drive the real prompt in a PTY, observing paints and the submitted draft."""
    def __init__(self, installed, color, turns=1, history=()):
        self.master, self.slave = pty.openpty()
        self.resize(100, 24)
        self.original = termios.tcgetattr(self.slave)
        self.output = bytearray()
        env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
        program = '''
import importlib.util,json,pathlib,readline,socket,sys
sys.path.insert(0,sys.argv[1])
spec=importlib.util.spec_from_file_location('prompt_under_test',pathlib.Path(sys.argv[1])/'bopbop-local.py')
local=importlib.util.module_from_spec(spec);sys.modules[spec.name]=local;spec.loader.exec_module(local)
from bopbop_prompt import prompt_line
runtime=local.AgentRuntime(local.Registry(pathlib.Path(sys.argv[2]),['test']), 'test')
def forbidden(*args,**kwargs): raise AssertionError('help must not execute tools or network calls')
socket.socket=forbidden;runtime.execute=forbidden;local.chat=forbidden
readline.clear_history()
for line in json.loads(sys.argv[4]): readline.add_history(line)
try:
    for _ in range(int(sys.argv[5])):
        draft=prompt_line(local.Display(sys.argv[3]=='True'),local.SLASH_COMMANDS,runtime)
        print('DRAFT_JSON='+json.dumps(draft),flush=True)
except KeyboardInterrupt:
    print('CANCELLED',flush=True)
'''
        self.process = subprocess.Popen([sys.executable, '-c', program, str(ROOT / 'bin'),
                                         str(installed.parents[1]), str(color), json.dumps(history), str(turns)], stdin=self.slave,
                                        stdout=self.slave, stderr=self.slave, env=env)

    def resize(self, columns, rows):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))

    def read_until(self, expected):
        deadline = time.monotonic() + 5
        while expected not in self.output:
            if time.monotonic() > deadline:
                pytest.fail(f'Missing {expected!r}: {bytes(self.output[-1500:])!r}')
            if select.select([self.master], [], [], .1)[0]:
                self.output.extend(os.read(self.master, 65536))
        # Drain the rest of this repaint, including queued input already sent.
        while select.select([self.master], [], [], .05)[0]:
            self.output.extend(os.read(self.master, 65536))
        result = bytes(self.output)
        self.output.clear()
        return result

    def paint(self, keys, expected):
        os.write(self.master, keys)
        output = self.read_until(expected)
        frame = output.split(b'\r\x1b[J')[-1].decode('utf-8')
        return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', frame)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        os.close(self.master)
        os.close(self.slave)


@pytest.mark.parametrize('color', [False, True])
def test_live_help_preserves_draft_through_paging_hide_resize_and_cursor_edits(installed, color):
    terminal = PromptTerminal(installed, color)
    try:
        terminal.read_until(b'> ')
        frame = terminal.paint(b'/call sample_count', b'call help')
        assert 'required' in frame and 'Example' in frame
        prefix = ' {"window":"24h","project":"café 漢字 :: '
        frame = terminal.paint(prefix.encode(), b'call help')
        assert 'call help' in frame  # incomplete JSON does not hide help
        tail = 'quoted \\"text\\" ' + 'x' * 20 + '"} :: résumé'
        draft = '/call sample_count' + prefix + tail
        terminal.paint(tail.encode(), 'résumé'.encode())
        frame = terminal.paint(b'\x1bOP', b'F1 compact')
        assert 'Notes' in frame
        next_frame = terminal.paint(b'\x1b[6~', b'F1 compact')
        assert next_frame != frame
        terminal.paint(b'\x1b[5~', b'F1 compact')
        frame = terminal.paint(b'\x1b', b'\r\x1b[J')
        assert 'F1' not in frame and 'call help' not in frame
        terminal.paint(b'\x1b[11~', b'F1 compact')
        terminal.resize(48, 12)
        output = terminal.read_until(b'\r\x1b[J')
        frame = output.split(b'\r\x1b[J')[-1].decode()
        frame = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', frame)
        assert all(text_width(row) <= 47 for row in frame.split('\r\n'))
        assert len(frame.split('\r\n')) <= 12
        terminal.paint(b'\x1b[F\x1b[D\x1b[D!', b'\r\x1b[J')
        terminal.paint(b'\x1bOP', b'F1 full reference')
        terminal.paint(b'?', b'\r\x1b[J')
        os.write(terminal.master, b'\r')
        result = terminal.read_until(b'DRAFT_JSON=')
        submitted = json.loads(result.split(b'DRAFT_JSON=')[-1].split(b'\r\n')[0])
        assert submitted == draft[:-2] + '!?' + draft[-2:]
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert termios.tcgetattr(terminal.slave) == terminal.original
        assert not (installed / 'called').exists()
    finally:
        terminal.close()


def test_interrupt_live_help_restores_terminal_without_dispatch(installed):
    terminal = PromptTerminal(installed, False)
    try:
        terminal.read_until(b'> ')
        terminal.paint(b'/call sample_count {', b'call help')
        os.write(terminal.master, b'\x03')
        terminal.read_until(b'CANCELLED')
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert termios.tcgetattr(terminal.slave) == terminal.original
        assert not (installed / 'called').exists()
    finally:
        terminal.close()


@pytest.mark.parametrize('suffix,mode,key,prefix,used', [
    ('{', 'key', '', '', set()),
    ('{"wi', 'key', '', 'wi', set()),
    ('{"window":', 'value', 'window', '', set()),
    ('{"window":"7', 'value', 'window', '7', set()),
    ('{"window":"7d", "li', 'key', '', 'li', {'window'}),
    ('{"limit":1', 'value', 'limit', '1', set()),
    ('{"project":"brace } comma , colon :: quote \\" x', 'value', 'project', 'brace } comma , colon :: quote " x', set()),
    ('{"project":["nested, value"], "wi', 'key', '', 'wi', {'project'}),
])
def test_argument_focus_tolerates_partial_json(suffix, mode, key, prefix, used):
    assert argument_focus('/call sample_count' + suffix) == (mode, key, prefix, frozenset(used))


def test_parameter_help_filters_keys_values_and_uses_cursor(local, installed):
    runtime = runtime_for(local, installed)
    def help_for(suffix, cursor=None):
        return '\n'.join(call_help_lines(runtime, 'sample_count', local.Display(False), 100,
                                         '/call sample_count ' + suffix, cursor))
    assert 'Observation window' in help_for('{"wi')
    assert 'limit:' not in help_for('{"wi')
    text = help_for('{"window":"7')
    assert 'Matching values: "7d"' in text
    assert '"24h"' not in text
    assert 'window:' not in help_for('{"window":"7d", ')
    text = help_for('{"window":"24h", "li')
    assert 'minimum: 1' in text and 'maximum: 20' in text and '"limit": 7' in text
    assert 'No matching parameters' in help_for('{"nonexistent')
    draft = '/call sample_count {"window":"7d","limit":2} :: question'
    cursor = draft.index('7d') + 1
    assert argument_focus(draft, cursor)[:3] == ('value', 'window', '7')
    assert argument_focus(draft) is None
    assert call_tool_name(runtime, '/call sample_count{') == 'sample_count'
    assert parse_call('sample_count{"window":"7d"}', runtime) == ('sample_count', {'window':'7d'}, None)


def test_live_filtered_help_and_history_restore_exact_draft(installed):
    previous = '/call sample_count {"window":"24h","limit":7}'
    terminal = PromptTerminal(installed, False, history=['Earlier question', previous])
    try:
        terminal.read_until(b'> ')
        frame = terminal.paint(b'/call sample_count{"wi', b'Observation window')
        assert 'limit:' not in frame
        frame = terminal.paint(b'ndow":"7', b'Matching values')
        assert '"7d"' in frame and '"24h"' not in frame
        draft = '/call sample_count{"window":"7'
        frame = terminal.paint(b'\x1b[A', previous.encode())
        assert 'call help' in frame
        terminal.paint(b'\x1b[A', b'Earlier question')
        terminal.paint(b'\x1b[B', previous.encode())
        frame = terminal.paint(b'\x1b[B', b'Matching values')
        assert draft in frame
        os.write(terminal.master, b'd"}\r')
        output = terminal.read_until(b'DRAFT_JSON=')
        submitted = json.loads(output.split(b'DRAFT_JSON=')[-1].split(b'\r\n')[0])
        assert submitted == draft + 'd"}'
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert not (installed / 'called').exists()
        assert termios.tcgetattr(terminal.slave) == terminal.original
    finally:
        terminal.close()


def test_history_remembers_chat_and_slash_lines_and_restores_unsent_text(installed):
    terminal = PromptTerminal(installed, False, turns=4)
    try:
        terminal.read_until(b'> ')
        terminal.paint('Explain café 漢字\r'.encode(), b'DRAFT_JSON=')
        command = '/call sample_count {"window":"24h"}'
        terminal.paint((command + '\r').encode(), b'DRAFT_JSON=')
        terminal.paint(b'unsent draft', b'unsent draft')
        terminal.paint(b'\x1b[A', command.encode())
        terminal.paint(b'\x1b[A', 'Explain café 漢字'.encode())
        terminal.paint(b'\x1b[B', command.encode())
        terminal.paint(b'\x1b[B', b'unsent draft')
        os.write(terminal.master, b'\r')
        result = terminal.read_until(b'DRAFT_JSON=')
        assert b'DRAFT_JSON="unsent draft"' in result
        # Blank-prompt recall inserts a command for editing, without submitting it.
        terminal.paint(b'\x1b[A', b'unsent draft')
        terminal.paint(b'\x1b[A', command.encode())
        frame = terminal.paint(b'\x1b[D\x1b[D', b'Matching values')
        assert 'Observation window' in frame
        os.write(terminal.master, b'\x1b[F\r')
        output = terminal.read_until(b'DRAFT_JSON=')
        submitted = json.loads(output.split(b'DRAFT_JSON=')[-1].split(b'\r\n')[0])
        assert submitted == command
        terminal.process.wait(timeout=5)
        assert terminal.process.returncode == 0
        assert not (installed / 'called').exists()
    finally:
        terminal.close()


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
    process = subprocess.Popen([sys.executable, str(ROOT / 'bin/bopbop-local.py'), '--ui', 'plain', '--banner', 'off',
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


def test_unknown_workspace_tool_explains_recovery(local, tmp_path):
    result = local.execute(tmp_path, 'search', {'path': '.', 'query': 'reset'})
    assert result.startswith('ERROR: unknown tool search')
    assert 'run_command' in result and 'rg -n PATTERN PATH' in result
    assert not list(tmp_path.iterdir())


def test_workspace_investigation_guidance_is_not_added_to_profiles(local, tmp_path):
    prompt = local.system_prompt(tmp_path, local.AgentRuntime())
    assert 'rg -n reset bin' in prompt
    assert 'do not edit files' in prompt
    profile = local.system_prompt(tmp_path, local.AgentRuntime(profile='dashboard'))
    assert 'rg -n reset bin' not in profile


@pytest.mark.parametrize('command', [None, ['rg', '-n', 'reset'], 42, {'argv': 'rg'}])
def test_command_wrong_type_returns_correctable_error(local, tmp_path, command):
    result = local.execute(tmp_path, 'run_command', {'command': command})
    assert result.startswith('ERROR: command must be a string')
    assert '"command": "rg -n PATTERN PATH"' in result
    assert local.execute(tmp_path, 'run_command', {'command': 'pwd'}).startswith('exit=0')


@pytest.mark.parametrize('finish_at,expected_calls', [(13, 13), (100, 24)])
def test_workspace_investigation_budget(local, tmp_path, monkeypatch, finish_at, expected_calls):
    calls = []
    def chat(model, messages, context, step, display, runtime):
        calls.append(step)
        message = ({'role': 'assistant', 'content': 'verified answer'} if step == finish_at else
                   {'role': 'assistant', 'content': '', 'tool_calls': [
                       {'function': {'name': 'list_files', 'arguments': {}}}]})
        return message, local.ModelStats(0, None, 0, 0, 0, 0, 0, 0, 0, 0)
    monkeypatch.setattr(local, 'chat', chat)
    answer, _ = local.turn('fixture', [{'role': 'system', 'content': 'fixture'},
        {'role': 'user', 'content': 'investigate'}], tmp_path, 16384, local.Display(False),
        runtime=local.AgentRuntime())
    assert len(calls) == expected_calls
    assert answer == ('verified answer' if finish_at == 13 else
                      'Stopped after 24 model steps without a final reply.')


def test_read_file_ranges_and_truncation_are_explicit(local, tmp_path):
    path = tmp_path / 'source.py'
    path.write_text('first\nsecond\nthird\n')
    assert local.execute(tmp_path, 'read_file', {'path': 'source.py', 'start_line': 2, 'end_line': 2}) == 'second\n'
    assert local.execute(tmp_path, 'read_file', {'path': 'source.py', 'start_line': 3}) == 'third\n'
    for args in ({'start_line': 0}, {'start_line': True}, {'start_line': 3, 'end_line': 2}, {'line_start': 2}):
        assert local.execute(tmp_path, 'read_file', {'path': 'source.py', **args}).startswith('ERROR:')
    path.write_text('x' * 17000)
    result = local.execute(tmp_path, 'read_file', {'path': 'source.py'})
    assert len(result) == 16000 and '\n[TRUNCATED:' in result
    assert 'start_line/end_line' in result
