#!/usr/bin/env python3
"""Run Claude Code against a local Ollama model, inside the current repository.

No API key, no subscription tokens: Ollama speaks the Anthropic Messages API, so
Claude Code itself becomes the local agent, with all its normal tools.

    python local_agent.py run  [--ro] <profile> "<task>" [--verify "<cmd>"]
    python local_agent.py queue <profile> "<task>" [--verify "<cmd>"]
    python local_agent.py flush                            run the queue, grouped
    python local_agent.py list                             show the queue
    python local_agent.py warm <profile>                   preload a model
    python local_agent.py unload                           free the VRAM
    python local_agent.py ps                               what is loaded now
    python local_agent.py gitignore                        ignore machine-only files

Profiles are defined by the setup; see SKILL.md.
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
import runtime  # noqa: E402

runtime.fix_console()

# Heaviest model first. Coding runs while the card is entirely its own, and each
# model loads once per flush instead of once per task.
ORDER = ("code", "fast", "text", "tiny", "vision")

WORK_DIR = Path(os.environ.get("LOCAL_AGENT_DIR", ".local-delegate"))
QUEUE = WORK_DIR / "queue.jsonl"
SESSIONS = WORK_DIR / "sessions.json"

# How many times a task may be re-sent to the local model when its verification
# command fails. Local tokens are free; an expensive-model round trip is not.
DEFAULT_ATTEMPTS = 3

# Model residency: keep it loaded between tasks. Reloading a large coder costs about
# twenty seconds, which dominates a working session where tasks arrive a few at a
# time. The VRAM rule says never load a second model — it does not say to evict the
# first one the moment it becomes useful.
KEEP_ALIVE = os.environ.get("LOCAL_AGENT_KEEP_ALIVE", "30m")

# Denied at every level. Claude Code evaluates deny rules before any permission mode
# ("deny, then ask, then allow"), so these hold even under bypassPermissions, and a
# deny rule cannot carry exceptions.
#
# Be clear about what this is: a net, not a cage. Claude Code matches Bash rules by
# command prefix, and its file rules explicitly do not reach "arbitrary subprocesses
# that read or write files indirectly". The agent may run `python`, so a determined
# one walks straight past every rule below. This list stops a confused model from
# doing something irreversible; only an OS-level sandbox stops a malicious one.
#
# Two rationales, worth keeping distinct:
#   - destroys attributability: rm, and the git verbs that rewrite the working tree.
#     The whole safety story is "clean tree, so the diff is the agent's and
#     `git checkout .` undoes it". An agent that can stash or reset erases both the
#     work and the evidence.
#   - reaches outside the tree: push, sudo, network. Nothing here should be visible
#     off this machine.
DENY_TOOLS = [
    "Bash(rm *)", "Bash(rmdir *)", "Bash(del *)", "Bash(rd *)",
    "Bash(sudo *)", "Bash(su *)", "Bash(shutdown *)", "Bash(reboot *)",
    "Bash(git commit *)", "Bash(git push *)", "Bash(git reset *)",
    "Bash(git checkout *)", "Bash(git restore *)", "Bash(git clean *)",
    "Bash(git rebase *)", "Bash(git merge *)", "Bash(git branch *)",
    "Bash(git tag *)", "Bash(git stash *)",
    "Bash(pip install *)", "Bash(pip3 install *)", "Bash(npm install *)",
    "Bash(npm i *)", "Bash(yarn add *)", "Bash(pnpm add *)", "Bash(cargo add *)",
    "Bash(go install *)", "Bash(apt *)", "Bash(brew *)", "Bash(choco *)",
    "Bash(curl *)", "Bash(wget *)", "Bash(ssh *)", "Bash(scp *)",
    "WebFetch", "WebSearch",
]

# The one carve-out, off by default. These block legitimate work — a task that adds a
# dependency and then has to make `pytest` pass fails at the install step, and the
# retry loop spends all three attempts on a failure that is not the model's fault.
# Whether an agent may touch the lockfile is a project decision, not a global one, so
# it is opt-in per run (--allow-installs) or per repository (allow_installs in
# .local-delegate/project.md's front matter is not read; use the flag or the env var).
INSTALL_TOOLS = {
    "Bash(pip install *)", "Bash(pip3 install *)", "Bash(npm install *)",
    "Bash(npm i *)", "Bash(yarn add *)", "Bash(pnpm add *)", "Bash(cargo add *)",
    "Bash(go install *)",
}
ALLOW_INSTALLS = os.environ.get("LOCAL_AGENT_ALLOW_INSTALLS", "").lower() in ("1", "true", "yes")

GUARDRAILS = """You are running on a local model on this machine. Two consequences,
and they change how you should work:

YOUR TOKENS ARE FREE. Nobody is paying for your output. Reading a file is always
cheaper than guessing what is in it. Read widely, re-read your own work, try things,
check them. Taking forty turns to return correct code is a win; taking five turns to
return a plausible guess is a loss, because a human then pays to find the mistake.

YOU CAN RUN COMMANDS. Inside this working directory you have the same authority the
operator granted the session that called you. Use it. Before you call any library
function, class or attribute you have not seen in this repository with your own eyes,
verify it: grep the repository for an existing use, or run the interpreter and check.
Inventing an API that looks right is the single most expensive mistake you can make
here, because it survives type checks and lint and only fails at runtime.

Rules:
1. Do only what was asked. Do not redesign anything on your own initiative.
2. Read files before editing them. Never rewrite one from memory.
3. If a verification command was given, the task is not finished until that command
   exits 0. Run it. If it fails, read the error, fix the cause and run it again.
4. If the task is ambiguous or needs an architectural decision, STOP and write down
   what is missing. Do not guess your way past it.
5. Finish with a short summary: files touched, what changed, what you could NOT do,
   and whether the verification command passed. Say so plainly if it did not.
6. Never commit, push, install packages or touch anything outside this directory."""


def git(*args):
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True)
        return p.returncode, p.stdout.strip()
    except OSError:
        return 1, ""


def in_git_repo():
    return git("rev-parse", "--git-dir")[0] == 0


# --- git ignore rules -------------------------------------------------------
def ignore_entries():
    base = WORK_DIR.as_posix().rstrip("/")
    return [f"{base}/{e}" for e in cfgmod.MACHINE_ONLY]


def gitignore_status():
    if not in_git_repo():
        return False, True
    gi = Path(".gitignore")
    if not gi.exists():
        return True, False
    try:
        lines = {l.strip() for l in gi.read_text(encoding="utf-8", errors="replace").splitlines()}
    except OSError:
        return True, False
    base = WORK_DIR.as_posix().rstrip("/")
    if any(p in lines for p in (base, f"{base}/", f"/{base}/")):
        return True, True
    return True, all(e in lines for e in ignore_entries())


def write_gitignore():
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


# --- preconditions ----------------------------------------------------------
class Refused(Exception):
    """A run could not start. Always surfaces as a non-zero exit: a caller that
    checks the status — a script, a CI step, an agent running this in the background —
    must not read a refusal as success."""


def require_clean_tree(baseline=None):
    """Refuse to start on modified tracked files, so the resulting diff is entirely
    the local agent's and can be discarded with one command.

    With a baseline commit recorded, a second pass over the agent's own uncommitted
    work is allowed: the diff stays attributable to that baseline. Without this, the
    only way to iterate on a defective delegation is to commit it first, which is the
    worst of the available options.
    """
    if not in_git_repo():
        return
    code, tracked = git("status", "--porcelain", "--untracked-files=no")
    if code != 0 or not tracked:
        return
    if baseline:
        print(f"note: working tree has changes since {baseline[:8]} — treating them as "
              f"this delegation's own work and continuing.")
        return
    raise Refused(
        "Tracked files have uncommitted changes. Commit or stash first, so every line\n"
        "of the resulting diff is attributable to the local agent and can be thrown\n"
        "away with a single `git checkout .`.\n"
        "To iterate on a delegation you have already started, pass --baseline <commit>.\n\n"
        + tracked)


def require_model(profile):
    name = f"local-{profile}"
    present = set(oa.installed_models())
    if name not in present and f"{name}:latest" not in present:
        raise Refused(f"Model {name!r} is missing. Run the setup "
                      f"(see references/setup.md) before delegating.")
    return name


# --- project rules ----------------------------------------------------------
RULE_FILES = ("AGENTS.md", "CLAUDE.md", ".local-delegate/project.md")
# The rules ride in the prompt, which rides in an argv. Windows caps a command line at
# 32767 characters, so this has to leave room for the task, the guardrails and the deny
# list. 8000 characters is a long CLAUDE.md and still leaves three quarters of the
# budget free.
MAX_RULES_CHARS = 8000


def project_rules():
    """Load the repository's own instructions for the local agent.

    Claude reads these automatically; the child does not, because `claude -p` in
    another process starts from nothing. Rules that must be remembered and pasted by
    the caller are rules that will eventually be forgotten, and every convention the
    project cares about would then be silently ignored by exactly the agent writing
    the code.
    """
    chunks = []
    for rel in RULE_FILES:
        p = Path(rel)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            chunks.append(f"--- {rel} ---\n{text}")
    if not chunks:
        return ""
    joined = "\n\n".join(chunks)
    if len(joined) > MAX_RULES_CHARS:
        joined = joined[:MAX_RULES_CHARS] + "\n[...truncated...]"
    return ("\n\nPROJECT RULES — these come from this repository and are binding.\n"
            "Follow them exactly, including any conventions about wording, units or "
            "how values must be represented.\n\n" + joined)


# --- sessions ---------------------------------------------------------------
def load_sessions():
    if not SESSIONS.exists():
        return {}
    try:
        return json.loads(SESSIONS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_session(profile, session_id):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    s = load_sessions()
    s[profile] = session_id
    try:
        SESSIONS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


# --- running ----------------------------------------------------------------
def readonly_outside(add_dirs):
    """Deny rules that make an extra directory readable but not writable.

    `--add-dir` does not mean read-only: the docs are explicit that files in
    additional directories "follow the same permission rules as the original working
    directory", so under bypassPermissions the agent could edit them. The rule is that
    the agent may *read* outside the repository — a library source, a config that
    settles a question — and never write there, so the write has to be denied
    explicitly. `Edit(...)` is the rule Claude Code consults for every file-modifying
    tool; a `Write(...)` path rule is accepted and then never checked.
    """
    rules = []
    for d in add_dirs or []:
        # `//` anchors at the filesystem root; Windows paths are matched in POSIX form.
        posix = Path(d).resolve().as_posix()
        if len(posix) > 1 and posix[1] == ":":          # C:/x -> /c/x
            posix = "/" + posix[0].lower() + posix[2:]
        rules.append(f"Edit(//{posix.lstrip('/')}/**)")
    return rules


def warn_if_oversized(cmd, claude_bin):
    """Say so when the prompt is close to what the platform can carry in an argv.

    The prompt is not small — task plus every project rule — and Windows caps a
    command line at 32767 characters, or 8191 when the launcher is a .cmd/.bat shim
    and the call therefore goes through cmd.exe. That shim also truncates any argument
    at its first newline, which silently drops every project rule while looking like a
    normal run. npm-style installs put exactly such a shim on PATH, so this is worth
    naming rather than discovering from a mysteriously ignored convention.
    """
    if os.name != "nt":
        return
    shim = str(claude_bin).lower().endswith((".cmd", ".bat"))
    size = sum(len(str(a)) + 3 for a in cmd)
    limit = 8191 if shim else 32767
    if shim:
        print("  ⚠ the launcher is a .cmd/.bat shim, which truncates arguments at the "
              "first newline: project rules and multi-line tasks may not reach the "
              "agent. Prefer claude.exe on PATH.", file=sys.stderr)
    if size > limit * 0.9:
        print(f"  ⚠ the command line is {size} characters against a {limit} limit; "
              f"trim the project rules if the agent behaves as though it never saw "
              f"the task.", file=sys.stderr)


def build_command(claude_cmd, profile, model, task, read_only, resume_id, add_dirs,
                  allow_installs=False):
    cmd = [*claude_cmd, "-p", task, "--model", model, "--output-format", "json"]
    if read_only:
        # Reading and read-only shell commands, no edits.
        cmd += ["--permission-mode", "plan"]
    else:
        # Full authority inside the working directory, no prompts — nobody is there
        # to answer one. The deny list above still applies: Claude Code evaluates deny
        # rules before any mode, so commits, installs and network calls stay blocked.
        cmd += ["--permission-mode", "bypassPermissions"]
    # Without this the local agent is told nothing about how it is expected to work:
    # that its tokens are free, that it must run its verification command, and that it
    # must never call a library API it has not seen with its own eyes.
    cmd += ["--append-system-prompt", GUARDRAILS]
    deny = [d for d in DENY_TOOLS if not (allow_installs and d in INSTALL_TOOLS)]
    cmd += ["--disallowedTools", *deny, *readonly_outside(add_dirs)]
    for d in add_dirs or []:
        cmd += ["--add-dir", d]
    if resume_id:
        cmd += ["--resume", resume_id]
    return cmd


def parse_result(stdout):
    """Claude Code's json output carries the session id and the final text."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None, stdout
    if isinstance(data, list):
        data = data[-1] if data else {}
    text = data.get("result") or data.get("text") or ""
    return data.get("session_id"), text or stdout


def run_verify(command):
    print(f"  ↻ verifying: {command}")
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run the verification command: {e}"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, out[-4000:]


def run_one(profile, task, read_only=False, verify=None, attempts=DEFAULT_ATTEMPTS,
            baseline=None, add_dirs=None, fresh=False, allow_installs=False,
            check_tree=True):
    if not oa.is_up():
        raise Refused(f"Ollama is not answering on {oa.BASE}. Start it, then retry.")
    claude_cmd = runtime.claude_command()
    if not claude_cmd:
        raise Refused(
            "Could not find the Claude Code launcher. It drives the local model, so\n"
            "nothing can run without it. Looked on PATH and in the usual install\n"
            "directories, including the desktop app's versioned folder.\n"
            f"If it lives somewhere else, set {runtime.CLAUDE_BIN_ENV} to the command\n"
            "that starts it, quoted as you would in a shell.")
    model = require_model(profile)
    if not read_only and check_tree:
        require_clean_tree(baseline)

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = WORK_DIR / f"{stamp}-{profile}.log"

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env["ANTHROPIC_BASE_URL"] = oa.BASE
    env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
    env["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude-local")
    env["PYTHONIOENCODING"] = "utf-8"

    resume_id = None if (fresh or read_only) else load_sessions().get(profile)
    rules = project_rules()
    transcript = []
    ok = False
    detail = ""

    for attempt in range(1, max(1, attempts) + 1):
        prompt = task
        if attempt == 1:
            prompt = task + rules
            if verify:
                prompt += (f"\n\nVERIFICATION: this task is only finished when "
                           f"`{verify}` exits 0. Run it yourself and fix what it "
                           f"reports until it passes.")
        else:
            prompt = (f"The previous attempt did not pass verification.\n\n"
                      f"Command: {verify}\nOutput:\n{detail}\n\n"
                      f"Read the error, find the cause in the files you changed, fix "
                      f"it, and run the command again until it exits 0. Original "
                      f"task, for reference:\n{task}")

        cmd = build_command(claude_cmd, profile, model, prompt, read_only,
                            resume_id, add_dirs, allow_installs)
        warn_if_oversized(cmd, claude_cmd[0])
        mode = "read-only" if read_only else "read-write"
        suffix = f", attempt {attempt}/{attempts}" if verify and attempts > 1 else ""
        print(f"▶ {profile} → {model} ({mode}{suffix})")

        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        except OSError as e:
            raise Refused(f"could not start the local agent: {e}")

        sid, text = parse_result(proc.stdout)
        if sid and not read_only:
            save_session(profile, sid)
            resume_id = sid
        transcript.append(text)
        print(text)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)

        if not verify:
            ok = proc.returncode == 0
            break
        ok, detail = run_verify(verify)
        if ok:
            print("  ✓ verification passed")
            break
        print(f"  ✗ verification failed{' — retrying locally' if attempt < attempts else ''}")

    log.write_text("\n\n".join(transcript), encoding="utf-8")
    print(f"  log: {log}")
    if verify and not ok:
        print(f"  ⚠ {profile}: verification never passed after {attempts} attempts. "
              f"The work is NOT confirmed — read the diff before trusting it.",
              file=sys.stderr)
    return 0 if ok else 1


# --- queue ------------------------------------------------------------------
def enqueue(profile, task, verify=None):
    if profile not in ORDER:
        raise Refused(f"unknown profile {profile!r} — pick from {', '.join(ORDER)}")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    item = {"profile": profile, "task": task}
    if verify:
        item["verify"] = verify
    with QUEUE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"queued [{profile}]: {task}")
    return 0


def read_queue():
    if not QUEUE.exists():
        return []
    items = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items


def show_queue():
    items = read_queue()
    if not items:
        print("queue is empty")
        return 0
    for p in ORDER:
        for it in items:
            if it["profile"] == p:
                v = f"   [verify: {it['verify']}]" if it.get("verify") else ""
                print(f"  [{p}] {it['task']}{v}")
    return 0


def flush(attempts=DEFAULT_ATTEMPTS, baseline=None, allow_installs=False):
    items = read_queue()
    if not items:
        print("queue is empty, nothing to do")
        return 0
    if not oa.is_up():
        raise Refused(f"Ollama is not answering on {oa.BASE}. Start it, then retry.")

    # One check for the whole batch, before anything is loaded or evicted. It cannot be
    # per task: the first task in a flush legitimately dirties the tree, so re-checking
    # would refuse every task after it — which is exactly the two-task batch the skill's
    # worked example tells you to queue.
    require_clean_tree(baseline)

    failures, current = 0, None
    ran, kept = [], []
    for p in ORDER:
        group = [it for it in items if it["profile"] == p]
        if not group:
            continue
        print(f"\n══ group {p} — {len(group)} task(s) ══")
        # Evict only when switching to a different model. The previous group's model
        # is the only thing that needs to leave the card.
        if current and current != p:
            for name in oa.unload_all():
                print(f"  ↓ evicted {name}")
        current = p
        for it in group:
            try:
                if run_one(p, it["task"], verify=it.get("verify"), attempts=attempts,
                           baseline=baseline, allow_installs=allow_installs,
                           check_tree=False) != 0:
                    failures += 1
                ran.append(it)
            except Refused as e:
                # It never started, so it touched nothing: it goes back in the queue and
                # can be retried as it stands. A task whose agent *did* run does not,
                # even on a non-zero exit — it may have edited files, and running it
                # again would apply those edits twice.
                failures += 1
                kept.append(it)
                print(f"⚠ could not start, left in the queue: {e}", file=sys.stderr)

    if ran:
        done = QUEUE.with_suffix(f".{datetime.now():%Y%m%d-%H%M%S}.done")
        write_rows(done, ran)
        print(f"\n{len(ran)} task(s) ran (archived to {done.name})")
    if kept:
        write_rows(QUEUE, kept)
        print(f"{len(kept)} task(s) never started and are still queued — "
              f"fix the cause above, then run `flush` again")
    else:
        QUEUE.unlink(missing_ok=True)
    if current and ran:
        # Claude Code's requests carry no keep_alive, so Ollama has just reset this
        # model to its 5-minute default. One empty generate re-arms the long residency
        # that makes the next delegation start immediately instead of reloading.
        try:
            oa.generate(f"local-{current}", "", keep_alive=KEEP_ALIVE)
            print(f"The {current} model stays loaded for {KEEP_ALIVE} — "
                  f"`unload` frees the card when you want it.")
        except Exception:
            pass
    show_changes()
    return 1 if failures else 0


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def show_changes():
    """Show what the delegation actually produced.

    Plain `git diff` covers tracked files only, so a newly created module — the most
    common shape of a delegated coding task — appears nowhere, leaving the review step
    blind exactly where it is most needed. Intent-to-add fixes that without staging
    any content.
    """
    if not in_git_repo():
        return
    code, untracked = git("ls-files", "--others", "--exclude-standard")
    new_files = [f for f in untracked.splitlines() if f.strip()]
    for f in new_files:
        git("add", "-N", f)
    print("\n── changes ──")
    print(git("--no-pager", "diff", "--stat")[1] or "(no changes to tracked files)")
    if new_files:
        print(f"\n── {len(new_files)} new file(s), included above ──")
        for f in new_files:
            print(f"  + {f}")


# --- dispatch ---------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--verify", metavar="CMD",
                       help="command that must exit 0; the agent retries locally until it does")
        p.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                       help=f"local retries when verification fails (default {DEFAULT_ATTEMPTS})")

    def installs(p):
        p.add_argument("--allow-installs", action="store_true", dest="allow_installs",
                       help="let the agent install dependencies, so a task that adds "
                            "one can still make its verification command pass")

    r = sub.add_parser("run", help="run one task now")
    r.add_argument("--ro", action="store_true", help="read-only: analyse, do not edit")
    r.add_argument("--fresh", action="store_true", help="ignore the stored session")
    r.add_argument("--baseline", metavar="COMMIT",
                   help="allow iterating on uncommitted work attributable to this commit")
    r.add_argument("--add-dir", action="append", metavar="PATH",
                   help="extra directory the agent may read (repeatable)")
    r.add_argument("profile", choices=ORDER)
    r.add_argument("task")
    common(r)
    installs(r)

    q = sub.add_parser("queue", help="enqueue a task without running it")
    q.add_argument("profile", choices=ORDER)
    q.add_argument("task")
    q.add_argument("--verify", metavar="CMD")

    f = sub.add_parser("flush", help="run the whole queue, grouped by model")
    f.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    f.add_argument("--baseline", metavar="COMMIT")
    installs(f)

    sub.add_parser("list", help="show the queue")
    w = sub.add_parser("warm", help="preload a model")
    w.add_argument("profile", choices=ORDER)
    sub.add_parser("unload", help="evict everything from VRAM")
    sub.add_parser("ps", help="what is loaded right now")
    sub.add_parser("gitignore", help="add the machine-only paths to .gitignore")

    a = ap.parse_args()

    if a.cmd == "run":
        return run_one(a.profile, a.task, read_only=a.ro, verify=a.verify,
                       attempts=a.attempts, baseline=a.baseline,
                       add_dirs=a.add_dir, fresh=a.fresh,
                       allow_installs=a.allow_installs or ALLOW_INSTALLS)
    if a.cmd == "queue":
        return enqueue(a.profile, a.task, a.verify)
    if a.cmd == "flush":
        return flush(a.attempts, a.baseline, a.allow_installs or ALLOW_INSTALLS)
    if a.cmd == "list":
        return show_queue()
    if a.cmd == "warm":
        name = require_model(a.profile)
        print(f"  ↑ preloading {name}")
        oa.generate(name, "", keep_alive=KEEP_ALIVE)
        return 0
    if a.cmd == "unload":
        for n in oa.unload_all():
            print(f"  ↓ evicted {n}")
        return 0
    if a.cmd == "ps":
        loaded = oa.loaded_models()
        print("\n".join((m.get("name") or m.get("model")) for m in loaded)
              or "(nothing loaded)")
        return 0
    if a.cmd == "gitignore":
        return write_gitignore()
    return 0


if __name__ == "__main__":
    try:
        code = main() or 0
    except Refused as e:
        print(str(e), file=sys.stderr)
        code = 2                      # a refusal is never a success
    except BrokenPipeError:
        code = 0
    runtime.quiet_broken_pipe()
    sys.exit(code)
