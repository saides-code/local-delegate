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

On run and flush:
    --verify "<cmd>"   the task is finished when this exits 0, not when a file is
                       written; the agent retries locally until it does
    --attempts N       how many of those local retries (default 3)
    --allow-installs   let the agent install dependencies, off by default
    --baseline <sha>   iterate on uncommitted work already attributable to that commit

    --think            leave the model's thinking on; off by default, because Ollama
                       enables it and it is measured ~100x slower for the same answer
    --timeout N        seconds before one attempt is killed with its process tree

On run only:
    --ro               read-only: analyse, do not edit
    --fresh            ignore the stored session and start cold
    --add-dir <path>   an extra directory it may read, but not write

Other verbs:
    ps                 what is loaded, and whether a delegation is running
    kill               stop a delegation that outlived its caller
    drop [n|all]       remove a queued task, or clear the queue

Environment:
    LOCAL_AGENT_CLAUDE_BIN     the command that starts Claude Code, if discovery fails
    LOCAL_AGENT_VERIFY_SHELL   `cmd` to run verification in cmd.exe instead of PowerShell
    LOCAL_AGENT_KEEP_ALIVE     how long a model stays resident (default 30m)
    LOCAL_AGENT_ALLOW_INSTALLS truthy to allow installs without the flag
    LOCAL_AGENT_TIMEOUT        seconds before an attempt is killed (default 1800)
    OLLAMA_URL                 where Ollama answers (default http://localhost:11434)

Exit codes: 0 done, 1 the task ran and failed, 2 refused before anything started.
Profiles are defined by the setup; see SKILL.md.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ollama_api as oa  # noqa: E402
import config as cfgmod  # noqa: E402
import runtime  # noqa: E402
import thinkproxy  # noqa: E402

runtime.fix_console()

# Heaviest model first. Coding runs while the card is entirely its own, and each
# model loads once per flush instead of once per task.
ORDER = ("code", "fast", "text", "tiny", "vision")

WORK_DIR = Path(os.environ.get("LOCAL_AGENT_DIR", ".local-delegate"))
QUEUE = WORK_DIR / "queue.jsonl"
SESSIONS = WORK_DIR / "sessions.json"
RUNNING = WORK_DIR / "running.json"

# A ceiling on one attempt, because there was none and the consequence was measured:
# a delegation that stalled outlived the session that started it by over an hour,
# holding the GPU, and had to be found and killed by hand. A local model is slow, not
# infinitely slow -- anything past this is stuck, and a stuck run that never returns is
# worse than one that fails.
DEFAULT_TIMEOUT = int(os.environ.get("LOCAL_AGENT_TIMEOUT", "1800"))

# How often to say the run is still alive. A hung run and a slow one look identical
# without this, which is what makes people wait twenty-five minutes for nothing.
HEARTBEAT_SECONDS = 30

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


def forget_session(profile):
    s = load_sessions()
    if s.pop(profile, None) is None:
        return
    try:
        SESSIONS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


def looks_like_stale_session(proc):
    """Whether a failed run failed because the session id no longer resolves.

    Matched on the message rather than an exit code because Claude Code reports this
    as an ordinary failure. Kept narrow on purpose: retrying a genuine task failure
    without the session would silently throw away the context the resume existed for.
    """
    blob = ((proc.stdout or "") + (proc.stderr or "")).lower()
    return any(p in blob for p in (
        "no conversation found", "session not found", "could not find session",
        "no such session", "invalid session id"))


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


def kill_tree(pid):
    """Kill a process and everything it started.

    Killing only the direct child is not enough: the launcher spawns its own children,
    and those keep the model resident and the work going. Windows has no process groups
    in the POSIX sense, so taskkill /T walks the tree; elsewhere the child is made a
    session leader at spawn so one signal reaches the whole group.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(1)


def note_running(pid, profile, task):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        RUNNING.write_text(json.dumps({
            "pid": pid, "profile": profile, "started": datetime.now().isoformat(timespec="seconds"),
            "task": task[:200]}, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_running():
    try:
        RUNNING.unlink(missing_ok=True)
    except OSError:
        pass


def read_running():
    if not RUNNING.exists():
        return None
    try:
        return json.loads(RUNNING.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def pid_alive(pid):
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                           capture_output=True, text=True)
        return str(pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def run_child(cmd, env, timeout, profile, task):
    """Run the local agent with a ceiling, a heartbeat, and a recorded pid.

    Returns (CompletedProcess, timed_out). The pid file is what lets `ps` say a
    delegation is running and `kill` stop one that outlived its caller.
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    # Closed stdin, deliberately. `claude -p` also reads stdin for piped context, so an
    # inherited handle that never delivers anything costs a three-second wait and a
    # "no stdin data received" warning on every single attempt. The prompt goes in the
    # command line; there is nothing to pipe.
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", **kwargs)
    note_running(proc.pid, profile, task)

    stop = threading.Event()

    def heartbeat():
        t0 = time.monotonic()
        while not stop.wait(HEARTBEAT_SECONDS):
            mins, secs = divmod(int(time.monotonic() - t0), 60)
            print(f"  · still working, {mins}m{secs:02d}s elapsed "
                  f"(ceiling {timeout // 60}m)", flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        print(f"  ⚠ no result after {timeout // 60} minutes — stopping it",
              file=sys.stderr, flush=True)
        kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
    finally:
        stop.set()
        clear_running()

    code = 124 if timed_out else proc.returncode
    return subprocess.CompletedProcess(cmd, code, out or "", err or ""), timed_out


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


def powershell():
    """The PowerShell to run verification commands in, or None to fall back.

    Windows `shell=True` means cmd.exe, which is not the shell this skill documents.
    Set LOCAL_AGENT_VERIFY_SHELL=cmd to go back to it — worth knowing about, because
    Windows PowerShell 5.1 has no `&&`, so a verification command chained that way
    needs either cmd or a `;`.
    """
    if os.name != "nt" or os.environ.get("LOCAL_AGENT_VERIFY_SHELL", "").lower() == "cmd":
        return None
    return shutil.which("pwsh") or shutil.which("powershell")


def write_verify_script(command, directory):
    """Put the command in a .ps1 rather than passing it to -Command.

    A script file sidesteps the second round of quote parsing that -Command applies,
    which mangles any command carrying quoted paths. Two details matter:
    `exit $LASTEXITCODE` because PowerShell otherwise reports the *script's* status
    and a verification step that always passes is worse than none; and the call
    operator, because a line beginning with a quoted string is a string expression in
    PowerShell, not a command to run.
    """
    body = command.strip()
    if body[:1] in ('"', "'"):
        body = "& " + body
    script = Path(directory) / "verify.ps1"
    script.write_text(f"{body}\nexit $LASTEXITCODE\n", encoding="utf-8")
    return script


def run_verify(command):
    print(f"  ↻ verifying: {command}")
    shell = powershell()
    try:
        if shell:
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            script = write_verify_script(command, WORK_DIR)
            argv, use_shell = [shell, "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass", "-File", str(script)], False
        else:
            argv, use_shell = command, True
        p = subprocess.run(argv, shell=use_shell, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run the verification command: {e}"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, out[-4000:]


def run_one(profile, task, read_only=False, verify=None, attempts=DEFAULT_ATTEMPTS,
            baseline=None, add_dirs=None, fresh=False, allow_installs=False,
            check_tree=True, timeout=DEFAULT_TIMEOUT, think=False):
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
    env["ANTHROPIC_BASE_URL"] = oa.BASE       # replaced below when the proxy is on
    env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
    env["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude-local")
    env["PYTHONIOENCODING"] = "utf-8"

    resume_id = None if (fresh or read_only) else load_sessions().get(profile)
    rules = project_rules()
    transcript = []
    ok = False
    detail = ""

    # Thinking is on by default in Ollama and cannot be turned off from the client, so
    # the requests are rewritten on the way through. thinkproxy.py records what was
    # tried first and why each one failed. --think keeps the model's own behaviour.
    proxy = None
    if think:
        print("  · thinking left on for this run")
    else:
        proxy = thinkproxy.ThinkProxy(oa.BASE, timeout_s=timeout)
        proxy.__enter__()
        env["ANTHROPIC_BASE_URL"] = proxy.url

    try:
        return _run_attempts(profile, task, read_only, verify, attempts, add_dirs,
                             allow_installs, timeout, model, env, claude_cmd, log,
                             resume_id, rules, transcript)
    finally:
        if proxy:
            proxy.__exit__(None, None, None)


def _run_attempts(profile, task, read_only, verify, attempts, add_dirs, allow_installs,
                  timeout, model, env, claude_cmd, log, resume_id, rules, transcript):
    """The attempt loop. Split out only so the proxy has one place to be shut down."""
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
            proc, timed_out = run_child(cmd, env, timeout, profile, task)
        except OSError as e:
            raise Refused(f"could not start the local agent: {e}")

        # A stored session id can outlive the session it names — the local config
        # directory gets cleared, or the transcript is pruned. Without this the profile
        # is stuck: every run resumes an id that no longer resolves, and the only way
        # out is a --fresh the caller has no reason to suspect it needs.
        if resume_id and proc.returncode != 0 and looks_like_stale_session(proc):
            print(f"  · stored session {resume_id[:8]} no longer resolves — starting fresh",
                  file=sys.stderr)
            forget_session(profile)
            resume_id = None
            cmd = build_command(claude_cmd, profile, model, prompt, read_only,
                                None, add_dirs, allow_installs)
            try:
                proc, timed_out = run_child(cmd, env, timeout, profile, task)
            except OSError as e:
                raise Refused(f"could not start the local agent: {e}")

        if timed_out:
            # Nothing useful came back and the tree is dead. Retrying the identical
            # prompt would stall the same way, so stop rather than burn the attempts.
            detail = (f"the local agent produced no result within "
                      f"{timeout // 60} minutes and was stopped")
            print(f"  ✗ {detail}", file=sys.stderr)
            transcript.append(detail)
            ok = False
            break

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


def ordered_queue():
    """The queue in the order a flush would run it: grouped by profile, heaviest first.

    `list` and `drop` must agree on what "number 2" means, so both go through here
    rather than each imposing its own order on the file.
    """
    items = read_queue()
    return [it for p in ORDER for it in items if it["profile"] == p]


def show_queue():
    items = ordered_queue()
    if not items:
        print("queue is empty")
        return 0
    for n, it in enumerate(items, 1):
        v = f"   [verify: {it['verify']}]" if it.get("verify") else ""
        print(f"  {n}. [{it['profile']}] {it['task']}{v}")
    return 0


def drop_queued(which):
    """Remove one queued task, or all of them.

    A failed flush leaves its tasks in the queue on purpose, so they can be retried --
    but retrying is not always what you want, and without this the only way to change
    your mind is to edit queue.jsonl by hand.
    """
    items = ordered_queue()
    if not items:
        print("queue is empty, nothing to drop")
        return 0
    if str(which).lower() == "all":
        QUEUE.unlink(missing_ok=True)
        print(f"dropped all {len(items)} queued task(s)")
        return 0
    try:
        n = int(which)
    except ValueError:
        raise Refused(f"expected a number from `list`, or 'all' — got {which!r}")
    if not 1 <= n <= len(items):
        raise Refused(f"there is no task {n}; the queue has {len(items)}")
    gone = items.pop(n - 1)
    if items:
        write_rows(QUEUE, items)
    else:
        QUEUE.unlink(missing_ok=True)
    print(f"dropped {n}. [{gone['profile']}] {gone['task'][:80]}")
    return 0


def flush(attempts=DEFAULT_ATTEMPTS, baseline=None, allow_installs=False,
          timeout=DEFAULT_TIMEOUT, think=False):
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
                           check_tree=False, timeout=timeout, think=think) != 0:
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
    # The skill's own state — sessions.json, verify.ps1, the logs — is not the agent's
    # work, and listing it pollutes the very diff §10 tells the reviewer to trust.
    # Taken from WORK_DIR rather than hardcoded, because LOCAL_AGENT_DIR can move it.
    own = WORK_DIR.as_posix().rstrip("/") + "/"
    new_files = [f for f in untracked.splitlines()
                 if f.strip() and not f.startswith(own)]
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
        p.add_argument("--think", action="store_true",
                       help="leave the model's own thinking on; off by default because "
                            "it is measured at 100x slower for the same answer")
        p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                       help=f"ceiling on one attempt before the process tree is killed "
                            f"(default {DEFAULT_TIMEOUT})")

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
    sub.add_parser("ps", help="what is loaded, and whether a delegation is running")
    sub.add_parser("kill", help="stop a running delegation and its process tree")
    dq = sub.add_parser("drop", help="remove a queued task, or clear the queue")
    dq.add_argument("which", nargs="?", default="all",
                    help="1-based position from `list`, or 'all' (default)")
    sub.add_parser("gitignore", help="add the machine-only paths to .gitignore")

    a = ap.parse_args()

    if a.cmd == "run":
        return run_one(a.profile, a.task, read_only=a.ro, verify=a.verify,
                       attempts=a.attempts, baseline=a.baseline,
                       add_dirs=a.add_dir, fresh=a.fresh,
                       allow_installs=a.allow_installs or ALLOW_INSTALLS,
                       timeout=a.timeout, think=a.think)
    if a.cmd == "queue":
        return enqueue(a.profile, a.task, a.verify)
    if a.cmd == "flush":
        return flush(a.attempts, a.baseline, a.allow_installs or ALLOW_INSTALLS,
                     a.timeout, a.think)
    if a.cmd == "list":
        return show_queue()
    if a.cmd == "drop":
        return drop_queued(a.which)
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
        print("in VRAM: " + (", ".join((m.get("name") or m.get("model"))
                                       for m in loaded) or "(nothing loaded)"))
        # Which model Ollama holds says nothing about whether a delegation is running;
        # a stalled one that outlived its caller looks exactly like an idle machine.
        r = read_running()
        if not r:
            print("delegation: none running")
        elif pid_alive(r["pid"]):
            print(f"delegation: {r['profile']} running since {r['started']} "
                  f"(pid {r['pid']}) — `kill` stops it")
            print(f"  task: {r['task'][:100]}")
        else:
            print(f"delegation: stale record for pid {r['pid']}, process is gone")
            clear_running()
        return 0

    if a.cmd == "kill":
        r = read_running()
        if not r:
            print("no delegation recorded as running")
            return 0
        if not pid_alive(r["pid"]):
            print(f"pid {r['pid']} is already gone; clearing the record")
            clear_running()
            return 0
        print(f"stopping {r['profile']} (pid {r['pid']}) and everything it started")
        kill_tree(r["pid"])
        clear_running()
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
