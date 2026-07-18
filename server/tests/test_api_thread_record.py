"""End-to-end for POST /api/thread/record: an out-of-band push is attributed
to the peer's conversation and then shows up as an unseen push for the next
turn — the exact path notify.sh drives."""

from fastapi.testclient import TestClient

import db
import main


def test_record_creates_conversation_and_is_injectable(tmp_db):
    with TestClient(main.app) as client:
        r = client.post(
            "/api/thread/record",
            json={
                "peer": "+15550001111",
                "text": "rave-pi fan — 1hr check: idle 48C, fan off",
                "source": "notify",
            },
        )
        assert r.status_code == 200, r.text
        cid = r.json()["conversation_id"]

    # Same peer resolves to the same conversation the poll loop would use...
    assert db.get_or_create_conversation_for_peer("signal", "+15550001111") == cid
    # ...and the push is available to the next turn's prompt injection.
    pushes = db.get_unseen_pushes(cid)
    assert any("idle 48C" in p["content"] for p in pushes)
    assert pushes[0]["source"] == "notify"


def test_record_requires_peer_and_text(tmp_db):
    with TestClient(main.app) as client:
        assert client.post("/api/thread/record", json={"peer": "+1", "text": ""}).status_code == 400
        assert client.post("/api/thread/record", json={"peer": "", "text": "x"}).status_code == 400
