#!/usr/bin/env python3
"""Read and write the local-delegate configuration.

Two files, deliberately, because they answer different questions:

  ~/.local-delegate/config.json     WHICH models back which profile on THIS machine,
                                    the parameters they were built with, why each was
                                    chosen, and what the last selfcheck measured.
                                    Machine-specific: Ollama is one service per box,
                                    and these tags mean nothing on another computer.

  <repo>/.local-delegate/project.md WHAT this project tends to delegate: recurring
                                    task shapes, things that must never be delegated,
                                    house conventions the local agent should follow.
                                    Portable: it survives a change of models and is
                                    worth committing so the team shares it.

Keeping them apart is what lets the update flow reason about models without touching
project decisions, and lets a project note outlive any particular model.

    python config.py show
    python config.py set-profile code --model qwen3-coder:30b --num-ctx 65536 \
        --params temperature=1.0 top_p=0.95 --reason "best tool calling under 20 GB"
    python config.py record-check code --tps 18.4 --vram-pct 62 --tools yes
    python config.py record-scan --note "nothing newer worth switching to"
    python config.py init-project           # creates .local-delegate/project.md here
"""
import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(".local-delegate")
PROJECT_FILE = PROJECT_DIR / "project.md"
HOME_DIR = Path.home() / ".local-delegate"

# config.json is machine-exclusive: the tags it names depend on what is installed on
# this box and on how much VRAM it has, and the measurements come from this GPU. Only
# project.md — what to delegate here — is portable and worth committing.
MACHINE_ONLY = ["config.json", "*.log", "queue.jsonl", "queue.*.done"]


def project_dir():
    """The skill's folder, in the repo. Falls back to home outside a repository.

    Model requirements are a property of the project, not only of the machine: one
    repo needs a strong coder, another needs translation quality, and they should not
    overwrite each other's choices. So the config lives with the code.
    """
    if os.environ.get("LOCAL_DELEGATE_HOME"):
        return Path(os.environ["LOCAL_DELEGATE_HOME"])
    if PROJECT_DIR.exists():
        return PROJECT_DIR
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return PROJECT_DIR
    except (OSError, subprocess.SubprocessError):
        pass
    return HOME_DIR


def config_path():
    return project_dir() / "config.json"


def machine_id():
    """Enough to notice we are on different hardware, not a fingerprint."""
    gpu = ""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            first = r.stdout.strip().splitlines()[0].split(",")
            gpu = f"{first[0].strip()} {round(int(first[1])/1024)}GB" if len(first) > 1 else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {"host": platform.node(), "gpu": gpu}

# Rechecking the model landscape more often than this is noise: new generations do
# not ship weekly, and the research costs real tokens.
SCAN_INTERVAL_DAYS = 30

PROJECT_TEMPLATE = """# Delegation notes for this project

Written by the `local-delegate` skill. Portable across machines and worth committing:
it says what to delegate here, not which models exist on any one computer.

## What this project is

<languages, frameworks, test command — filled in at setup>

## Delegate here

<recurring mechanical work in this repo: which files, which task shapes>

## Never delegate here

<parts that need judgement, or that burned us before>

## Conventions the local agent must follow

<test command that has to stay green, style rules, files that are off limits>
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load():
    CONFIG = config_path()
    if not CONFIG.exists():
        return {"version": 1, "profiles": {}, "last_scan": None, "scan_history": []}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"warning: {CONFIG} is unreadable, starting fresh", file=sys.stderr)
        return {"version": 1, "profiles": {}, "last_scan": None, "scan_history": []}


def save(cfg):
    cfg["machine"] = machine_id()
    CONFIG = config_path()
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def days_since(iso):
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).days


def cmd_show(a):
    cfg = load()
    CONFIG = config_path()
    if not cfg["profiles"]:
        print(f"No configuration yet ({CONFIG} does not exist).")
        print("Run the guided setup — see references/setup.md.")
        return 1
    checks = cfg.get("checks", {})
    print(f"Config: {CONFIG}   (this machine only — do not commit)")
    warn_if_foreign(cfg)
    print()
    for name, p in cfg["profiles"].items():
        print(f"[{name}] {p.get('model', '?')}   ctx {p.get('num_ctx', '?')}")
        if p.get("reason"):
            print(f"    why: {p['reason']}")
        if p.get("params"):
            print(f"    params: {' '.join(f'{k}={v}' for k, v in p['params'].items())}")
        c = checks.get(name)
        if c:
            print(f"    last check ({days_since(c.get('at'))}d ago): "
                  f"{c.get('tps', '?')} tok/s, {c.get('vram_pct', '?')}% in VRAM, "
                  f"tools {'yes' if c.get('tools') else 'NO'}")
        else:
            print("    last check: never — run selfcheck.py")
        print()
    d = days_since(cfg.get("last_scan"))
    if cfg.get("last_scan"):
        print(f"Last look for newer models: {d} days ago")
        for h in cfg.get("scan_history", [])[-3:]:
            print(f"  · {h.get('at', '')[:10]}: {h.get('note', '')}")
        if d is not None and d >= SCAN_INTERVAL_DAYS:
            print(f"\n→ Over {SCAN_INTERVAL_DAYS} days: worth checking for newer models "
                  f"(see references/update.md).")
    else:
        print("Never looked for newer models. See references/update.md.")
    return 0


def warn_if_foreign(mach):
    """A config written on other hardware describes models that may not exist here,
    and speeds that were never measured on this GPU. Silence would be worse than a
    warning: the numbers look authoritative."""
    was = mach.get("machine")
    if not was:
        return
    now_id = machine_id()
    if was.get("host") == now_id["host"] and was.get("gpu") == now_id["gpu"]:
        return
    print(f"⚠ written on {was.get('host', '?')} ({was.get('gpu') or 'unknown GPU'}), "
          f"you are on {now_id['host']} ({now_id['gpu'] or 'unknown GPU'}).")
    print("  Tags and measurements may not apply here — re-run setup or selfcheck.")


def cmd_set_profile(a):
    cfg = load()
    params = {}
    for kv in a.params or []:
        if "=" not in kv:
            raise SystemExit(f"malformed parameter {kv!r} — expected key=value")
        k, v = kv.split("=", 1)
        params[k] = v
    entry = cfg["profiles"].get(a.profile, {})
    # Keep the previous model around: the update flow needs something to roll back to.
    if entry.get("model") and entry["model"] != a.model:
        entry["previous_model"] = entry["model"]
    entry.update({"model": a.model, "num_ctx": a.num_ctx, "params": params,
                  "reason": a.reason or entry.get("reason", ""), "set_at": now()})
    cfg["profiles"][a.profile] = entry
    save(cfg)
    print(f"recorded [{a.profile}] → {a.model}")
    return 0


def cmd_record_check(a):
    cfg = load()
    cfg.setdefault("checks", {})[a.profile] = {
        "at": now(), "tps": a.tps, "vram_pct": a.vram_pct,
        "tools": a.tools == "yes", "issues": a.issue or [],
    }
    save(cfg)
    return 0


def cmd_record_scan(a):
    cfg = load()
    cfg["last_scan"] = now()
    cfg.setdefault("scan_history", []).append({"at": now(), "note": a.note})
    cfg["scan_history"] = cfg["scan_history"][-20:]
    save(cfg)
    print(f"recorded: {a.note}")
    return 0


def cmd_due(a):
    """Exit 0 when a fresh look for newer models is due. For scripting."""
    d = days_since(load().get("last_scan"))
    due = d is None or d >= SCAN_INTERVAL_DAYS
    print(f"{'due' if due else 'not due'} (last scan: "
          f"{'never' if d is None else f'{d} days ago'})")
    return 0 if due else 1


def cmd_where(a):
    d = project_dir()
    print(f"Folder: {d}\n")
    print(f"  project.md    {'ok ' if (d / 'project.md').exists() else '-  '} "
          f"what to delegate here — the only file worth committing")
    print(f"  config.json   {'ok ' if config_path().exists() else '-  '} "
          f"models, parameters, measurements — MACHINE ONLY, do not commit")
    print(f"  *.log, queue      transient — do not commit")
    return 0


def cmd_move_here(a):
    """Move the model configuration into the repo, for people who want one place."""
    src, dst = config_path(), PROJECT_DIR / "config.json"
    if src.exists() and src.resolve() == dst.resolve():
        print("already repo-local — nothing to do")
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"copied {src} → {dst}")
        print(f"the original is left in place; delete it if you want a single source")
    else:
        save_to = {"version": 1, "profiles": {}, "last_scan": None, "scan_history": []}
        dst.write_text(json.dumps(save_to, indent=2), encoding="utf-8")
        print(f"created {dst}")
    print("\nFrom now on this project uses its own configuration.")
    print("Committing it shares the model choices with the repo — useful on one")
    print("machine, misleading on another, which is why `show` warns when the")
    print("hardware does not match.")
    return 0


def cmd_gitignore_hint(a):
    """Print the lines that must not be committed, and whether anything is needed.

    Everything here is moot outside a git repository, so say that plainly rather than
    handing over rules for a file that will never exist.
    """
    base = PROJECT_DIR.as_posix()
    lines = [f"{base}/{e}" for e in MACHINE_ONLY]
    try:
        r = subprocess.run(["git", "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=10)
        is_repo = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        is_repo = False
    if not is_repo:
        print("not a git repository — nothing to ignore")
        return 0
    gi = Path(".gitignore")
    have = set()
    if gi.exists():
        try:
            have = {l.strip() for l in gi.read_text(encoding="utf-8",
                                                    errors="replace").splitlines()}
        except OSError:
            pass
    if any(p in have for p in (base, f"{base}/", f"/{base}/")) or all(l in have for l in lines):
        print("already covered by .gitignore")
        return 0
    print("\n".join(l for l in lines if l not in have))
    return 0


def cmd_init_project(a):
    if PROJECT_FILE.exists():
        print(f"{PROJECT_FILE} already exists, leaving it alone")
        return 0
    PROJECT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_FILE.write_text(PROJECT_TEMPLATE, encoding="utf-8")
    print(f"created {PROJECT_FILE} — fill it in with what this project delegates")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print the current configuration")

    s = sub.add_parser("set-profile", help="record which model backs a profile")
    s.add_argument("profile")
    s.add_argument("--model", required=True)
    s.add_argument("--num-ctx", type=int, required=True)
    s.add_argument("--params", nargs="*", metavar="key=value")
    s.add_argument("--reason", help="one line on why this model for this job")

    c = sub.add_parser("record-check", help="store a selfcheck result")
    c.add_argument("profile")
    c.add_argument("--tps", type=float)
    c.add_argument("--vram-pct", type=int)
    c.add_argument("--tools", choices=["yes", "no"], default="yes")
    c.add_argument("--issue", action="append")

    r = sub.add_parser("record-scan", help="note that you looked for newer models")
    r.add_argument("--note", required=True,
                   help="what you found, or why nothing changed")

    sub.add_parser("due", help="is a fresh look for newer models due?")
    sub.add_parser("where", help="which files are in use")
    sub.add_parser("gitignore-hint", help="print the lines that must not be committed")
    sub.add_parser("move-here", help="move the model config into this repo")
    sub.add_parser("init-project", help="create .local-delegate/project.md here")

    a = ap.parse_args()
    return {"show": cmd_show, "set-profile": cmd_set_profile,
            "record-check": cmd_record_check, "record-scan": cmd_record_scan,
            "due": cmd_due, "where": cmd_where, "move-here": cmd_move_here,
            "gitignore-hint": cmd_gitignore_hint,
            "init-project": cmd_init_project}[a.cmd](a)


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
        code = main()
    except BrokenPipeError:
        code = 0
    _quiet_broken_pipe()
    sys.exit(code)
