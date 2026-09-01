#!/usr/bin/env python3
"""Quick health check of every local-* profile. A couple of minutes.

For each profile it measures load time, generation speed, how much of the model
actually sits in VRAM, and whether tool calling really works — then evicts it so
the next one starts from a clean card.

    python selfcheck.py              # all profiles
    python selfcheck.py code text    # only these
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ollama_api as oa  # noqa: E402
import runtime  # noqa: E402

runtime.fix_console()
import config as cfgmod  # noqa: E402

PROFILES = ("code", "fast", "text", "tiny", "vision")
NS = 1_000_000_000

PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    },
}

# Below this a coding agent is slower than doing the work by hand.
MIN_TOKENS_PER_SEC = 5.0
# The `code` profile is expected to spill into RAM; the others are not.
MIN_VRAM_FRACTION = 0.6


def server_conditions():
    """The tuning variables as this shell sees them.

    Be honest about what this is. Ollama runs as its own process — a service, a
    desktop app, sometimes another host — and does not report its configuration over
    the API, so these are *this shell's* variables and agree with the server only when
    the server inherited them. They are recorded because a measurement without its
    conditions cannot be compared later, not because they are authoritative.
    """
    return {
        "source": "client shell environment, not read from the server",
        "flash_attention": os.environ.get("OLLAMA_FLASH_ATTENTION", "unset here"),
        "kv_cache_type": os.environ.get("OLLAMA_KV_CACHE_TYPE", "unset here"),
        "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "unset here"),
    }


def blobs_dir():
    """Where Ollama keeps model blobs, when it is running on this machine."""
    if not any(h in oa.BASE for h in ("localhost", "127.0.0.1", "::1")):
        return None
    root = os.environ.get("OLLAMA_MODELS")
    return Path(root) / "blobs" if root else Path.home() / ".ollama" / "models" / "blobs"


def pull_in_flight():
    """True when a download is still writing, which invalidates any measurement.

    Worth a check rather than a footnote: the same model measured during a 22 GB pull
    read 16.3 tok/s and 67.6 tok/s once the disk was quiet — a factor of four, written
    into the config identically either way.
    """
    d = blobs_dir()
    if not d or not d.is_dir():
        return False
    try:
        return any("partial" in p.name for p in d.iterdir())
    except OSError:
        return False


def measure(profile):
    name = f"local-{profile}"
    row = {"profile": profile, "model": name, "issues": []}

    gen = oa.generate(name, "Reply with one word: ready", keep_alive="5m")
    eval_count = gen.get("eval_count", 0)
    eval_ns = gen.get("eval_duration", 0)
    row["load_s"] = gen.get("load_duration", 0) / NS
    row["total_ms"] = gen.get("total_duration", 0) / 1_000_000
    row["tokens"] = gen.get("prompt_eval_count", 0) + eval_count
    row["tps"] = eval_count / (eval_ns / NS) if eval_ns else 0.0

    row["vram_pct"] = None
    for m in oa.loaded_models():
        # The API reports "local-text:latest" while `name` is "local-text", so a
        # bare comparison never matches and the residency check silently never fires
        # — which is the one thing step 9 exists to catch on a card that is too small.
        loaded = (m.get("name") or m.get("model") or "").split(":")[0]
        if loaded == name.split(":")[0]:
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            if size:
                row["vram_pct"] = round(vram * 100 / size)
            break

    chat = oa.chat_with_tool(name, "What is the weather in Turin? Use the tool.", PROBE_TOOL)
    row["tools"] = bool(chat.get("message", {}).get("tool_calls"))

    if row["vram_pct"] == 0:
        row["issues"].append("runs entirely on CPU — it does not fit in VRAM")
    elif row["vram_pct"] is not None and row["vram_pct"] < MIN_VRAM_FRACTION * 100:
        verdict = ("expected for this profile" if profile == "code"
                   else "too big for what this profile does")
        row["issues"].append(f"only {row['vram_pct']}% in VRAM, rest spills to RAM — {verdict}")
    if row["tps"] < MIN_TOKENS_PER_SEC:
        row["issues"].append(f"{row['tps']:.1f} tok/s — too slow to drive an agent")
    if not row["tools"]:
        row["issues"].append("no tool calling — Claude Code cannot read or write "
                             "a single file with this model, the profile is unusable")

    oa.unload(name)
    return row


def record(row):
    """Persist the measurement. Later, `update` compares candidates against these
    real numbers rather than against what a model card claims."""
    try:
        cfg = cfgmod.load()
        cfg.setdefault("checks", {})[row["profile"]] = {
            "at": cfgmod.now(), "tps": round(row["tps"], 1),
            "vram_pct": row["vram_pct"], "tools": row["tools"], "issues": row["issues"],
            "conditions": server_conditions()}
        cfgmod.save(cfg)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profiles", nargs="*", metavar="PROFILE",
                    help=f"one or more of: {', '.join(PROFILES)} (default: all)")
    ap.add_argument("--anyway", action="store_true",
                    help="measure even while a model download is in flight")
    a = ap.parse_args()

    unknown = [p for p in a.profiles if p not in PROFILES]
    if unknown:
        raise SystemExit(f"unknown profile(s): {', '.join(unknown)} "
                         f"— pick from {', '.join(PROFILES)}")

    if not oa.is_up():
        raise SystemExit(f"Ollama is not answering on {oa.BASE}")

    present = set(oa.installed_models())
    wanted = [p for p in (a.profiles or PROFILES)
              if f"local-{p}" in present or f"local-{p}:latest" in present]
    if not wanted:
        raise SystemExit("No local-* profile found. Run the setup first.")

    if pull_in_flight() and not a.anyway:
        raise SystemExit(
            "A model download is still writing to disk. Measuring now reports the\n"
            "disk contention, not the model: the same profile has read four times\n"
            "slower under a pull, and the number would be stored as if it were real.\n"
            "Wait for the pull to finish, or pass --anyway if you know what you are\n"
            "measuring.")

    cond = server_conditions()
    print(f"This shell: flash_attention={cond['flash_attention']}, "
          f"kv_cache={cond['kv_cache_type']}, keep_alive={cond['keep_alive']}")
    print("  (Ollama does not report its own configuration, so these match the server")
    print("   only if it inherited this environment. See references/setup.md step 6.)")
    print()
    print(f"{'PROFILE':<8}{'MODEL':<14}{'LOAD':>7}{'TOK/S':>8}{'IN VRAM':>9}{'TOOLS':>7}  STATUS")
    print("-" * 72)

    rows, issues = [], []
    for p in wanted:
        try:
            r = measure(p)
        except oa.OllamaDown as e:
            print(f"{p:<8}{'local-'+p:<14}  no answer ({e})")
            issues.append(f"{p}: the model did not respond at all")
            continue
        rows.append(r)
        issues += [f"{r['profile']}: {i}" for i in r["issues"]]
        record(r)
        status = "ok" if not r["issues"] else ("UNUSABLE" if not r["tools"] else "check below")
        vram = f"{r['vram_pct']}%" if r["vram_pct"] is not None else "?"
        print(f"{r['profile']:<8}{r['model']:<14}{r['load_s']:>6.1f}s{r['tps']:>8.1f}"
              f"{vram:>9}{'yes' if r['tools'] else 'NO':>7}  {status}")

    print()
    if issues:
        print("Issues to report to the user:")
        for i in issues:
            print(f"  · {i}")
    else:
        print("No issues found.")

    tok = sum(r["tokens"] for r in rows)
    secs = sum(r["total_ms"] for r in rows) / 1000
    print()
    print(f"Tokens processed locally during this check: {tok} "
          f"(not charged to the Claude subscription)")
    print(f"Local processing time: {secs:.1f}s")
    print()
    print("This is a tiny probe. It confirms the local path works and gives a sense of")
    print("scale — it is not an estimate of savings, which only real tasks can show.")
    return 1 if issues else 0





if __name__ == "__main__":
    try:
        code = main()
    except BrokenPipeError:
        code = 0
    runtime.quiet_broken_pipe()
    sys.exit(code)
