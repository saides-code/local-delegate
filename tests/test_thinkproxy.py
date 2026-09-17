#!/usr/bin/env python3
"""The proxy that disables the local model's thinking.

This is pinned by tests because the alternatives fail *silently*: `think: false` in
the body is accepted and ignored on /v1/messages, and MAX_THINKING_TOKENS=0 has no
effect at all. A regression here would not raise anything -- delegations would simply
become a hundred times slower again, which is how the defect survived a whole release.
"""
import importlib.util
import json
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "local-delegate" / "scripts"
spec = importlib.util.spec_from_file_location("thinkproxy", SCRIPTS / "thinkproxy.py")
thinkproxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thinkproxy)


class _Upstream(BaseHTTPRequestHandler):
    """Stands in for Ollama: records the body it was given, answers trivially."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            self.server.seen.append(json.loads(raw))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.server.seen.append(raw)
        payload = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ThinkProxyTest(unittest.TestCase):
    def setUp(self):
        self.up = HTTPServer(("127.0.0.1", 0), _Upstream)
        self.up.seen = []
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.addCleanup(self.up.server_close)
        self.addCleanup(self.up.shutdown)
        upstream = f"http://127.0.0.1:{self.up.server_address[1]}"
        self.proxy = thinkproxy.ThinkProxy(upstream)
        self.proxy.__enter__()
        self.addCleanup(self.proxy.__exit__, None, None, None)

    def post(self, path, body):
        req = urllib.request.Request(self.proxy.url + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def test_thinking_is_disabled_on_messages(self):
        """The whole point: Claude Code will not send this field, so the proxy adds it."""
        self.post("/v1/messages", {"model": "m", "messages": []})
        self.assertEqual(self.up.seen[-1].get("thinking"), {"type": "disabled"})

    def test_an_explicit_thinking_choice_is_respected(self):
        """Overriding a deliberate request would be the same silent second-guessing
        this proxy exists to undo."""
        asked = {"type": "enabled", "budget_tokens": 1024}
        self.post("/v1/messages", {"model": "m", "messages": [], "thinking": asked})
        self.assertEqual(self.up.seen[-1].get("thinking"), asked)

    def test_other_paths_are_untouched(self):
        self.post("/v1/complete", {"model": "m"})
        self.assertNotIn("thinking", self.up.seen[-1])

    def test_the_body_still_arrives_intact(self):
        self.post("/v1/messages", {"model": "m", "max_tokens": 7, "messages": ["x"]})
        got = self.up.seen[-1]
        self.assertEqual(got["model"], "m")
        self.assertEqual(got["max_tokens"], 7)
        self.assertEqual(got["messages"], ["x"])

    def test_a_response_is_relayed_back(self):
        out = self.post("/v1/messages", {"model": "m", "messages": []})
        self.assertTrue(out["ok"])
        self.assertEqual(out["path"], "/v1/messages")

    def test_unparseable_bodies_are_forwarded_unchanged(self):
        """A request shape the proxy has never seen must still reach the model."""
        req = urllib.request.Request(self.proxy.url + "/v1/messages", data=b"not json",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        self.assertEqual(self.up.seen[-1], b"not json")


if __name__ == "__main__":
    unittest.main()
