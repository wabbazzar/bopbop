import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import db
from runner import run_turn

# Auth model: BopBop binds to 127.0.0.1 and is designed to sit behind a
# private network boundary you trust — a VPN/tailnet (e.g. Tailscale Serve)
# or an authenticating reverse proxy. If you expose it any wider, set
# BOPBOP_REQUIRE_BEARER=1 (and BOPBOP_BEARER_TOKEN) to gate /api/chat.
# NEVER expose it to the public internet unauthenticated: the agent runs
# `claude --dangerously-skip-permissions` with full access to this machine.
REQUIRE_BEARER = os.environ.get("BOPBOP_REQUIRE_BEARER", "0") == "1"
BEARER = os.environ.get("BOPBOP_BEARER_TOKEN")
if REQUIRE_BEARER and not BEARER:
    raise SystemExit(
        "BOPBOP_REQUIRE_BEARER=1 but BOPBOP_BEARER_TOKEN is unset"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD = REPO_ROOT / "build"
PUBLIC = REPO_ROOT / "public"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    signal_task: asyncio.Task | None = None
    if os.environ.get("BOPBOP_SIGNAL_ENABLED") == "1":
        from signal_channel import run as run_signal  # lazy: only if enabled
        signal_task = asyncio.create_task(run_signal(), name="bopbop-signal")
        logging.getLogger("bopbop").info("Signal channel task started")
    try:
        yield
    finally:
        if signal_task is not None:
            signal_task.cancel()
            try:
                await signal_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="BopBop", lifespan=lifespan)


class ChatReq(BaseModel):
    message: str
    conversation_id: str | None = None


def _auth(authorization: str | None) -> None:
    if not REQUIRE_BEARER:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    if authorization[7:] != BEARER:
        raise HTTPException(401, "Invalid bearer token")


@app.post("/api/chat")
async def chat(req: ChatReq, authorization: str | None = Header(None)):
    _auth(authorization)

    cid = req.conversation_id or db.new_conversation("pwa")
    db.add_message(cid, "user", req.message)
    prompt = "[channel: pwa]\n\n" + req.message
    resume_id = db.get_active_claude_session(cid)

    async def stream():
        yield json.dumps({"kind": "meta", "conversation_id": cid}) + "\n"

        full_text: list[str] = []
        new_session_id: str | None = None
        async for ev in run_turn(prompt, resume_session_id=resume_id):
            kind = ev.get("kind")
            if kind == "session":
                new_session_id = ev.get("session_id")
                continue  # internal — don't surface session ids to the client
            if kind == "text":
                full_text.append(ev["delta"])
            yield json.dumps(ev) + "\n"

        if new_session_id:
            db.set_active_claude_session(cid, new_session_id)
        if full_text:
            db.add_message(cid, "assistant", "".join(full_text))

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class ThreadRecordReq(BaseModel):
    peer: str
    text: str
    channel: str = "signal"
    role: str = "assistant"
    source: str = "external"


@app.post("/api/thread/record")
async def thread_record(
    req: ThreadRecordReq, authorization: str | None = Header(None)
):
    """Record a message that reached a channel thread out-of-band — e.g. an
    automated alert pushed straight to the messaging backend, bypassing a
    normal turn. It's attributed to the peer's persistent conversation so the
    next turn's prompt can surface it (see signal_channel prompt injection),
    letting the agent field replies to messages it didn't itself generate."""
    _auth(authorization)
    if not req.peer or not req.text:
        raise HTTPException(400, "peer and text are required")
    cid = db.get_or_create_conversation_for_peer(req.channel, req.peer)
    mid = db.add_message(cid, req.role, req.text, source=(req.source or "external"))
    return {"ok": True, "conversation_id": cid, "message_id": mid}


class InjectReq(BaseModel):
    message: str


@app.post("/api/test/signal-inject")
async def signal_inject(req: InjectReq, request: Request):
    """Test-only: emulate an inbound Note-to-Self message. Localhost-only —
    the Tailscale Serve proxy would surface as a tailnet IP and is refused.
    Feeds signal_channel's live queue; the reply goes out via real /v2/send
    (grey bubble in Note-to-Self), so the full production path is exercised."""
    if request.client and request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "localhost only")
    from signal_channel import inject_message

    if not await inject_message(req.message):
        raise HTTPException(503, "signal channel not running")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "context_dir": os.environ.get("BOPBOP_CONTEXT_DIR"),
        "model": os.environ.get("BOPBOP_CLAUDE_MODEL", "sonnet"),
        "signal_enabled": os.environ.get("BOPBOP_SIGNAL_ENABLED") == "1",
        "signal_account": os.environ.get("SIGNAL_ACCOUNT"),
        "require_bearer": REQUIRE_BEARER,
    }


# HTTP middleware that disables caching for the PWA shell + service
# worker + manifest. Hashed `_app/immutable/*` assets keep their default
# long-cache headers because their filenames change every build.
# Implemented as middleware (rather than a StaticFiles subclass) because
# the html=True root path bypasses StaticFiles.get_response and goes
# straight to FileResponse, so a get_response override doesn't cover "/".
@app.middleware("http")
async def _no_cache_shell(request, call_next):
    response = await call_next(request)
    path = request.url.path
    p_lower = path.lower()
    if (
        path in ("/", "/index.html", "/service-worker.js", "/manifest.webmanifest")
        or p_lower.endswith(".html")
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# PWA serving is feature-flagged. Set BOPBOP_PWA_ENABLED=1 to mount the
# Svelte build at /. Default is OFF — the PWA is being held until proper
# tests exist (hydration smoke, send round-trip, persistence-survives-
# reload, visual regression). The Signal channel and /api/chat stay
# alive regardless so the agent itself keeps working.
PWA_ENABLED = os.environ.get("BOPBOP_PWA_ENABLED", "0") == "1"

if PWA_ENABLED and BUILD.exists() and (BUILD / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(BUILD), html=True), name="frontend")
else:
    from fastapi.responses import HTMLResponse

    _DISABLED_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BopBop — PWA disabled</title>
<style>
  body{font:15px/1.5 -apple-system,system-ui,sans-serif;
       padding:2rem;max-width:520px;margin:0 auto;color:#181c2a;
       background:linear-gradient(180deg,#a6d9f6,#bfccf6,#f6f3fc);
       min-height:100vh;}
  h1{font-size:1.15rem;margin-top:0}
  code{background:rgba(0,0,0,.06);padding:.1rem .3rem;border-radius:.2rem}
  p{margin:.6rem 0}
</style>
</head><body>
<h1>BopBop PWA — paused</h1>
<p>Frontend disabled until proper tests are in place.</p>
<p>Signal channel still works — message your number as usual.</p>
<p>API still works — <code>POST /api/chat</code> with JSON
   <code>{"message":"…"}</code>.</p>
</body></html>
"""

    @app.get("/", response_class=HTMLResponse)
    def root():
        return HTMLResponse(_DISABLED_HTML)
