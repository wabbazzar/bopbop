"""Process transport must preserve completed turns and identify abnormal exits."""
import os
import time
from types import SimpleNamespace

from test_cli_tools import local
from bopbop_worker import TurnWorker


def collect(worker, final):
    events = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        events.extend(worker.poll(limit=1))
        if events and events[-1]['kind'] == final:
            return events
        time.sleep(.01)
    raise AssertionError(f'Worker never emitted {final}: {[event["kind"] for event in events]}')


def test_worker_drains_full_evidence_and_completion(local, tmp_path):
    (tmp_path / 'evidence.txt').write_text('evidence\n' * 2000)
    messages = [{'role': 'system', 'content': 'rules'}]
    args = SimpleNamespace(workspace=tmp_path, model='unused', context=16384)
    worker = TurnWorker(local, args, local.AgentRuntime(), messages, local.SessionStats(),
                        '/call read_file {"path":"evidence.txt"}')
    try:
        events = collect(worker, 'finished')
        assert not any(event['kind'] == 'worker_exit' for event in events)
        result = next(event for event in events if event['kind'] == 'tool_complete')
        assert result['ok'] and len(result['result']) == 16000
        completed = events[-1]
        assert completed['messages'][-1]['content'] == result['result']
        assert completed['session'].turns == 1
        assert messages == [{'role': 'system', 'content': 'rules'}]
    finally:
        worker.close()
        worker.close()


def test_worker_reports_abrupt_exit_without_claiming_completion(local, tmp_path, monkeypatch):
    monkeypatch.setattr(local, 'handle_command', lambda *args: os._exit(7))
    args = SimpleNamespace(workspace=tmp_path, model='unused', context=16384)
    worker = TurnWorker(local, args, local.AgentRuntime(), [], local.SessionStats(), '/status')
    try:
        assert collect(worker, 'worker_exit') == [{'kind': 'worker_exit', 'exit_code': 7}]
    finally:
        worker.close()
