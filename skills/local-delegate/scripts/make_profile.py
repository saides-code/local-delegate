#!/usr/bin/env python3
"""Build a `local-<profile>` model from an Ollama tag, with an explicit context
window and sampling parameters.

    python make_profile.py code qwen3-coder:30b 65536 \
        temperature=1.0 top_p=0.95 top_k=40 min_p=0.01 repeat_penalty=1.0

Nothing about which model or which parameters is hardcoded here, on purpose.
Recommended sampling values differ per model family and vendors revise them;
a wrong value (a low temperature on a family that asks for 1.0 is the classic)
degrades output quietly, without ever raising an error. Look them up on the
model card during setup and pass them in.

The context window matters just as much: Ollama truncates to 4096 tokens by
default, which is shorter than Claude Code's own system prompt. Baking num_ctx
into the model is what stops the local agent from starting out lobotomised.
"""
import argparse
import shutil
import subprocess
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as cfgmod  # noqa: E402
import runtime  # noqa: E402

runtime.fix_console()

# `vision` is here because setup.md tells the operator to propose a fifth profile
# when a capability the four do not cover comes up. An instruction you cannot execute
# is worse than no instruction: it pushes the work onto an existing profile, which is
# exactly the bending the document forbids.
PROFILES = ("code", "fast", "text", "tiny", "vision")

# E-05: two candidates for one profile have to coexist to be compared. A suffix gives
# the challenger its own model and its own config slot, so the incumbent keeps serving
# while the measurement happens.
def slot(profile, suffix=None):
    return f"{profile}-{suffix}" if suffix else profile


def parse_params(pairs):
    out = []
    for kv in pairs:
        if "=" not in kv:
            raise SystemExit(f"malformed parameter {kv!r} — expected key=value")
        k, v = kv.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            raise SystemExit(f"malformed parameter {kv!r} — expected key=value")
        out.append((k, v))
    return out


def build(profile, tag, num_ctx, params, extra_lines=()):
    lines = [f"FROM {tag}", f"PARAMETER num_ctx {num_ctx}"]
    lines += [f"PARAMETER {k} {v}" for k, v in params]
    lines += list(extra_lines)
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", choices=PROFILES)
    ap.add_argument("--as", dest="suffix", metavar="NAME",
                    help="build as a challenger (local-code-b) instead of replacing "
                         "the incumbent, so both can be measured")
    ap.add_argument("tag", help="Ollama tag to pull, e.g. qwen3-coder:30b")
    ap.add_argument("num_ctx", type=int, help="context window to bake in")
    ap.add_argument("params", nargs="*", metavar="key=value",
                    help="sampling parameters from the model card")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the Modelfile without pulling or creating")
    ap.add_argument("--reason", help="one line on why this model for this job; "
                                     "stored in the config and read by the update flow")
    a = ap.parse_args()

    if a.num_ctx < 8192:
        print(f"warning: num_ctx {a.num_ctx} is small. Claude Code's system prompt "
              f"alone is longer than that — the agent will start out truncated.",
              file=sys.stderr)

    params = parse_params(a.params)
    if not params:
        print("warning: no sampling parameters given, the model keeps its defaults.\n"
              "         If its model card recommends specific values, pass them.",
              file=sys.stderr)

    modelfile = build(a.profile, a.tag, a.num_ctx, params)
    key = slot(a.profile, a.suffix)
    name = f"local-{key}"
    print(f"── {name} ← {a.tag}")
    print("".join(f"   {l}\n" for l in modelfile.splitlines()))

    if a.dry_run:
        return 0
    if not shutil.which("ollama"):
        raise SystemExit("ollama is not on PATH")

    subprocess.run(["ollama", "pull", a.tag], check=True)
    with tempfile.TemporaryDirectory() as td:
        mf = Path(td) / "Modelfile"
        mf.write_text(modelfile, encoding="utf-8")
        subprocess.run(["ollama", "create", name, "-f", str(mf)], check=True)

    try:
        cfg = cfgmod.load()
        prev = cfg["profiles"].get(key, {})
        if prev.get("model") and prev["model"] != a.tag:
            prev["previous_model"] = prev["model"]
        prev.update({"model": a.tag, "num_ctx": a.num_ctx, "params": dict(params),
                     "reason": a.reason or prev.get("reason", ""), "set_at": cfgmod.now()})
        cfg["profiles"][key] = prev
        cfgmod.save(cfg)
        print(f"✓ {name} ready (recorded in {cfgmod.config_path()})")
    except OSError:
        print(f"✓ {name} ready (could not write the config file)")
    return 0





if __name__ == "__main__":
    try:
        code = main()
    except BrokenPipeError:
        code = 0
    runtime.quiet_broken_pipe()
    sys.exit(code)
