#!/usr/bin/env python3
"""A stand-in for the Claude Code launcher, so the skill can be driven end to end.

It records how it was invoked and then does whatever the test told it to do. That
first half is the point: most of what local_agent.py decides ends up encoded in the
command line it builds -- the permission mode, the deny list, whether a session is
resumed -- and none of it is observable any other way.

Driven by three environment variables:

    FAKE_CLAUDE_LOG    JSONL file; one record appended per invocation
    FAKE_CLAUDE_PLAN   JSON list of per-invocation instructions (see below)
    FAKE_CLAUDE_STATE  counter file, so successive calls read successive plan steps

A plan step is a dict, all keys optional:

    {"write": {"path": "x.txt", "content": "..."},   create a file
     "result": "text the agent reports back",
     "session_id": "sess-1",
     "exit": 0}
"""
import json
import os
import sys
from pathlib import Path


def load_plan():
    plan_file = os.environ.get("FAKE_CLAUDE_PLAN")
    if not plan_file or not Path(plan_file).is_file():
        return []
    return json.loads(Path(plan_file).read_text(encoding="utf-8"))


def next_index():
    state = Path(os.environ.get("FAKE_CLAUDE_STATE", "fake_claude_state"))
    n = 0
    if state.is_file():
        try:
            n = int(state.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            n = 0
    state.write_text(str(n + 1), encoding="utf-8")
    return n


def parse_argv(argv):
    """Pull out the flags the tests assert on. Everything is kept in `raw` anyway."""
    out = {"raw": argv, "add_dir": [], "disallowed": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-p" and i + 1 < len(argv):
            out["prompt"] = argv[i + 1]
            i += 2
        elif a == "--model" and i + 1 < len(argv):
            out["model"] = argv[i + 1]
            i += 2
        elif a == "--permission-mode" and i + 1 < len(argv):
            out["permission_mode"] = argv[i + 1]
            i += 2
        elif a == "--resume" and i + 1 < len(argv):
            out["resume"] = argv[i + 1]
            i += 2
        elif a == "--add-dir" and i + 1 < len(argv):
            out["add_dir"].append(argv[i + 1])
            i += 2
        elif a == "--disallowedTools":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                out["disallowed"].append(argv[i])
                i += 1
        else:
            i += 1
    return out


def main():
    argv = sys.argv[1:]
    record = parse_argv(argv)
    record["cwd"] = os.getcwd()
    record["env"] = {k: os.environ.get(k) for k in
                     ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                      "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR")}

    step = {}
    plan = load_plan()
    idx = next_index()
    if idx < len(plan):
        step = plan[idx]
    record["step"] = idx

    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    write = step.get("write")
    if write:
        p = Path(write["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(write.get("content", ""), encoding="utf-8")

    print(json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": step.get("result", "did the thing"),
        "session_id": step.get("session_id", "sess-default"),
    }))
    return int(step.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
