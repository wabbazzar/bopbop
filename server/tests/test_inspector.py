import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_cli_tools import local, installed, runtime_for
from bopbop_inspector import (InspectorStore, Snapshot, request_snapshot, result_snapshot,
                              file_snapshot, attachment_block)


@pytest.fixture
def model_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'message': {'content': 'Fixture '}, 'done': False}).encode() + b'\n')
            self.wfile.flush()
            time.sleep(.4)
            self.wfile.write(json.dumps({'message': {'content': 'answer.'}, 'done': True,
                'prompt_eval_count': 41, 'eval_count': 3, 'eval_duration': 1000000}).encode() + b'\n')
        def log_message(self, *unused): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}', requests
    server.shutdown(); server.server_close(); thread.join()


def test_request_snapshot_is_exact_wire_payload_after_pruning(local, tmp_path, monkeypatch, model_server):
    url, requests = model_server
    monkeypatch.setenv('BOPBOP_OLLAMA_HOST', url)
    events = []
    runtime = local.AgentRuntime(observer=events.append)
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'old' * 5000},
                {'role': 'assistant', 'content': 'old answer'}, {'role': 'user', 'content': 'current'}]
    answer, usage = local.turn('fixture', messages, tmp_path, 2048, local.Display(False), runtime=runtime)
    event = next(e for e in events if e['kind'] == 'request')
    record = request_snapshot(event)
    assert json.loads(record.body) == requests[0]
    assert record.metadata['removed_messages'][0]['content'] == 'old' * 5000
    assert [m['content'] for m in requests[0]['messages']] == ['rules', 'current']
    assert 'headers' not in record.body
    assert 'Fixture answer.' not in record.body  # no queued-reference mutation
    assert ''.join(e['delta'] for e in events if e['kind'] == 'stream') == 'Fixture answer.'
    assert next(e for e in events if e['kind'] == 'model_usage')['prompt_tokens'] == 41


def test_result_retains_complete_returned_bytes_and_source_limits(local, installed, tmp_path):
    runtime = runtime_for(local, installed)
    events = []; runtime.observer = events.append
    result = local.run_tool(tmp_path, 'sample_count', {'window':'24h'}, local.Display(False), runtime, local.TurnStats())
    event = next(e for e in events if e['kind'] == 'tool_complete')
    snapshot = result_snapshot(event)
    assert snapshot.body == result
    assert snapshot.metadata['call_id'] == events[0]['call_id']
    assert snapshot.metadata['arguments'] == {'window': '24h'}
    event.update(result='{"state":"stale","truncated":true}', ok=False,
                 telemetry={'state':'stale', 'truncated':True, 'source_age_s':300})
    snapshot = result_snapshot(event)
    assert snapshot.metadata['telemetry']['truncated']
    assert snapshot.metadata['telemetry']['source_age_s'] == 300
    assert not snapshot.metadata['ok']


def test_retention_preserves_pin_and_explains_evictions():
    store = InspectorStore(max_bytes=80, max_items=2)
    first = Snapshot('result', 'first', 'a' * 25)
    store.add(first); store.pinned = first.id
    for _ in range(4): store.add(Snapshot('result', 'later', 'b' * 25))
    assert first.id in store.items and len(store.items) == 2
    assert store.evictions == 3 and 'evicted' in store.notice
    assert store.add(Snapshot('context', 'too big', 'x' * 81)) is None
    assert 'not retained' in store.notice


def test_file_snapshot_attachment_is_explicit_versioned_and_bounded(tmp_path):
    target = tmp_path / 'code.py'; target.write_text('first\nsecond\nthird\n')
    snapshot = file_snapshot(tmp_path, 'code.py')
    selected = snapshot.attachment(2, 2)
    target.write_text('changed\n')
    assert selected['content'] == 'second' and selected['lines'] == [2, 2]
    assert selected['sha256'] == snapshot.digest
    assert snapshot.body == 'first\nsecond\nthird\n'
    assert 'second' in attachment_block([selected]) and 'third' not in attachment_block([selected])
    with pytest.raises(ValueError): snapshot.attachment(0, 3)
    with pytest.raises(ValueError): file_snapshot(tmp_path, '../outside')
    (tmp_path / 'escape').symlink_to('/etc/hosts')
    with pytest.raises(ValueError): file_snapshot(tmp_path, 'escape')
    target.write_bytes(b'x' * 262145)
    with pytest.raises(ValueError, match='256 KB'): file_snapshot(tmp_path, 'code.py')


def test_diff_snapshot_identifies_base_and_working_version(tmp_path):
    def git(*args): subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.org')
    target = tmp_path / 'example.py'; target.write_text('before\n')
    git('add', 'example.py'); git('commit', '-m', 'fixture')
    target.write_text('after\n')
    snapshot = file_snapshot(tmp_path, 'example.py', diff=True)
    assert '-before' in snapshot.body and '+after' in snapshot.body
    assert len(snapshot.metadata['base_commit']) == 40
