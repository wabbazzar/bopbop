import asyncio
import json
import sys

import runner
from test_cli_tools import installed
from test_inspector import model_server


def test_quiet_agent_sends_heartbeats_and_drains_stderr(monkeypatch, tmp_path):
    script = ('import sys,time\nsys.stderr.write("x"*200000);sys.stderr.flush()\n'
              'time.sleep(.15)\nprint(\'{"kind":"done"}\',flush=True)\n')
    monkeypatch.setattr(runner, '_build_args', lambda *args: ('ollama', [sys.executable, '-c', script]))
    monkeypatch.setattr(runner, 'CONTEXT_DIR', str(tmp_path))
    monkeypatch.setattr(runner, 'HEARTBEAT_SECONDS', .02)
    async def collect():
        return [event async for event in runner.run_turn('unused')]
    events = asyncio.run(asyncio.wait_for(collect(), timeout=3))
    assert events[-1] == {'kind': 'done'}
    assert any(event['kind'] == 'progress' and event['elapsed_ms'] > 0 for event in events)


def test_failed_agent_reports_the_error_tail_without_blocking(monkeypatch, tmp_path):
    script = 'import sys\nsys.stderr.write("x"*200000+"\\nconcrete failure\\n");sys.exit(7)\n'
    monkeypatch.setattr(runner, '_build_args', lambda *args: ('ollama', [sys.executable, '-c', script]))
    monkeypatch.setattr(runner, 'CONTEXT_DIR', str(tmp_path))
    async def collect():
        return [event async for event in runner.run_turn('unused')]
    events = asyncio.run(asyncio.wait_for(collect(), timeout=3))
    assert events[-1]['kind'] == 'error'
    assert 'concrete failure' in events[-1]['message']
    assert len(events[-1]['message']) < 550


CODEX_CONTEXT_PREFIX = (
    "This project may carry shared cross-harness instructions in CLAUDE.md "
    "and BopBop personality/context files. Read and follow CLAUDE.md and "
    "context/personality.md when present, in addition to AGENTS.md.\n\n"
)


def test_claude_args_remain_backward_compatible(monkeypatch):
    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "claude")
    monkeypatch.setenv("BOPBOP_CLAUDE_BIN", "/opt/claude")
    monkeypatch.setenv("BOPBOP_CLAUDE_MODEL", "sonnet")

    harness, args = runner._build_args("hello", "claude-session")

    assert harness == "claude"
    assert args == [
        "/opt/claude",
        "-p",
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--resume",
        "claude-session",
        "hello",
    ]


def test_codex_new_turn_args_use_json_and_machine_access(monkeypatch):
    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "codex")
    monkeypatch.setenv("BOPBOP_CODEX_BIN", "/opt/codex")
    monkeypatch.delenv("BOPBOP_CODEX_MODEL", raising=False)

    harness, args = runner._build_args("hello")

    assert harness == "codex"
    assert args == [
        "/opt/codex",
        "exec",
        "--json",
        "--color",
        "never",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        CODEX_CONTEXT_PREFIX + "hello",
    ]


def test_codex_resume_uses_same_thread_and_optional_model(monkeypatch):
    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "codex")
    monkeypatch.setenv("BOPBOP_CODEX_MODEL", "gpt-test")

    _, args = runner._build_args("follow up", "codex-thread")

    assert args == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--model",
        "gpt-test",
        "codex-thread",
        CODEX_CONTEXT_PREFIX + "follow up",
    ]


def test_codex_jsonl_is_normalized_to_bopbop_events():
    assert runner._codex_events(
        {"type": "thread.started", "thread_id": "thread-1"}, 12
    ) == [{"kind": "session", "session_id": "thread-1"}]
    # Agent messages are buffered by run_turn so progress commentary is not
    # concatenated into the Signal reply.
    assert runner._codex_events(
        {
            "type": "item.completed",
            "item": {"id": "item-1", "type": "agent_message", "text": "hello"},
        },
        20,
    ) == []
    assert runner._codex_events(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
        123,
    ) == [
        {
            "kind": "done",
            "cost_usd": None,
            "duration_ms": 123,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
    ]


def test_unknown_harness_fails_closed(monkeypatch):
    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "other")

    try:
        runner.agent_harness()
    except ValueError as exc:
        assert "unsupported BOPBOP_AGENT_HARNESS" in str(exc)
    else:
        raise AssertionError("unsupported harness was accepted")


def test_ollama_runner_uses_local_transport_and_resume(monkeypatch):
    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "ollama")
    monkeypatch.setenv("BOPBOP_OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("BOPBOP_OLLAMA_CONTEXT", "8192")
    harness, args = runner._build_args("hello", "2ed153e6-e45b-4378-8f2e-10d7ab8062e9")
    assert harness == "ollama"
    assert args[:3] == ["python3", args[1], "--jsonl"]
    assert args[1].endswith("/bin/bopbop-local.py")
    assert args[-5:] == ["--context", "8192", "--session-id",
                         "2ed153e6-e45b-4378-8f2e-10d7ab8062e9", "hello"]


def test_codex_turn_emits_only_final_agent_message(monkeypatch):
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "progress",
                "type": "agent_message",
                "text": "I am checking the project.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "final",
                "type": "agent_message",
                "text": "Final answer.",
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    class FakeStdout:
        def __aiter__(self):
            async def lines():
                for event in events:
                    yield (json.dumps(event) + "\n").encode()

            return lines()

    class FakeStderr:
        async def read(self, *unused):
            return b""

    class FakeProcess:
        stdout = FakeStdout()
        stderr = FakeStderr()
        returncode = 0

        async def wait(self):
            return 0

        def kill(self):
            raise AssertionError("completed process should not be killed")

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setenv("BOPBOP_AGENT_HARNESS", "codex")
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_subprocess)

    async def collect():
        return [event async for event in runner.run_turn("hello")]

    normalized = asyncio.run(collect())
    assert [e for e in normalized if e["kind"] == "text"] == [
        {"kind": "text", "delta": "Final answer."}
    ]


def test_wrapper_durations_follow_tool_ids_when_results_finish_out_of_order(monkeypatch):
    from types import SimpleNamespace
    events = [
        {'type': 'item.started', 'item': {'id': 'a', 'type': 'command_execution'}},
        {'type': 'item.started', 'item': {'id': 'b', 'type': 'command_execution'}},
        {'type': 'item.completed', 'item': {'id': 'b', 'type': 'command_execution', 'status': 'completed', 'aggregated_output': 'B'}},
        {'type': 'item.completed', 'item': {'id': 'a', 'type': 'command_execution', 'status': 'completed', 'aggregated_output': 'A'}},
        {'type': 'turn.completed', 'usage': {}},
    ]
    class Stdout:
        def __aiter__(self):
            async def lines():
                for event in events:
                    yield (json.dumps(event) + '\n').encode()
            return lines()
    class Process:
        stdout = Stdout()
        stderr = None
        returncode = 0
        async def wait(self): return 0
    async def subprocess(*args, **kwargs): return Process()
    clock = iter([0, .1, .2, .6, .9, 1.0])
    monkeypatch.setenv('BOPBOP_AGENT_HARNESS', 'codex')
    monkeypatch.setattr(runner.asyncio, 'create_subprocess_exec', subprocess)
    monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    async def collect(): return [event async for event in runner.run_turn('hello')]
    results = [event for event in asyncio.run(collect()) if event['kind'] == 'tool_result']
    assert [(event['id'], event['preview'], event['duration_ms']) for event in results] == [('b', 'B', 400), ('a', 'A', 800)]


def test_web_workspace_agent_loads_server_packs_alongside_workspace_tools(
    monkeypatch, tmp_path, installed, model_server,
):
    """Exercise the real server subprocess, not the terminal launcher defaults."""
    url, requests = model_server
    monkeypatch.setenv('BOPBOP_AGENT_HARNESS', 'ollama')
    monkeypatch.setenv('BOPBOP_AGENT_PROFILE', '')
    monkeypatch.setenv('BOPBOP_AGENT_PACKS', 'test')
    monkeypatch.setenv('BOPBOP_CLI_PACKS', 'terminal-only-not-installed')
    monkeypatch.setenv('BOPBOP_CONTEXT_DIR', str(installed.parents[1]))
    monkeypatch.setenv('BOPBOP_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('BOPBOP_PYTHON_BIN', sys.executable)
    monkeypatch.setenv('BOPBOP_OLLAMA_HOST', url)
    monkeypatch.setattr(runner, 'CONTEXT_DIR', str(installed.parents[1]))

    async def collect():
        return [event async for event in runner.run_turn('[channel: pwa]\nCheck project health.')]

    events = asyncio.run(asyncio.wait_for(collect(), timeout=10))
    assert not any(event['kind'] == 'error' for event in events)
    assert events[-1]['kind'] == 'done'
    names = {tool['function']['name'] for tool in requests[0]['tools']}
    assert {'sample_count', 'read_file', 'run_command'} <= names
    assert ''.join(e['delta'] for e in events if e['kind'] == 'text') == 'Fixture answer.'
