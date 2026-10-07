import asyncio
import json

import runner


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
        async def read(self):
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
