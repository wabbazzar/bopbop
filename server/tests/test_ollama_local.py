"""Exercise the JSONL transport and warm history without a model download."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_jsonl_transport_resumes_history(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/api/chat"
            size = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(size))
            requests.append(payload)
            if payload['messages'][-1]['content'] == 'fail':
                self.send_error(500, 'fixture model failure')
                return
            body = json.dumps({"message": {"content": f"reply {len(requests)}"},
                               "done": True, "prompt_eval_count": 7,
                               "eval_count": 2, "eval_duration": 1000000000}).encode() + b"\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).resolve().parents[2] / "bin" / "bopbop-local.py"
    env = {**os.environ, "BOPBOP_OLLAMA_HOST": f"http://127.0.0.1:{server.server_port}",
           "BOPBOP_DATA_DIR": str(tmp_path / "data")}
    try:
        first = subprocess.run([sys.executable, str(script), "--jsonl", "--workspace",
                                str(tmp_path), "hello"], env=env, capture_output=True,
                               text=True, check=True)
        events = [json.loads(line) for line in first.stdout.splitlines()]
        assert [event["kind"] for event in events] == ["session", "model", "text", "done"]
        sid = events[0]["session_id"]
        assert events[2]["delta"] == "reply 1"
        second = subprocess.run([sys.executable, str(script), "--jsonl", "--workspace",
                                 str(tmp_path), "--session-id", sid, "again"], env=env,
                                capture_output=True, text=True, check=True)
        assert json.loads(second.stdout.splitlines()[0])["session_id"] == sid
        assert [message["content"] for message in requests[1]["messages"]
                if message["role"] in {"user", "assistant"}] == ["hello", "reply 1", "again"]
        failed = subprocess.run([sys.executable, str(script), '--jsonl', '--workspace',
                                 str(tmp_path), 'fail'], env=env, capture_output=True,
                                text=True, check=True)
        events = [json.loads(line) for line in failed.stdout.splitlines()]
        assert [event['kind'] for event in events] == ['session', 'error']
        assert 'HTTP Error 500' in events[-1]['message']
        assert not failed.stderr and 'Traceback' not in failed.stdout
        assert not (tmp_path / 'data/ollama_sessions' / (events[0]['session_id'] + '.json')).exists()
    finally:
        server.shutdown()
        server.server_close()
