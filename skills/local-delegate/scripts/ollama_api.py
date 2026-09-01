"""Tiny Ollama HTTP client. Standard library only, no dependencies.

Why a module instead of curl: every script here has to read numbers out of JSON,
and doing that with grep/awk was the source of two real bugs (a silent awk
redirection, and a regex that broke on whitespace). json.loads has neither
failure mode.
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")


class OllamaDown(Exception):
    """Ollama is not answering on BASE."""


def _call(path, payload=None, timeout=300):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise OllamaDown(f"{url}: {e}") from e
    return json.loads(body) if body.strip() else {}


def is_up():
    try:
        _call("/api/tags", timeout=5)
        return True
    except (OllamaDown, json.JSONDecodeError):
        return False


def installed_models():
    """Every model tag present on this machine."""
    return sorted(m.get("model", "") for m in _call("/api/tags").get("models", []))


def loaded_models():
    """Models currently held in memory, with their VRAM split."""
    return _call("/api/ps").get("models", [])


def generate(model, prompt, keep_alive=None, timeout=300):
    # Default to a long residency: the model that was just loaded is the one the
    # next task will want, and reloading a large coder costs ~20 s.
    keep_alive = keep_alive or os.environ.get("LOCAL_AGENT_KEEP_ALIVE", "30m")
    return _call(
        "/api/generate",
        {"model": model, "prompt": prompt, "stream": False, "keep_alive": keep_alive},
        timeout,
    )


def chat_with_tool(model, message, tool, timeout=300):
    return _call(
        "/api/chat",
        {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": message}],
            "tools": [tool],
        },
        timeout,
    )


def unload(model):
    """keep_alive 0 evicts the model right away.

    Two models resident at once on a 16 GB card means extra offload for both,
    which is exactly the coding slowdown the profiles are meant to avoid.
    """
    try:
        _call("/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
    except OllamaDown:
        pass


def unload_all():
    names = [m.get("name") or m.get("model") for m in loaded_models()]
    for n in filter(None, names):
        unload(n)
    return names
