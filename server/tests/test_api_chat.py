"""Browser contract with an isolated DB and fake runner; no Signal or model."""
import json

import pytest
from fastapi.testclient import TestClient

import db
import main
import runner


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv('BOPBOP_SIGNAL_ENABLED', '0')
    monkeypatch.setattr(main, 'REQUIRE_BEARER', False)
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize('harness', ['claude', 'codex', 'ollama'])
def test_chat_ndjson_history_and_harness_resume(client, monkeypatch, harness):
    calls = []

    async def fake_turn(prompt, resume_session_id=None):
        calls.append((prompt, resume_session_id))
        yield {'kind': 'session', 'session_id': 'internal-agent-session'}
        yield {'kind': 'tool', 'name': 'ice_query', 'args': {'report': 'health'}}
        yield {'kind': 'tool_result', 'name': 'ice_query', 'ok': True, 'state': 'partial', 'duration_ms': 31}
        yield {'kind': 'model', 'step': 1, 'prompt_tokens': 100, 'output_tokens': 12}
        yield {'kind': 'text', 'delta': '<emote>happy</emote>Hello'}
        yield {'kind': 'text', 'delta': ' world'}
        yield {'kind': 'done', 'duration_ms': 125, 'usage': {'input_tokens': 100, 'output_tokens': 12}}

    monkeypatch.setattr(main, 'run_turn', fake_turn)
    monkeypatch.setattr(main, 'agent_harness', lambda: harness)
    response = client.post('/api/chat', json={'message': 'hello'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/x-ndjson')
    assert response.headers['cache-control'] == 'no-store'
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [e['kind'] for e in events] == ['meta', 'tool', 'tool_result', 'model', 'text', 'text', 'done']
    assert 'internal-agent-session' not in response.text
    cid = events[0]['conversation_id']
    assert db.get_active_agent_session(cid, harness) == 'internal-agent-session'
    with db._conn() as connection:
        rows = connection.execute('SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY rowid', (cid,)).fetchall()
    assert [(r['role'], r['content']) for r in rows] == [('user', 'hello'), ('assistant', '<emote>happy</emote>Hello world')]
    client.post('/api/chat', json={'message': 'follow up', 'conversation_id': cid})
    assert calls == [('[channel: pwa]\n\nhello', None), ('[channel: pwa]\n\nfollow up', 'internal-agent-session')]


def test_bearer_gate_never_invokes_runner(client, monkeypatch):
    monkeypatch.setattr(main, 'REQUIRE_BEARER', True)
    monkeypatch.setattr(main, 'BEARER', 'fixture-access')
    for headers in [{}, {'Authorization': 'Bearer incorrect'}]:
        assert client.post('/api/chat', headers=headers, json={'message': 'hello'}).status_code == 401
    with db._conn() as connection:
        assert connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0


def test_browser_config_omits_private_health_fields(client, monkeypatch):
    monkeypatch.setenv('SIGNAL_ACCOUNT', '+15550001111')
    monkeypatch.setenv('BOPBOP_CONTEXT_DIR', '/private/context')
    result = client.get('/api/client')
    assert result.status_code == 200
    assert set(result.json()) == {'ok', 'agent_harness', 'model', 'require_bearer'}
    assert '/private' not in result.text and '+1555' not in result.text
    assert result.headers['cache-control'] == 'no-store'


def test_claude_trace_ids_and_usage():
    tool = runner._claude_events({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'lookup', 'input': {}}]}})[0]
    result = runner._claude_events({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1'}]}})[0]
    assert tool['id'] == result['id'] == 't1'
    done = runner._claude_events({'type': 'result', 'usage': {'input_tokens': 42, 'output_tokens': 7}})[0]
    assert done['usage'] == {'input_tokens': 42, 'output_tokens': 7}


def test_wrapper_result_previews_are_bounded_and_ignore_images():
    result = runner._codex_events({'type': 'item.completed', 'item': {
        'id': 't1', 'type': 'command_execution', 'status': 'completed',
        'aggregated_output': 'x' * 10000,
    }}, 100)[0]
    assert result['id'] == 't1' and len(result['preview']) == 8000
    assert result['truncated'] is True
    result = runner._claude_events({'type': 'user', 'message': {'content': [{
        'type': 'tool_result', 'tool_use_id': 't1', 'content': [
            {'type': 'image', 'source': {'data': 'private-image-bytes'}},
            {'type': 'text', 'text': 'project healthy'},
        ],
    }]}})[0]
    assert result['preview'] == 'project healthy'
