"""Shared fake Ollama/DeepSeek HTTP backends for V2 tests (stdlib only)."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))


class FakeState:
    tags = {"models": [{"name": "qwen2.5-coder:0.5b", "digest": "sha256:fake123",
                        "modified_at": "2026-01-01T00:00:00Z"}]}
    chat_content = '{"edits": []}'
    chat_queue: list = []  # when non-empty, one entry served per /api/chat call
    completions_content = "hello-from-api"
    api_key_seen = ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(FakeState.tags)
        elif self.path == "/v1/models":
            self._send({"data": [{"id": m["name"]} for m in FakeState.tags["models"]]})
        else:
            self._send({}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/chat":
            if FakeState.chat_queue:
                content = FakeState.chat_queue.pop(0)
            else:
                content = FakeState.chat_content
            self._send({"message": {"content": content},
                        "prompt_eval_count": 10, "eval_count": 5})
        elif self.path == "/chat/completions":
            FakeState.api_key_seen = self.headers.get("Authorization", "")
            self._send({"choices": [{"message": {"content": FakeState.completions_content}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
        else:
            self._send({}, 404)


class FakeBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def base_url(self):
        return f"http://127.0.0.1:{self.port}"
