#!/usr/bin/env python3
"""Write a restore report for every installed Ollama model. Deletes nothing.

Run this before removing anything. Models built locally with `ollama create`,
or imported from a GGUF, exist nowhere else: once removed they are gone, and
their saved Modelfile is not enough to rebuild them because its FROM line points
at a blob that disappears with the model. This script tells you which ones those
are, so the decision to delete is an informed one.

    python backup_models.py [output-directory]
"""
import argparse
import json
import platform
import shutil
import subprocess
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ollama_api as oa  # noqa: E402


def ollama(*args):
    try:
        p = subprocess.run(["ollama", *args], capture_output=True, text=True, timeout=60)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def registry_reachable():
    try:
        req = urllib.request.Request("https://ollama.com/library/gemma4", method="HEAD")
        urllib.request.urlopen(req, timeout=10)
        return True
    except (urllib.error.URLError, OSError):
        return False


def is_pullable(tag):
    base = tag.split(":", 1)[0]
    url = f"https://ollama.com/{base}" if "/" in base else f"https://ollama.com/library/{base}"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
        return True
    except (urllib.error.URLError, OSError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir", nargs="?",
                    default=str(Path.home() / f"ollama-backup-{datetime.now():%Y%m%d-%H%M%S}"))
    a = ap.parse_args()

    if not shutil.which("ollama"):
        raise SystemExit("ollama is not on PATH")
    if not oa.is_up():
        raise SystemExit(f"Ollama is not answering on {oa.BASE}")

    models = oa.installed_models()
    if not models:
        print("No models installed — nothing to back up.")
        return 0

    out = Path(a.outdir)
    (out / "modelfiles").mkdir(parents=True, exist_ok=True)

    online = registry_reachable()
    if not online:
        print("⚠ ollama.com unreachable: cannot tell re-downloadable models from local-only "
              "ones. Treat every model below as irreplaceable until you can check.")

    report = [f"# Ollama models present before cleanup", "",
              f"Date: {datetime.now():%Y-%m-%d %H:%M}  ·  Host: {platform.node()}", "",
              "```", ollama("list").rstrip(), "```", ""]
    restore = ["#!/usr/bin/env bash",
               f"# Restores the models present on {datetime.now():%Y-%m-%d}.",
               "set -e", ""]
    custom = []

    for m in models:
        safe = m.replace(":", "_").replace("/", "_")
        mf = ollama("show", "--modelfile", m)
        if mf:
            (out / "modelfiles" / f"{safe}.Modelfile").write_text(mf, encoding="utf-8")
        report += [f"### {m}", "```", (ollama("show", m) or "(details unavailable)").rstrip(),
                   "```", ""]
        if online and is_pullable(m):
            restore.append(f"ollama pull {m}")
            print(f"  · {m} — re-downloadable")
        else:
            custom.append(m)
            restore += [
                f"# {m} — NOT on the registry.",
                f"#   Its Modelfile is in modelfiles/{safe}.Modelfile, but the FROM line",
                f"#   points at a local blob that disappears with the model. To rebuild it",
                f"#   you must replace that line with the correct base model, then:",
                f"#   ollama create {m} -f modelfiles/{safe}.Modelfile", ""]
            print(f"  · {m} — {'LOCAL ONLY, cannot be re-downloaded' if online else 'status unknown'}")

    if custom:
        report += ["## ⚠ Warning", "",
                   "These models are not on the Ollama registry — removing them is permanent:",
                   ""] + [f"- `{c}`" for c in custom] + [""]

    (out / "report.md").write_text("\n".join(report), encoding="utf-8")
    rp = out / "restore.sh"
    rp.write_text("\n".join(restore) + "\n", encoding="utf-8")
    rp.chmod(0o755)
    (out / "models.json").write_text(json.dumps(
        {"date": datetime.now().isoformat(), "models": models, "local_only": custom},
        indent=2), encoding="utf-8")

    print(f"\nReport:  {out / 'report.md'}")
    print(f"Restore: {rp}")
    if custom:
        print(f"\n⚠ {len(custom)} model(s) cannot be re-downloaded:")
        for c in custom:
            print(f"   {c}")
    print("\nNothing was removed. Deletion is a separate, deliberate `ollama rm <name>`.")
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
        code = main()
    except BrokenPipeError:
        code = 0
    _quiet_broken_pipe()
    sys.exit(code)
