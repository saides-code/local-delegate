#!/usr/bin/env python3
"""Run Claude Code against a local Ollama model, inside the current repo.

No API key, no subscription tokens: Ollama speaks the Anthropic Messages API,
so Claude Code itself becomes the local agent, with all its normal tools.

    python local_agent.py run  [--ro] <profile> "<task>"   run it now
    python local_agent.py queue <profile> "<task>"         enqueue, run later
    python local_agent.py flush                            run the queue, grouped
    python local_agent.py list                             show the queue
    python local_agent.py warm <profile>                   preload a model
    python local_agent.py unload                           free the VRAM
    python local_agent.py ps                               what is loaded now

Profiles: code, fast, text, tiny — see SKILL.md for what each is for.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ollama_api as oa  # noqa: E402
import config as cfgmod  # noqa: E402

# Heaviest model first. Coding runs while the card is entirely its own, and each
# model is loaded exactly once per flush instead of once per task.
ORDER = ("code", "fast", "text", "tiny")
MAX_TURNS = {"code": 40, "fast": 30, "text": 10, "tiny": 6}

WORK_DIR = Path(os.environ.get("LOCAL_AGENT_DIR", ".local-delegate"))
QUEUE = WORK_DIR / "queue.jsonl"

READ_TOOLS = ["Read", "Grep", "Glob", "Bash(git diff*)", "Bash(git status*)",
              "Bash(ls*)", "Bash(cat*)"]
WRITE_TOOLS = ["Edit", "Write", "Bash(npm test*)", "Bash(npm run*)", "Bash(pytest*)",
               "Bash(python*)", "Bash(node*)", "Bash(go test*)", "Bash(cargo*)"]
DENY_TOOLS = ["Bash(rm*)", "Bash(git commit*)", "Bash(git push*)", "Bash(sudo*)",
              "WebFetch", "WebSearch"]

GUARDRAILS = """You are a local agent with a limited context budget. Non-negotiable rules:
1. Do only what was asked. Do not redesign anything on your own initiative.
2. Read files before editing them. Never rewrite one from memory.
3. If the task is ambiguous or needs an architectural decision, STOP and write down
   what is missing instead of guessing.
4. Finish with a five-line summary: files touched, what changed, what you could NOT do.
5. No commits, no pushes, no destructive commands."""


def git(*args, cwd=None):
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
        return p.returncode, p.stdout.strip()
    except OSError:
        return 1, ""


def in_git_repo():
    return git("rev-parse", "--git-dir")[0] == 0


def ignore_entries():
    """Paths under the skill's folder that belong to this machine only."""
    base = WORK_DIR.as_posix().rstrip("/")
    return [f"{base}/{e}" for e in cfgmod.MACHINE_ONLY]


def gitignore_status():
    """(is_git_repo, already_ignored). Everything git-related is skipped when the
    project is not a repository — there is nothing to ignore into."""
    if not in_git_repo():
        return False, True
    entries = ignore_entries()
    gi = Path(".gitignore")
    if not gi.exists():
        return True, False
    try:
        lines = {l.strip() for l in gi.read_text(encoding="utf-8", errors="replace").splitlines()}
    except OSError:
        return True, False
    base = WORK_DIR.as_posix().rstrip("/")
    if any(p in lines for p in (base, f"{base}/", f"/{base}/")):
        return True, True          # a blanket ignore of the folder: the user's call
    return True, all(e in lines for e in entries)


def write_gitignore():
    """Add the machine-only paths to .gitignore.

    One mechanism, one visible file. project.md stays tracked: it records what to
    delegate in this project and is the part worth carrying to another machine.
    """
    is_repo, done = gitignore_status()
    if not is_repo:
        print("not a git repository — nothing to ignore")
        return 0
    if done:
        print(".gitignore already covers the machine-only files")
        return 0
    gi = Path(".gitignore")
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    lines = {l.strip() for l in existing.splitlines()}
    missing = [e for e in ignore_entries() if e not in lines]
    block = ["", "# local-delegate: specific to this machine.",
             "# project.md is NOT ignored — it records what to delegate in this project",
             "# and is meant to travel with the repo."] + missing
    with gi.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n".join(block) + "\n")
    print(f"added {len(missing)} line(s) to .gitignore")
    return 0


def require_clean_tree():
    """Refuse to start on modified tracked files.

    The point is that `git diff` afterwards contains only the local agent's work and
    can be discarded with `git checkout .`. Untracked files do not affect that diff,
    so they only earn a warning — blocking on them would stop delegation over an
    unrelated scratch file, or over this skill's own project.md before it is committed.
    """
    if not in_git_repo():
        return
    code, tracked = git("status", "--porcelain", "--untracked-files=no")
    if code == 0 and tracked:
        raise SystemExit(
            "Tracked files have uncommitted changes. Commit or stash first, so that\n"
            "every line of the resulting diff is attributable to the local agent and\n"
            "can be thrown away with a single `git checkout .`.\n\n"
            + tracked)
    code, all_out = git("status", "--porcelain")
    if code == 0:
        untracked = [l for l in all_out.splitlines() if l.startswith("??")]
        if untracked:
            print(f"note: {len(untracked)} untracked file(s) present; they are outside "
                  f"the diff, but the agent could overwrite one if it creates a file "
                  f"with the same name.")


def model_for(profile):
    return f"local-{profile}"


def require_model(profile):
    name = model_for(profile)
    present = set(oa.installed_models())
    if name not in present and f"{name}:latest" not in present:
        raise SystemExit(f"Model {name!r} is missing. Run the setup "
                         f"(see references/setup.md) before delegating.")
    return name


def run_one(profile, task, read_only=False):
    if not oa.is_up():
        raise SystemExit(f"Ollama is not answering on {oa.BASE}. Start it, then retry.")
    model = require_model(profile)
    if not read_only:
        require_clean_tree()

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = WORK_DIR / f"{stamp}-{profile}.log"

    tools = list(READ_TOOLS) + ([] if read_only else WRITE_TOOLS)
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env["ANTHROPIC_BASE_URL"] = oa.BASE
    env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
    # A separate config dir: the local agent should not inherit the main session's
    # MCP servers, which would be wasted context on a small model.
    env["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude-local")

    cmd = ["claude", "-p", task, "--model", model,
           "--permission-mode", "plan" if read_only else "acceptEdits",
           "--allowedTools", *tools,
           "--disallowedTools", *DENY_TOOLS,
           "--append-system-prompt", GUARDRAILS,
           "--max-turns", str(MAX_TURNS[profile]),
           "--output-format", "text"]

    mode = "read-only" if read_only else "read-write"
    print(f"▶ {profile} → {model} ({mode}, max {MAX_TURNS[profile]} turns)")
    print(f"  log: {log}")
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("`claude` is not on PATH — Claude Code is what actually "
                         "drives the local model.")
    output = (proc.stdout or "") + (proc.stderr or "")
    log.write_text(output, encoding="utf-8")
    print(output)
    if proc.returncode != 0:
        print(f"⚠ the local agent exited with code {proc.returncode}", file=sys.stderr)
    return proc.returncode


def enqueue(profile, task):
    if profile not in ORDER:
        raise SystemExit(f"unknown profile {profile!r} — pick from {', '.join(ORDER)}")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"profile": profile, "task": task}, ensure_ascii=False) + "\n")
    print(f"queued [{profile}]: {task}")


def read_queue():
    if not QUEUE.exists():
        return []
    items = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items


def show_queue():
    items = read_queue()
    if not items:
        print("queue is empty")
        return
    for p in ORDER:
        for it in items:
            if it["profile"] == p:
                print(f"  [{p}] {it['task']}")


def flush():
    items = read_queue()
    if not items:
        print("queue is empty, nothing to do")
        return 0
    if not oa.is_up():
        raise SystemExit(f"Ollama is not answering on {oa.BASE}. Start it, then retry.")

    failures = 0
    for p in ORDER:
        group = [it for it in items if it["profile"] == p]
        if not group:
            continue
        print(f"\n══ group {p} — {len(group)} task(s) ══")
        # Free first, load second: never two models resident at once.
        evicted = oa.unload_all()
        for name in evicted:
            print(f"  ↓ evicted {name}")
        for it in group:
            try:
                if run_one(p, it["task"]) != 0:
                    failures += 1
            except SystemExit as e:
                failures += 1
                print(f"⚠ task failed, continuing: {e}", file=sys.stderr)

    done = QUEUE.with_suffix(f".{datetime.now():%Y%m%d-%H%M%S}.done")
    QUEUE.rename(done)
    print(f"\nQueue emptied (archived to {done.name})")
    if in_git_repo():
        print("\n── combined diff ──")
        print(git("--no-pager", "diff", "--stat")[1])
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one task now")
    r.add_argument("--ro", action="store_true", help="read-only: analyse, do not edit")
    r.add_argument("profile", choices=ORDER)
    r.add_argument("task")

    q = sub.add_parser("queue", help="enqueue a task without running it")
    q.add_argument("profile", choices=ORDER)
    q.add_argument("task")

    sub.add_parser("flush", help="run the whole queue, grouped by model")
    sub.add_parser("list", help="show the queue")
    w = sub.add_parser("warm", help="preload a model in the background")
    w.add_argument("profile", choices=ORDER)
    sub.add_parser("unload", help="evict everything from VRAM")
    sub.add_parser("gitignore",
                   help="commit the log/queue ignore rules to the repo's .gitignore")
    sub.add_parser("ps", help="what is loaded right now")

    a = ap.parse_args()

    if a.cmd == "run":
        return run_one(a.profile, a.task, read_only=a.ro)
    if a.cmd == "queue":
        return enqueue(a.profile, a.task) or 0
    if a.cmd == "flush":
        return flush()
    if a.cmd == "list":
        return show_queue() or 0
    if a.cmd == "warm":
        name = require_model(a.profile)
        print(f"  ↑ preloading {name}")
        oa.generate(name, "", keep_alive="30m")
        return 0
    if a.cmd == "gitignore":
        return write_gitignore()
    if a.cmd == "unload":
        for n in oa.unload_all():
            print(f"  ↓ evicted {n}")
        return 0
    if a.cmd == "ps":
        loaded = oa.loaded_models()
        print("\n".join((m.get("name") or m.get("model")) for m in loaded) or "(nothing loaded)")
        return 0
    return 0


def _quiet_broken_pipe():
    """`python x.py | head` closes the pipe early; without this Python prints a
    traceback that reads like a crash. Exit quietly instead, as CLI tools do."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":
    try:
        code = main() or 0
    except BrokenPipeError:
        code = 0
    _quiet_broken_pipe()
    sys.exit(code)
