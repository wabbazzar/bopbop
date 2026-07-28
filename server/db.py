import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.path.expanduser(os.environ.get("BOPBOP_DATA_DIR", "~/.bopbop/data")))
DB_PATH = os.environ.get("BOPBOP_DB_PATH", str(DATA_DIR / "bopbop.db"))

# An agent session stays "warm" until BOTH expiry conditions fail:
#   - more than SESSION_IDLE_TIMEOUT seconds since the last user message
#   - AND more than SESSION_MIN_TURNS user-message turns since the session
#     was created
# Whichever extends the session longer wins (max-of).
SESSION_IDLE_TIMEOUT = 3600  # 1 hour
SESSION_MIN_TURNS = 20  # keep at least this many turns of context


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id, created_at);
            -- Stable peer -> conversation mapping so a restart doesn't fork a
            -- new conversation (and drop the warm agent session) for an
            -- ongoing thread. One row per (channel, peer).
            CREATE TABLE IF NOT EXISTS channel_peers (
                channel TEXT NOT NULL,
                peer TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                PRIMARY KEY (channel, peer)
            );
            """
        )
        # Migrate: tag messages with their source so out-of-band pushes (e.g.
        # a notify.sh alert sent straight to the channel, bypassing a turn) can
        # be told apart from real conversation turns. NULL == a normal turn.
        msg_cols = {
            row["name"] for row in c.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "source" not in msg_cols:
            c.execute("ALTER TABLE messages ADD COLUMN source TEXT")
        # Migrate: add session columns if upgrading from a pre-V2 DB. The
        # historical column names stay in place for a zero-copy upgrade.
        existing_cols = {
            row["name"]
            for row in c.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "claude_session_id" not in existing_cols:
            c.execute(
                "ALTER TABLE conversations ADD COLUMN claude_session_id TEXT"
            )
        if "claude_session_active_at" not in existing_cols:
            c.execute(
                "ALTER TABLE conversations "
                "ADD COLUMN claude_session_active_at INTEGER"
            )
        if "claude_session_created_at" not in existing_cols:
            c.execute(
                "ALTER TABLE conversations "
                "ADD COLUMN claude_session_created_at INTEGER"
            )
        if "agent_harness" not in existing_cols:
            c.execute("ALTER TABLE conversations ADD COLUMN agent_harness TEXT")


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def new_conversation(channel: str) -> str:
    cid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (id, channel, created_at) VALUES (?, ?, ?)",
            (cid, channel, int(time.time())),
        )
    return cid


def add_message(
    conversation_id: str, role: str, content: str, source: str | None = None
) -> str:
    mid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, int(time.time()), source),
        )
    return mid


def get_or_create_conversation_for_peer(channel: str, peer: str) -> str:
    """Return the persistent conversation id for (channel, peer), creating it
    on first contact. Backed by the channel_peers table so the mapping
    survives process restarts (unlike an in-memory dict)."""
    with _conn() as c:
        row = c.execute(
            "SELECT conversation_id FROM channel_peers WHERE channel = ? AND peer = ?",
            (channel, peer),
        ).fetchone()
        if row:
            return row["conversation_id"]
        cid = str(uuid.uuid4())
        now = int(time.time())
        c.execute(
            "INSERT INTO conversations (id, channel, created_at) VALUES (?, ?, ?)",
            (cid, channel, now),
        )
        c.execute(
            "INSERT INTO channel_peers (channel, peer, conversation_id) "
            "VALUES (?, ?, ?)",
            (channel, peer, cid),
        )
        return cid


def reset_peer_conversation(channel: str, peer: str) -> None:
    """Forget the peer->conversation mapping so the next message starts a fresh
    conversation (and thus a fresh agent session). Message history is kept."""
    with _conn() as c:
        c.execute(
            "DELETE FROM channel_peers WHERE channel = ? AND peer = ?",
            (channel, peer),
        )


def get_unseen_pushes(
    conversation_id: str,
    limit: int = 8,
    max_bytes: int = 2000,
    fallback_window: int = 3600,
) -> list[dict]:
    """Out-of-band messages (source set, i.e. not a normal turn) recorded since
    the agent session was last active — i.e. messages the resumed session has
    NOT seen. These are pushed into the next turn's prompt so a reply to, say, a
    notify.sh alert has the context it's replying to.

    Bounded newest-first by count and total bytes (oldest dropped first), then
    returned oldest-first for prompt rendering. If the session has no
    active-at stamp (fresh), fall back to a `fallback_window`-second lookback.
    Returns dicts: {created_at, role, content, source}."""
    now = int(time.time())
    with _conn() as c:
        row = c.execute(
            "SELECT claude_session_active_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        since = (row["claude_session_active_at"] if row else None) or (
            now - fallback_window
        )
        rows = c.execute(
            "SELECT created_at, role, content, source FROM messages "
            "WHERE conversation_id = ? AND source IS NOT NULL AND source != 'turn' "
            "  AND created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (conversation_id, since, limit),
        ).fetchall()
    picked: list[dict] = []
    total = 0
    for r in rows:  # newest-first: keep until the byte budget is spent
        content = r["content"] or ""
        total += len(content)
        if picked and total > max_bytes:
            break
        picked.append(
            {
                "created_at": r["created_at"],
                "role": r["role"],
                "content": content,
                "source": r["source"],
            }
        )
    picked.reverse()  # oldest-first for rendering
    return picked


def get_active_agent_session(
    conversation_id: str, harness: str = "claude"
) -> str | None:
    """Return a warm session only when it belongs to the selected harness.

    Rows created before harness selection existed have ``agent_harness=NULL``;
    those are Claude sessions and remain resumable when using Claude.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT claude_session_id, claude_session_active_at, "
            "       claude_session_created_at, agent_harness "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not row or not row["claude_session_id"]:
            return None
        stored_harness = row["agent_harness"] or "claude"
        if stored_harness != harness:
            return None
        idle = int(time.time()) - (row["claude_session_active_at"] or 0)
        if idle <= SESSION_IDLE_TIMEOUT:
            return row["claude_session_id"]
        # Idle window closed — but if we haven't had 20 turns yet, keep going.
        created = row["claude_session_created_at"] or 0
        n = c.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE conversation_id = ? AND role = 'user' AND created_at >= ?",
            (conversation_id, created),
        ).fetchone()["n"]
        if n < SESSION_MIN_TURNS:
            return row["claude_session_id"]
    return None


def set_active_agent_session(
    conversation_id: str, session_id: str, harness: str = "claude"
) -> None:
    """Persist an agent session id together with the harness that owns it."""
    now = int(time.time())
    with _conn() as c:
        row = c.execute(
            "SELECT claude_session_id, agent_harness "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        stored_harness = (row["agent_harness"] or "claude") if row else None
        if (
            row
            and row["claude_session_id"] == session_id
            and stored_harness == harness
        ):
            c.execute(
                "UPDATE conversations SET claude_session_active_at = ?, "
                "agent_harness = ? "
                "WHERE id = ?",
                (now, harness, conversation_id),
            )
        else:
            c.execute(
                "UPDATE conversations SET claude_session_id = ?, "
                "claude_session_created_at = ?, claude_session_active_at = ?, "
                "agent_harness = ? "
                "WHERE id = ?",
                (session_id, now, now, harness, conversation_id),
            )


def clear_agent_session(conversation_id: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_id = NULL, "
            "claude_session_active_at = NULL, "
            "claude_session_created_at = NULL, agent_harness = NULL "
            "WHERE id = ?",
            (conversation_id,),
        )


# Backward-compatible names for existing callers and third-party scripts.
def get_active_claude_session(conversation_id: str) -> str | None:
    return get_active_agent_session(conversation_id, "claude")


def set_active_claude_session(conversation_id: str, session_id: str) -> None:
    set_active_agent_session(conversation_id, session_id, "claude")


def clear_claude_session(conversation_id: str) -> None:
    clear_agent_session(conversation_id)
