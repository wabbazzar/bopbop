"""BopBop Signal channel.

Bridges Signal (via signal-cli-rest-api) to the configured agent subprocess
spawned per turn in BOPBOP_CONTEXT_DIR — so the agent inherits the context,
scripts, skills, and MCP tools that directory provides.

Key behaviors:
  - /v2/send for outbound (produces grey bubbles in Note to Self)
  - syncMessage handling for Note-to-Self with destination == own number
  - Echo-loop prevention via outbound timestamp tracking
  - SIGNAL_ALLOWED_USERS allowlist (drop senders not on it) — required
  - Typing indicator refreshed every 7s during processing
  - 8000-char paragraph-aware message splitting
  - /reset slash command (clears stored conversation id for that sender)
  - Session warmth: turns resume the same harness-specific session while warm
  - Privacy-respecting content detectors (inbound + outbound): log only
    detector name + short matched phrase, never the message body
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Iterable

import httpx

# Where downloaded Signal attachments live (claude reads from here via its
# vision-aware Read tool).
ATTACHMENTS_DIR = (
    Path(os.path.expanduser(os.environ.get("BOPBOP_DATA_DIR", "~/.bopbop/data")))
    / "attachments"
    / "signal"
)

from runner import agent_harness, run_turn
from detectors import scan_inbound, scan_outbound
import db

# Optional telemetry hook: if the context dir ships lib/events.py with a
# log_event() function, message.in/out events flow into it. Absent = no-op.
_CONTEXT_DIR = Path(
    os.path.expanduser(
        os.environ.get("BOPBOP_CONTEXT_DIR", "~/.bopbop/context")
    )
)
sys.path.insert(0, str(_CONTEXT_DIR / "lib"))
try:
    from events import log_event  # type: ignore
except Exception:  # pragma: no cover — graceful if context dir lacks lib
    def log_event(*_a, **_k) -> None:
        return

log = logging.getLogger("bopbop.signal")

MAX_MSG_LEN = 8000
TYPING_INTERVAL = 7.0
POLL_INTERVAL = 1.5


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class SignalConfig:
    def __init__(self) -> None:
        self.signal_url = os.environ.get("SIGNAL_HTTP_URL", "http://127.0.0.1:8080")
        self.signal_account = os.environ.get("SIGNAL_ACCOUNT", "")
        self.allowed_users: set[str] = {
            u.strip()
            for u in os.environ.get("SIGNAL_ALLOWED_USERS", "").split(",")
            if u.strip()
        }

    def errors(self) -> list[str]:
        problems: list[str] = []
        if not self.signal_account:
            problems.append("SIGNAL_ACCOUNT not set")
        elif self.signal_account == "+15550000000":
            problems.append(
                "SIGNAL_ACCOUNT is still the template placeholder — "
                "edit ~/.bopbop/env with your real number"
            )
        if not self.allowed_users:
            problems.append("SIGNAL_ALLOWED_USERS not set")
        return problems


# ---------------------------------------------------------------------------
# Signal I/O — kept identical to agent.py
# ---------------------------------------------------------------------------


_http: httpx.AsyncClient | None = None
_typing_task: asyncio.Task | None = None
_sent_timestamps: deque[str] = deque(maxlen=50)


async def _signal_init(cfg: SignalConfig) -> bool:
    global _http
    _http = httpx.AsyncClient(timeout=30.0)
    try:
        r = await _http.get(f"{cfg.signal_url}/v1/health")
        return r.is_success
    except Exception as e:
        log.warning("signal-cli health check failed: %s", e)
        return False


async def _signal_close() -> None:
    global _http
    if _http:
        await _http.aclose()
        _http = None


def _split_message(text: str, max_len: int) -> list[str]:
    """Paragraph-boundary split, hard cut for paragraphs > max_len. Identical
    to keep messages renderable on phones."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            while len(para) > max_len:
                chunks.append(para[:max_len])
                para = para[max_len:]
            current = para
    if current:
        chunks.append(current)
    return chunks


async def _signal_send(cfg: SignalConfig, recipient: str, text: str) -> None:
    """Send via /v2/send to produce grey bubbles in Note to Self.
    Records outbound timestamps for echo-loop prevention."""
    assert _http is not None
    for chunk in _split_message(text, MAX_MSG_LEN):
        try:
            r = await _http.post(
                f"{cfg.signal_url}/v2/send",
                json={
                    "message": chunk,
                    "number": cfg.signal_account,
                    "recipients": [recipient],
                },
            )
            if r.is_success:
                ts = str(r.json().get("timestamp", ""))
                if ts:
                    _sent_timestamps.append(ts)
            else:
                log.warning("Send failed (%d): %s", r.status_code, r.text[:100])
        except Exception as e:
            log.warning("Send failed: %s", e)


async def _start_typing(cfg: SignalConfig, recipient: str) -> None:
    global _typing_task
    await _stop_typing()

    async def _loop() -> None:
        try:
            while True:
                assert _http is not None
                await _http.put(
                    f"{cfg.signal_url}/v1/typing-indicator/{cfg.signal_account}",
                    json={"recipient": recipient},
                )
                await asyncio.sleep(TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug("Typing indicator error: %s", e)

    _typing_task = asyncio.create_task(_loop())


async def _stop_typing() -> None:
    global _typing_task
    if _typing_task:
        _typing_task.cancel()
        try:
            await _typing_task
        except asyncio.CancelledError:
            pass
        _typing_task = None


# ---------------------------------------------------------------------------
# Receive — poll loop + envelope parsing (Note-to-Self aware)
# ---------------------------------------------------------------------------


async def _detect_mode(cfg: SignalConfig) -> str:
    """Ask the bridge which execution MODE it runs in.

    In json-rpc modes signal-cli keeps a persistent daemon and /v1/receive is
    served as a WebSocket push stream; in normal/native it is a plain HTTP GET
    that spawns a fresh JVM per call. We pick the transport to match instead of
    hard-coding one, so flipping MODE on the container needs no code change.
    """
    assert _http is not None
    try:
        r = await _http.get(f"{cfg.signal_url}/v1/about", timeout=30.0)
        if r.is_success:
            return str(r.json().get("mode") or "normal")
    except Exception as e:
        log.warning("Could not detect signal-cli mode (%s); assuming normal", e)
    return "normal"


async def _receive_ws(cfg: SignalConfig, queue: asyncio.Queue) -> None:
    """json-rpc mode: receive is a WebSocket the bridge pushes envelopes down.

    Replaces the 1.5s HTTP poll, which in MODE=normal cold-started a signal-cli
    JVM per request (~50% of a core burned continuously, bursts over 300%).
    """
    import websockets

    ws_url = cfg.signal_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/v1/receive/{cfg.signal_account}"
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                ws_url, ping_interval=30, ping_timeout=20, max_size=None
            ) as ws:
                log.info("Signal receive websocket connected (%s)", ws_url)
                backoff = 1.0
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    envelopes = payload if isinstance(payload, list) else [payload]
                    for env in envelopes:
                        msg = _extract_message(cfg, env)
                        if msg:
                            await queue.put(msg)
                log.warning("Signal receive websocket closed cleanly; reconnecting")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(
                "Signal receive websocket dropped (%s); retrying in %.0fs", e, backoff
            )
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            break
        backoff = min(backoff * 2, 30.0)


async def _poll_messages(cfg: SignalConfig, queue: asyncio.Queue) -> None:
    """Dispatch to the receive transport that matches the bridge's MODE."""
    mode = await _detect_mode(cfg)
    if mode.startswith("json-rpc"):
        log.info("signal-cli mode=%s — using websocket receive", mode)
        await _receive_ws(cfg, queue)
    else:
        log.info("signal-cli mode=%s — using HTTP poll receive", mode)
        await _receive_http(cfg, queue)


async def _receive_http(cfg: SignalConfig, queue: asyncio.Queue) -> None:
    assert _http is not None
    url = f"{cfg.signal_url}/v1/receive/{cfg.signal_account}"
    consecutive_errors = 0
    while True:
        try:
            r = await _http.get(url, params={"timeout": "1"})
            if r.is_success:
                consecutive_errors = 0
                envelopes = r.json()
                if not isinstance(envelopes, list):
                    envelopes = []
                for env in envelopes:
                    msg = _extract_message(cfg, env)
                    if msg:
                        await queue.put(msg)
            else:
                consecutive_errors += 1
                log.warning("Poll failed (%d): %s", r.status_code, r.text[:100])
        except asyncio.CancelledError:
            break
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors <= 3:
                log.warning("Poll error: %s", e)
            elif consecutive_errors == 4:
                log.warning("Poll errors continuing, suppressing logs")
        await asyncio.sleep(POLL_INTERVAL)


def _parse_attachments(items: list) -> list[dict]:
    """Normalize signal-cli's attachment metadata into our shape."""
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        att_id = item.get("id")
        if not att_id:
            continue
        out.append(
            {
                "id": str(att_id),
                "content_type": item.get("contentType")
                or "application/octet-stream",
                "filename": item.get("filename") or item.get("file") or "",
                "size": int(item.get("size") or 0),
            }
        )
    return out


def _extract_message(cfg: SignalConfig, data: dict) -> dict | None:
    """Returns {sender, text, attachments} or None to drop. Mirrors
    the reference signal-cli envelope handling and additionally surfaces attachments so
    images/files reach the handler instead of being silently dropped."""
    env = data.get("envelope", data)

    if "syncMessage" in env:
        sync = env["syncMessage"]
        sent = sync.get("sentMessage")
        if not sent:
            return None
        dest = sent.get("destinationNumber") or sent.get("destination", "")
        if dest != cfg.signal_account:
            return None
        sent_ts = str(sent.get("timestamp", ""))
        if sent_ts in _sent_timestamps:
            log.debug("Ignoring echo (timestamp %s)", sent_ts)
            return None
        text = (sent.get("message") or "").strip()
        attachments = _parse_attachments(sent.get("attachments") or [])
        if not text and not attachments:
            return None
        return {
            "sender": cfg.signal_account,
            "text": text,
            "attachments": attachments,
            "quote": _extract_quote(sent),
        }

    sender = env.get("sourceNumber") or env.get("source")
    if not sender or sender == cfg.signal_account:
        return None
    if sender not in cfg.allowed_users:
        return None

    data_msg = env.get("dataMessage") or (env.get("editMessage") or {}).get(
        "dataMessage"
    )
    if not data_msg:
        return None
    text = (data_msg.get("message") or "").strip()
    attachments = _parse_attachments(data_msg.get("attachments") or [])
    if not text and not attachments:
        return None
    return {
        "sender": sender,
        "text": text,
        "attachments": attachments,
        "quote": _extract_quote(data_msg),
    }


def _extract_quote(msg: dict) -> str | None:
    """The text of the message this one is a reply to, if any. Signal carries
    it in the envelope, so a reply-to keeps its referent even when the quoted
    message never passed through BopBop."""
    quote = msg.get("quote") or {}
    if not isinstance(quote, dict):
        return None
    qtext = (quote.get("text") or "").strip()
    return qtext or None


async def _download_attachment(cfg: SignalConfig, att: dict) -> Path | None:
    """Fetch the binary blob for one attachment via signal-cli and save it
    under data/attachments/signal/YYYY-MM-DD/. Returns the saved path."""
    assert _http is not None
    att_id = att["id"]

    # Pick a filename extension: prefer the original, fall back to mime type
    ext = ""
    if att.get("filename"):
        ext = Path(att["filename"]).suffix
    if not ext:
        ct = att.get("content_type", "")
        if "/" in ct:
            ext = "." + ct.split("/")[-1].split(";")[0].strip()
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in att_id)[:60]
    out_dir = ATTACHMENTS_DIR / time.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{int(time.time() * 1000)}_{safe_id}{ext}"

    try:
        r = await _http.get(f"{cfg.signal_url}/v1/attachments/{att_id}")
        if not r.is_success:
            log.warning("Attachment %s download failed: %d", att_id, r.status_code)
            return None
        out_path.write_bytes(r.content)
        log.info(
            "Downloaded attachment %s → %s (%d bytes, %s)",
            att_id[:12],
            out_path,
            len(r.content),
            att.get("content_type", "?"),
        )
        return out_path
    except Exception as e:
        log.warning("Attachment %s download failed: %s", att_id, e)
        return None


def _format_thread_context(pushes: list[dict]) -> str:
    """Render out-of-band thread messages (e.g. notify.sh alerts) as a compact
    prompt preamble so a reply to one of them has its context."""
    if not pushes:
        return ""
    lines = []
    for p in pushes:
        stamp = time.strftime("%H:%M", time.localtime(p.get("created_at") or 0))
        src = p.get("source") or "automated"
        body = " ".join((p.get("content") or "").split())
        lines.append(f"  - ({src}, {stamp}) {body}")
    return (
        "[thread-context] Messages in this Signal thread since your last turn — "
        "some sent automatically on your behalf, not typed by you. The user may "
        "be replying to one of these:\n" + "\n".join(lines) + "\n\n"
    )


def _compose_prompt(
    text: str,
    attachment_paths: list[Path],
    thread_context: list[dict] | None = None,
    quote: str | None = None,
) -> str:
    """Build the prompt fed to `claude`. Prepends `[channel: signal]` so the
    personality file can switch to Signal-flavored brevity. If attachments
    exist, instructs claude to use its Read tool — Read handles vision.
    `thread_context` (out-of-band pushes) and `quote` (the message being
    replied to) are surfaced so replies to messages BopBop didn't itself send
    still have their referent."""
    body = text or ""
    if quote:
        body = f'[in-reply-to] "{" ".join(quote.split())}"\n\n' + body
    if attachment_paths:
        paths_block = "\n".join(f"  - {p}" for p in attachment_paths)
        n = len(attachment_paths)
        plural = "s" if n != 1 else ""
        note = (
            f"\n\n[Signal attachment{plural}: the user sent {n} file{plural} "
            f"alongside this message. Use the Read tool on each path to view "
            f"them (images are rendered visually):\n{paths_block}\n]"
        )
        body = body + note
    return "[channel: signal]\n\n" + _format_thread_context(thread_context or []) + body


# ---------------------------------------------------------------------------
# Per-sender conversation tracking (one ongoing thread per Signal sender)
# ---------------------------------------------------------------------------


_conversations: dict[str, str] = {}  # sender → bopbop conversation_id

# Test hook: run() parks its live queue here so /api/test/signal-inject can
# feed messages through the exact production path (turn spawn, channel
# marker, reply via /v2/send). Only the poll/_extract_message step is
# bypassed — a same-device self-send never echoes back through /v1/receive,
# so the phone's syncMessage path can't be emulated from this box.
_inject_queue: asyncio.Queue | None = None


async def inject_message(text: str) -> bool:
    """Feed a message into the live Signal queue as if the account owner sent it
    from their phone (same shape _extract_message produces). Returns False if the
    Signal channel isn't running."""
    if _inject_queue is None:
        return False
    await _inject_queue.put(
        {
            "sender": os.environ.get("SIGNAL_ACCOUNT", ""),
            "text": text,
            "attachments": [],
        }
    )
    return True


def _get_or_create_conversation(sender: str) -> str:
    # Backed by the persistent channel_peers table so a service restart resumes
    # the same conversation (and warm agent session) instead of forking a new
    # one. The dict is just an in-process cache.
    cid = _conversations.get(sender)
    if cid is None:
        cid = db.get_or_create_conversation_for_peer("signal", sender)
        _conversations[sender] = cid
    return cid


def _reset_conversation(sender: str) -> None:
    _conversations.pop(sender, None)
    db.reset_peer_conversation("signal", sender)


# ---------------------------------------------------------------------------
# Run one turn through the configured agent CLI subprocess
# ---------------------------------------------------------------------------


async def _run_agent(
    text: str, resume_session_id: str | None = None
) -> tuple[str, str | None]:
    """Return reply text and the harness session id for warm resumption."""
    parts: list[str] = []
    new_session_id: str | None = None
    async for ev in run_turn(text, resume_session_id=resume_session_id):
        kind = ev.get("kind")
        if kind == "session":
            new_session_id = ev.get("session_id")
        elif kind == "text":
            parts.append(ev.get("delta", ""))
        elif kind == "error":
            log.warning("agent error: %s", ev.get("message", ""))
    return ("".join(parts).strip() or "(no response)", new_session_id)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run() -> None:
    """Run the Signal channel. Exits on cancellation."""
    cfg = SignalConfig()
    errors = cfg.errors()
    if errors:
        for e in errors:
            log.error("Config: %s", e)
        log.error("Signal channel disabled — fix config and restart")
        return

    # Wait for signal-cli with retry
    for attempt in range(10):
        if await _signal_init(cfg):
            break
        log.warning(
            "signal-cli not ready, retrying in 5s (attempt %d/10)", attempt + 1
        )
        await asyncio.sleep(5)
    else:
        log.error("Cannot connect to signal-cli after 10 attempts")
        return

    global _inject_queue
    queue: asyncio.Queue = asyncio.Queue()
    _inject_queue = queue
    poll_task = asyncio.create_task(_poll_messages(cfg, queue))

    log.info(
        "BopBop Signal channel started — account=%s, polling every %.1fs",
        cfg.signal_account,
        POLL_INTERVAL,
    )

    try:
        while True:
            msg = await queue.get()
            sender = msg["sender"]
            text = msg["text"]
            attachments = msg.get("attachments") or []
            quote = msg.get("quote")
            log.info(
                "← %s: %s%s",
                sender[-4:],
                text[:80] if text else "(no text)",
                f" [+{len(attachments)} att]" if attachments else "",
            )
            actor = "self" if sender == cfg.signal_account else sender[-4:]
            turn_start = time.monotonic()
            log_event(
                "bopbop-signal",
                "message.in",
                actor=actor,
                source="user",
                bytes=len(text),
                attachments=len(attachments),
            )
            for detector, reason in scan_inbound(text):
                log_event(
                    "bopbop-signal",
                    "message.flagged",
                    direction="in",
                    source="user",
                    actor=actor,
                    detector=detector,
                    reason=reason,
                )

            if text.strip().lower() == "/reset":
                old_cid = _conversations.get(sender)
                if old_cid:
                    db.clear_agent_session(old_cid)
                _reset_conversation(sender)
                await _signal_send(cfg, sender, "Session reset.")
                log_event("bopbop-signal", "session.reset", source="user", actor=actor)
                continue

            # Download attachments before starting the agent so their local
            # paths can be included in the prompt.
            attachment_paths: list[Path] = []
            for att in attachments:
                p = await _download_attachment(cfg, att)
                if p is not None:
                    attachment_paths.append(p)

            cid = _get_or_create_conversation(sender)
            db_text = text
            if attachment_paths:
                db_text = (text + "\n" if text else "") + "[attachments: " + ", ".join(
                    str(p) for p in attachment_paths
                ) + "]"
            db.add_message(cid, "user", db_text)

            harness = agent_harness()
            resume_id = db.get_active_agent_session(cid, harness)
            # Out-of-band messages (notify.sh alerts, etc.) that hit this thread
            # since the last turn — the resumed session hasn't seen them, so the
            # user could be replying to one. Surface them in the prompt.
            pushes = db.get_unseen_pushes(cid)

            await _start_typing(cfg, sender)
            try:
                prompt = _compose_prompt(
                    text, attachment_paths, thread_context=pushes, quote=quote
                )
                reply, new_session_id = await _run_agent(
                    prompt, resume_session_id=resume_id
                )
                if new_session_id:
                    db.set_active_agent_session(cid, new_session_id, harness)
                await _stop_typing()
                for detector, reason in scan_outbound(reply):
                    log_event(
                        "bopbop-signal",
                        "message.flagged",
                        direction="out",
                        source="agent",
                        actor=actor,
                        detector=detector,
                        reason=reason,
                    )
                db.add_message(cid, "assistant", reply)
                await _signal_send(cfg, sender, reply)
                log.info("→ %s: %s", sender[-4:], reply[:80])
                log_event(
                    "bopbop-signal",
                    "message.out",
                    actor=actor,
                    source="agent",
                    latency_ms=int((time.monotonic() - turn_start) * 1000),
                    bytes=len(reply),
                    status="ok",
                )
            except asyncio.CancelledError:
                await _stop_typing()
                raise
            except Exception:
                await _stop_typing()
                log.exception("Error handling Signal message")
                await _signal_send(cfg, sender, "Something went wrong. Check logs.")
                log_event(
                    "bopbop-signal",
                    "message.out",
                    actor=actor,
                    source="agent",
                    latency_ms=int((time.monotonic() - turn_start) * 1000),
                    status="error",
                )
    finally:
        log.info("BopBop Signal channel shutting down")
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        await _stop_typing()
        await _signal_close()
