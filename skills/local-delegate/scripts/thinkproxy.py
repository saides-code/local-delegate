#!/usr/bin/env python3
"""A loopback proxy that turns off the local model's thinking.

Why this exists, and why nothing simpler works. Every current local model family ships
a thinking variant, and Ollama leaves thinking ON unless a request says otherwise. On
the agent path that is not a slowdown, it is a wall: measured on gemma4:12b, one
sixty-word answer cost 153.5 s with thinking against 1.2 s without, and a real
document task streamed 1,091 thinking deltas in seven minutes without writing a byte.

Three levers were tried against a real task, and two of them fail silently:

  * `think: false` in the body -- works on /api/chat, and is *ignored* on /v1/messages,
    which is the endpoint Claude Code uses. It looks like a fix and is not one.
  * `MAX_THINKING_TOKENS=0` in the child's environment -- no effect; the run produced
    3,312 thinking deltas, more than the baseline.
  * `PARAMETER think false` in a Modelfile -- `Error: unknown parameter 'think'`.

What does work is the Anthropic-shaped field on the request itself:
`thinking: {"type": "disabled"}`. Claude Code will not send it, so this sits in front
of Ollama and adds it. The client talks to this; this talks to Ollama.

It is deliberately small: one origin, loopback only, no retries, no caching. Anything
it cannot parse it forwards untouched, so a request shape it has never seen still
reaches the model.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Hop-by-hop headers belong to one connection and must not be relayed onward.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                                  # the caller's output is the interesting one

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _rewrite(self, body):
        """Add the disabled-thinking field to a Messages request.

        Only /v1/messages carries it, and only when the body is JSON we understand.
        An explicit `thinking` already on the request is left alone: if a caller has
        asked for thinking on purpose, overriding it would be the same silent
        second-guessing this proxy exists to undo.
        """
        if "/v1/messages" not in self.path or not body:
            return body
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
        if not isinstance(data, dict) or "thinking" in data:
            return body
        data["thinking"] = {"type": "disabled"}
        return json.dumps(data).encode()

    def _forward(self, method):
        body = self._rewrite(self._body())
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP}
        req = urllib.request.Request(self.server.upstream + self.path,
                                     data=body or None, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=self.server.timeout_s)
        except urllib.error.HTTPError as e:
            resp = e                          # relay the model's own error verbatim
        except OSError as e:
            self.send_error(502, f"upstream unreachable: {e}")
            return

        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        # Length is unknown for a streamed answer, and buffering it would defeat the
        # streaming the client is relying on, so relay it as chunks.
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                              # the client gave up; nothing to salvage
        finally:
            resp.close()

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """Stay quiet when the client simply hangs up.

        The agent closes connections as it finishes, and the default handler prints a
        full traceback for each one. That noise lands in the middle of the delegation's
        output, where it reads like the run has crashed — during the one run that
        actually succeeded, it was the most alarming thing on screen.
        """
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class ThinkProxy:
    """Start with `with ThinkProxy(upstream) as p:` and point the child at `p.url`."""

    def __init__(self, upstream, timeout_s=900):
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.upstream = upstream.rstrip("/")
        self.server.timeout_s = timeout_s
        self.server.daemon_threads = True
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
