#!/usr/bin/env python3
"""Fixtures that let the skill be exercised for real: a fake Ollama, a fake launcher.

The scripts are driven as subprocesses against these, rather than by monkeypatching
their internals, because the defects that actually shipped lived in the seams --
the command line built for the child, the exit code returned to the caller, the
encoding of the stream it prints to. None of those are visible from inside.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "skills" / "local-delegate" / "scripts"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                    # keep the test output readable

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        state = self.server.state
        if self.path == "/api/tags":
            self._send({"models": [{"model": m} for m in state["installed"]]})
        elif self.path == "/api/ps":
            self._send({"models": state["loaded"]})
        else:
            self._send({}, 404)

    def do_POST(self):
        state = self.server.state
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        state["calls"].append({"path": self.path, "payload": payload})
        if self.path == "/api/generate":
            keep = payload.get("keep_alive")
            if keep == 0:                       # an unload request
                state["loaded"] = [m for m in state["loaded"]
                                   if (m.get("model") or m.get("name")) != payload.get("model")]
            self._send({"response": "ok", "done": True})
        else:
            self._send({})


class FakeOllama:
    """A stand-in Ollama, on a real socket. Point OLLAMA_URL at `url`."""

    def __init__(self, installed=(), loaded=()):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.state = {"installed": list(installed),
                             "loaded": [dict(m) for m in loaded],
                             "calls": []}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def calls(self):
        return self.server.state["calls"]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def fake_claude_command():
    """The launcher override that makes the fake agent the one that runs.

    Deliberately not a .bat/.cmd shim on PATH: cmd.exe truncates every argument at its
    first newline, so a shim would silently drop the project rules and the test would
    be measuring the shim rather than the skill. Naming the interpreter and the script
    is also the shape a real npm install has (`node .../cli.js`), so this exercises the
    multi-word launcher path at the same time.
    """
    return f'"{sys.executable}" "{HERE / "fake_claude.py"}"'


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def make_repo(path):
    """A git repository with one commit, so the clean-tree rule has something to see."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "test@example.com", cwd=path)
    git("config", "user.name", "test", cwd=path)
    git("config", "commit.gpgsign", "false", cwd=path)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=path)
    git("commit", "-qm", "seed", cwd=path)
    return path


class Session:
    """One test's worth of environment: a repo, a fake Ollama, a fake launcher."""

    def __init__(self, tmp, installed=("local-code", "local-text", "local-tiny")):
        self.tmp = Path(tmp)
        self.repo = make_repo(self.tmp / "repo")
        self.log = self.tmp / "claude-calls.jsonl"
        self.plan_file = self.tmp / "plan.json"
        self.state_file = self.tmp / "fake-state"
        self.ollama = FakeOllama(installed=installed)
        self._installed = installed

    def __enter__(self):
        self.ollama.__enter__()
        return self

    def __exit__(self, *exc):
        self.ollama.__exit__(*exc)

    def plan(self, steps):
        self.plan_file.write_text(json.dumps(steps), encoding="utf-8")

    def env(self, **extra):
        env = dict(os.environ)
        env["LOCAL_AGENT_CLAUDE_BIN"] = fake_claude_command()
        env["OLLAMA_URL"] = self.ollama.url
        env["FAKE_CLAUDE_LOG"] = str(self.log)
        env["FAKE_CLAUDE_PLAN"] = str(self.plan_file)
        env["FAKE_CLAUDE_STATE"] = str(self.state_file)
        env["PYTHONIOENCODING"] = ""            # exercise the real default encoding
        env.pop("PYTHONIOENCODING")
        env.update(extra)
        return env

    def run(self, *args, **extra_env):
        """Invoke local_agent.py the way the skill tells you to."""
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "local_agent.py"), *args],
            cwd=self.repo, env=self.env(**extra_env),
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def calls(self):
        if not self.log.is_file():
            return []
        return [json.loads(l) for l in
                self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def git(self, *args):
        return git(*args, cwd=self.repo)
