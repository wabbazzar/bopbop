import asyncio
import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

import db
import main
import runner
import settings


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv('BOPBOP_SIGNAL_ENABLED', '0')
    monkeypatch.setenv('BOPBOP_AGENT_HARNESS', 'ollama')
    monkeypatch.setenv('BOPBOP_OLLAMA_MODEL', 'gpt-oss:20b')
    monkeypatch.setattr(main, 'REQUIRE_BEARER', True)
    monkeypatch.setattr(main, 'BEARER', 'fixture-access')
    monkeypatch.setattr(settings, 'available_harnesses', lambda: [{'id': h, 'available': True} for h in settings.HARNESSES])
    monkeypatch.setattr(settings, 'profiles', lambda: [{'id': 'ice', 'name': 'ice-dashboard'}])
    async def models(): return ['gpt-oss:20b', 'fixture-small:latest']
    monkeypatch.setattr(settings, 'local_models', models)
    with TestClient(main.app) as client:
        yield client


AUTH = {'Authorization': 'Bearer fixture-access'}
WRITE = {**AUTH, 'X-BopBop-Settings': '1', 'Origin': 'http://testserver'}


def change(client, **values):
    current = client.get('/api/settings', headers=AUTH).json()
    data = {k: current[k] for k in ['harness', 'model', 'profile', 'ollama_context', 'revision']}
    return {**data, **values}


def test_settings_require_auth_and_cross_origin_writes_are_rejected(client):
    assert client.get('/api/settings').status_code == 401
    body = change(client)
    assert client.put('/api/settings', json=body).status_code == 401
    assert client.put('/api/settings', headers=AUTH, json=body).status_code == 403
    assert client.put('/api/settings', headers={**WRITE, 'Origin': 'https://another.example.com'}, json=body).status_code == 403
    assert not settings.settings_path().exists()


def test_settings_persist_atomically_without_touching_env_and_reject_stale_writes(client):
    before = dict(os.environ)
    old = change(client, model='fixture-small:latest', profile='ice', ollama_context=8192)
    result = client.put('/api/settings', headers=WRITE, json=old)
    assert result.status_code == 200
    assert result.json()['models']['ollama'] == 'fixture-small:latest'
    assert dict(os.environ) == before
    assert stat.S_IMODE(settings.settings_path().stat().st_mode) == 0o600
    assert 'fixture-access' not in settings.settings_path().read_text()
    assert client.put('/api/settings', headers=WRITE, json=old).status_code == 409
    # A new process/read observes saved preferences, with no service restart.
    assert settings.load().models['ollama'] == 'fixture-small:latest'
    assert settings.snapshot().profile == 'ice'


@pytest.mark.parametrize('values', [
    {'model': 'not-installed:latest'}, {'harness': 'unknown'}, {'profile': 'missing'},
    {'ollama_context': 1}, {'ollama_context': True}, {'ollama_context': 999999},
    {'model': '--help'}, {'model': 'x\nINJECTED=1'}, {'model': 'x; touch /tmp/unsafe'},
    {'model': ''}, {'model': 'x' * 129}, {'server_url': 'http://other.example.com'},
])
def test_unsupported_changes_do_not_mutate_preferences(client, values):
    result = client.put('/api/settings', headers=WRITE, json=change(client, **values))
    assert result.status_code == 422
    assert not settings.settings_path().exists()


def test_ollama_unavailable_is_reported_and_never_downloads_a_model(client, monkeypatch):
    import httpx
    async def offline(): raise httpx.ConnectError('offline')
    body = change(client)
    monkeypatch.setattr(settings, 'local_models', offline)
    result = client.get('/api/settings', headers=AUTH).json()
    assert result['local_models'] == [] and 'unavailable' in result['local_model_error']
    assert client.put('/api/settings', headers=WRITE, json=body).status_code == 503
    assert not settings.settings_path().exists()


def test_failed_atomic_write_preserves_old_settings(client, monkeypatch):
    body = change(client, model='fixture-small:latest')
    assert client.put('/api/settings', headers=WRITE, json=body).status_code == 200
    previous = settings.settings_path().read_bytes()
    def disk_full(*args): raise OSError('disk full')
    monkeypatch.setattr(settings.os, 'replace', disk_full)
    assert client.put('/api/settings', headers=WRITE, json=change(client, model='gpt-oss:20b')).status_code == 503
    assert settings.settings_path().read_bytes() == previous


def test_corrupt_settings_fail_visibly_without_starting_a_turn(client):
    settings.settings_path().write_text('{invalid json')
    for path in ['/api/client', '/api/health', '/api/settings']:
        assert client.get(path, headers=AUTH).status_code == 503
    assert client.post('/api/chat', headers=AUTH, json={'message': 'hello'}).status_code == 503
    with db._conn() as connection:
        assert connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0


@pytest.mark.parametrize('harness', ['claude', 'codex'])
def test_blank_wrapper_model_uses_installation_default(client, harness):
    assert client.put('/api/settings', headers=WRITE, json=change(client, harness=harness, model='')).status_code == 200
    assert runner.agent_model() is None
    assert '--model' not in runner._build_args('hello')[1]
    assert client.get('/api/client').json()['model'] == 'default'


def test_running_turn_freezes_config_and_new_turn_gets_new_model(client, monkeypatch):
    original = settings.snapshot()
    with settings.use(original):
        assert runner.agent_model() == 'gpt-oss:20b'
        # Another request saves to disk while this turn retains its ContextVar.
        updated = settings.save(settings.SettingsChange(**change(client, model='fixture-small:latest')))
        assert updated.models['ollama'] == 'fixture-small:latest'
        assert runner.agent_model() == 'gpt-oss:20b'
        args = runner._build_args('hello')[1]
        assert args[args.index('--model') + 1] == 'gpt-oss:20b'
    assert runner.agent_model() == 'fixture-small:latest'
    assert settings.snapshot().session_key != original.session_key


def test_parallel_turns_have_independent_contextvars(client):
    first = settings.AgentConfig('codex', 'first-model', '', 16384)
    second = settings.AgentConfig('ollama', 'second-model', 'ice', 8192)
    async def turn(config):
        with settings.use(config):
            await asyncio.sleep(0)
            return runner.agent_harness(), runner.agent_model(), settings.current().profile
    async def run(): return await asyncio.gather(turn(first), turn(second))
    assert asyncio.run(run()) == [('codex', 'first-model', ''), ('ollama', 'second-model', 'ice')]


def test_sessions_are_invalidated_when_model_or_pack_changes(tmp_db):
    cid = db.new_conversation('pwa')
    old = settings.AgentConfig('ollama', 'gpt-oss:20b', '', 16384)
    db.set_active_agent_session(cid, 'session-a', 'ollama', old.session_key)
    assert db.get_active_agent_session(cid, 'ollama', old.session_key) == 'session-a'
    for new in [settings.AgentConfig('ollama', 'other-model', '', 16384), settings.AgentConfig('ollama', old.model, 'ice', 16384), settings.AgentConfig('ollama', old.model, '', 8192)]:
        assert db.get_active_agent_session(cid, 'ollama', new.session_key) is None
    # Retaining the same key updates warmth without resetting creation time.
    db.set_active_agent_session(cid, 'session-a', 'ollama', old.session_key)
    assert db.get_active_agent_session(cid, 'ollama', old.session_key) == 'session-a'
