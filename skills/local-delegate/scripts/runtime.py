"""Shared runtime concerns: console encoding, and finding the Claude Code launcher.

Both exist because of failures observed on a real Windows install, not in theory.
"""
import os
import shlex
import shutil
import sys
from pathlib import Path


def fix_console():
    """Make output survive a cp1252 console, and appear while it is still useful.

    Two problems, one call.

    Encoding: on a Windows console the default codec raises UnicodeEncodeError on the
    first box-drawing character, which killed the installer before it downloaded
    anything. errors="replace" degrades a glyph to a placeholder instead of taking the
    process down.

    Buffering: Python block-buffers stdout whenever it is not a terminal, so a flush
    piped to a file or run in the background emits nothing at all until it exits. A
    delegation can run for ten minutes, and for all of them an empty log is
    indistinguishable from a hang -- which is the one thing progress output exists to
    rule out. Line buffering costs nothing here and makes every step visible as it
    happens.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass


def quiet_broken_pipe():
    """`python x.py | head` closes the pipe early; without this Python prints a
    traceback that reads like a crash. Exit quietly, as CLI tools do."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


def _version_key(p):
    """Sortable version tuple for a directory named like 2.1.247.

    Always integers: mixing an int list with a str list makes the comparison raise
    TypeError rather than sort, which would crash discovery on any install that has
    both a bare launcher and versioned subdirectories beside it.
    """
    parts = []
    for chunk in p.name.split("."):
        parts.append(int(chunk) if chunk.isdigit() else -1)
    return parts


CLAUDE_BIN_ENV = "LOCAL_AGENT_CLAUDE_BIN"


def claude_command():
    """The argv prefix that starts Claude Code, as a list.

    A list rather than a path because the launcher is not always one file. An npm
    install is `node <path>/cli.js`, and an operator whose install auto-discovery
    cannot see is better served by naming the whole command than by being told to fix
    their PATH. Set LOCAL_AGENT_CLAUDE_BIN to override, quoting as you would in a
    shell; anything else falls back to discovery.
    """
    override = os.environ.get(CLAUDE_BIN_ENV)
    if override:
        parts = shlex.split(override, posix=(os.name != "nt"))
        if parts:
            return [p.strip('"') for p in parts]
    found = find_claude()
    return [found] if found else []


def find_claude():
    """Locate the Claude Code launcher.

    PATH first, then the known install roots. The desktop app installs it under a
    versioned directory that changes with every update, so a PATH entry added by hand
    goes stale; picking the highest version found survives upgrades. This mirrors what
    check_machine.py already does for Ollama.
    """
    override = os.environ.get(CLAUDE_BIN_ENV)
    if override:
        parts = shlex.split(override, posix=(os.name != "nt"))
        if parts:
            return parts[0].strip('"')

    # A real executable is worth preferring over a .cmd/.bat shim: on Windows the shim
    # runs through cmd.exe, which truncates any argument at its first newline. The
    # prompt carries the task and every project rule, both multi-line, so a shim
    # silently delivers the first line and nothing else.
    found = shutil.which("claude")
    if found and not str(found).lower().endswith((".cmd", ".bat")):
        return found

    roots = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    home = Path.home()
    if appdata:
        roots.append(Path(appdata) / "Claude" / "claude-code")
    if localappdata:
        roots += [Path(localappdata) / "Claude" / "claude-code",
                  Path(localappdata) / "Programs" / "claude-code"]
    roots += [home / ".claude" / "local", home / ".local" / "share" / "claude-code",
              Path("/usr/local/lib/claude-code"), Path("/opt/claude-code")]

    candidates = []
    for root in roots:
        if not root.is_dir():
            continue
        for exe in ("claude.exe", "claude"):
            direct = root / exe
            if direct.is_file():
                candidates.append(([0], direct))       # unversioned: ranks lowest
        for child in root.iterdir():                 # versioned subdirectories
            if not child.is_dir():
                continue
            for exe in ("claude.exe", "claude"):
                c = child / exe
                if c.is_file():
                    candidates.append((_version_key(child), c))
    if not candidates:
        return found                    # a shim is still better than nothing
    candidates.sort(key=lambda t: t[0])
    return str(candidates[-1][1])
