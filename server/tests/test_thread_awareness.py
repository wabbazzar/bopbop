"""Tests for out-of-band thread awareness: peer->conversation mapping, the
/api/thread/record endpoint, unseen-push injection, and Signal quote capture.
Regression coverage for the bug where a reply to a notify.sh push got
'I don't have context' because BopBop never saw the push."""

import time

import db
import signal_channel as sc


# --------------------------------------------------------------------------
# db: peer mapping + source column
# --------------------------------------------------------------------------


def test_peer_conversation_is_stable(tmp_db):
    a = db.get_or_create_conversation_for_peer("signal", "+15550001111")
    b = db.get_or_create_conversation_for_peer("signal", "+15550001111")
    assert a == b  # same peer -> same conversation (survives "restart")
    c = db.get_or_create_conversation_for_peer("signal", "+15550002222")
    assert c != a  # different peer -> different conversation


def test_reset_forces_new_conversation(tmp_db):
    a = db.get_or_create_conversation_for_peer("signal", "+15550001111")
    db.reset_peer_conversation("signal", "+15550001111")
    b = db.get_or_create_conversation_for_peer("signal", "+15550001111")
    assert a != b


def test_add_message_records_source(tmp_db):
    cid = db.new_conversation("signal")
    db.add_message(cid, "assistant", "pushed", source="notify")
    db.add_message(cid, "user", "typed")  # normal turn -> NULL source
    with db._conn() as c:
        rows = {
            r["content"]: r["source"]
            for r in c.execute("SELECT content, source FROM messages").fetchall()
        }
    assert rows["pushed"] == "notify"
    assert rows["typed"] is None


def test_sessions_are_never_resumed_across_harnesses(tmp_db):
    cid = db.new_conversation("signal")
    db.set_active_agent_session(cid, "claude-session", "claude")
    assert db.get_active_agent_session(cid, "claude") == "claude-session"
    assert db.get_active_agent_session(cid, "codex") is None

    db.set_active_agent_session(cid, "codex-thread", "codex")
    assert db.get_active_agent_session(cid, "codex") == "codex-thread"
    assert db.get_active_agent_session(cid, "claude") is None


def test_pre_harness_session_rows_remain_claude_compatible(tmp_db):
    cid = db.new_conversation("signal")
    now = int(time.time())
    with db._conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_id = ?, "
            "claude_session_active_at = ?, claude_session_created_at = ? "
            "WHERE id = ?",
            ("legacy-session", now, now, cid),
        )
    assert db.get_active_agent_session(cid, "claude") == "legacy-session"
    assert db.get_active_agent_session(cid, "codex") is None


# --------------------------------------------------------------------------
# db.get_unseen_pushes: windowing / bounds / ordering
# --------------------------------------------------------------------------


def _insert(cid, content, source, created_at):
    with db._conn() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{content}-{created_at}", cid, "assistant", content, created_at, source),
        )


def test_unseen_pushes_only_after_last_turn(tmp_db):
    cid = db.new_conversation("signal")
    now = int(time.time())
    # Session last active at now-100; a push before that is "seen", after is not.
    db.set_active_claude_session(cid, "sess-1")
    with db._conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_active_at = ? WHERE id = ?",
            (now - 100, cid),
        )
    _insert(cid, "old-push", "notify", now - 200)  # before last turn -> seen
    _insert(cid, "new-push", "notify", now - 50)  # after last turn -> unseen
    _insert(cid, "a-turn", None, now - 10)  # normal turn, never a "push"
    got = db.get_unseen_pushes(cid)
    assert [p["content"] for p in got] == ["new-push"]


def test_unseen_pushes_oldest_first_and_bounded(tmp_db):
    cid = db.new_conversation("signal")
    now = int(time.time())
    db.set_active_claude_session(cid, "sess-1")
    with db._conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_active_at = ? WHERE id = ?",
            (now - 1000, cid),
        )
    for i in range(5):
        _insert(cid, f"p{i}", "notify", now - 500 + i)  # p0 oldest .. p4 newest
    got = db.get_unseen_pushes(cid, limit=3)
    # newest 3 kept, then rendered oldest-first
    assert [p["content"] for p in got] == ["p2", "p3", "p4"]


def test_unseen_pushes_byte_budget(tmp_db):
    cid = db.new_conversation("signal")
    now = int(time.time())
    db.set_active_claude_session(cid, "sess-1")
    with db._conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_active_at = ? WHERE id = ?",
            (now - 1000, cid),
        )
    _insert(cid, "OLD" + "x" * 100, "notify", now - 30)  # oldest
    _insert(cid, "BIG" + "y" * 5000, "notify", now - 20)  # oversized, older
    _insert(cid, "NEW" + "z" * 100, "notify", now - 10)  # newest
    got = db.get_unseen_pushes(cid, max_bytes=2000)
    # Newest is always kept; the older oversized push blows the budget and stops
    # collection (recency wins — that's what a reply is most likely about).
    assert [p["content"][:3] for p in got] == ["NEW"]


def test_unseen_pushes_newest_kept_even_if_oversized(tmp_db):
    cid = db.new_conversation("signal")
    now = int(time.time())
    db.set_active_claude_session(cid, "sess-1")
    with db._conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_active_at = ? WHERE id = ?",
            (now - 1000, cid),
        )
    _insert(cid, "HUGE" + "y" * 5000, "notify", now - 10)
    got = db.get_unseen_pushes(cid, max_bytes=2000)
    assert len(got) == 1 and got[0]["content"].startswith("HUGE")


def test_unseen_pushes_fallback_window_when_fresh(tmp_db):
    cid = db.new_conversation("signal")  # no session -> active_at NULL
    now = int(time.time())
    _insert(cid, "recent", "notify", now - 60)
    _insert(cid, "stale", "notify", now - 99999)
    got = db.get_unseen_pushes(cid, fallback_window=3600)
    assert [p["content"] for p in got] == ["recent"]


# --------------------------------------------------------------------------
# signal_channel: quote extraction + prompt composition
# --------------------------------------------------------------------------


def _cfg(monkeypatch, allowed="+15550001111"):
    monkeypatch.setenv("SIGNAL_ACCOUNT", "+15550009999")
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", allowed)
    return sc.SignalConfig()


def test_extract_quote_from_inbound(monkeypatch):
    cfg = _cfg(monkeypatch)
    env = {
        "envelope": {
            "sourceNumber": "+15550001111",
            "dataMessage": {
                "message": "what was it before?",
                "quote": {"text": "rave-pi fan: idle 48C", "author": "x"},
            },
        }
    }
    msg = sc._extract_message(cfg, env)
    assert msg["text"] == "what was it before?"
    assert msg["quote"] == "rave-pi fan: idle 48C"


def test_extract_quote_from_sync_note_to_self(monkeypatch):
    cfg = _cfg(monkeypatch)
    env = {
        "envelope": {
            "syncMessage": {
                "sentMessage": {
                    "destinationNumber": "+15550009999",
                    "message": "follow up",
                    "quote": {"text": "the summary"},
                }
            }
        }
    }
    msg = sc._extract_message(cfg, env)
    assert msg["text"] == "follow up"
    assert msg["quote"] == "the summary"


def test_extract_message_no_quote(monkeypatch):
    cfg = _cfg(monkeypatch)
    env = {
        "envelope": {
            "sourceNumber": "+15550001111",
            "dataMessage": {"message": "hi"},
        }
    }
    msg = sc._extract_message(cfg, env)
    assert msg["quote"] is None


def test_compose_prompt_injects_thread_context_and_quote():
    pushes = [
        {"created_at": 0, "role": "assistant", "content": "rave-pi fan idle 48C", "source": "notify"}
    ]
    out = sc._compose_prompt(
        "what was it before?", [], thread_context=pushes, quote="rave-pi fan idle 48C"
    )
    assert out.startswith("[channel: signal]")
    assert "[thread-context]" in out
    assert "rave-pi fan idle 48C" in out
    assert "[in-reply-to]" in out


def test_compose_prompt_plain_when_no_context():
    out = sc._compose_prompt("hello", [])
    assert "[thread-context]" not in out
    assert "[in-reply-to]" not in out
    assert out == "[channel: signal]\n\nhello"
