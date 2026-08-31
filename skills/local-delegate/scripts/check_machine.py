#!/usr/bin/env python3
"""Report this machine's specs and Ollama's state. Changes nothing.

Run this first during setup: every later decision (which models fit, which
quantisation, whether a parallel profile is even possible) follows from the
VRAM number this prints.

    python check_machine.py
"""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ollama_api as oa  # noqa: E402

GB = 1024 ** 3


def run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def cpu_name():
    s = platform.processor() or ""
    if platform.system() == "Linux" and Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if "model name" in line:
                return line.split(":", 1)[1].strip()
    if platform.system() == "Darwin":
        return run(["sysctl", "-n", "machdep.cpu.brand_string"]) or s
    if platform.system() == "Windows":
        out = run(["powershell", "-NoProfile", "-Command",
                   "(Get-CimInstance Win32_Processor).Name"])
        if out:
            return out
    return s or "unknown"


def total_ram_gb():
    if Path("/proc/meminfo").exists():
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / (1024 ** 2))
    if platform.system() == "Darwin":
        out = run(["sysctl", "-n", "hw.memsize"])
        return round(int(out) / GB) if out.isdigit() else None
    if platform.system() == "Windows":
        out = run(["powershell", "-NoProfile", "-Command",
                   "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        return round(int(out) / GB) if out.strip().isdigit() else None
    return None


def gpus():
    """NVIDIA and AMD both, because the VRAM number drives every later choice."""
    out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
               "--format=csv,noheader,nounits"])
    found = []
    for line in filter(None, (l.strip() for l in out.splitlines())):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            found.append({"name": parts[0], "vram_gb": round(int(parts[1]) / 1024, 1),
                          "vram_used_gb": round(int(parts[2]) / 1024, 1),
                          "driver": parts[3] if len(parts) > 3 else ""})
    if found:
        return found
    if run(["rocm-smi", "--showmeminfo", "vram"]):
        return [{"name": "AMD GPU (rocm-smi present)", "vram_gb": None}]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return [{"name": "Apple Silicon (unified memory)", "vram_gb": None}]
    return []


def models_dir():
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env)
    if platform.system() == "Windows":
        base = os.environ.get("USERPROFILE", str(Path.home()))
        return Path(base) / ".ollama" / "models"
    return Path.home() / ".ollama" / "models"


def dir_size_gb(p):
    if not p.exists():
        return 0.0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return round(total / GB, 1)


def ollama_binary():
    """On Windows it is usually installed but missing from the shell's PATH.

    Reporting 'not installed' when it is merely invisible sends the user off to
    reinstall on top of a working install, so look in the usual places too.
    """
    found = shutil.which("ollama")
    if found:
        return found, True
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    for c in [home / "AppData/Local/Programs/Ollama/ollama.exe",
              Path("C:/Program Files/Ollama/ollama.exe"),
              Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama")]:
        if c.exists():
            return str(c), False
    return None, False


def collect():
    binary, on_path = ollama_binary()
    md = models_dir()
    try:
        du = shutil.disk_usage(md if md.exists() else Path.home())
        disk = {"free_gb": round(du.free / GB, 1), "total_gb": round(du.total / GB, 1)}
    except OSError:
        disk = {}

    info = {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_name(),
        "cpu_threads": os.cpu_count(),
        "ram_gb": total_ram_gb(),
        "gpus": gpus(),
        "models_dir": str(md),
        "models_dir_size_gb": dir_size_gb(md),
        "disk": disk,
        "ollama_binary": binary,
        "ollama_on_path": on_path,
        "ollama_running": oa.is_up(),
        "installed_models": [],
        "loaded_models": [],
    }
    if info["ollama_running"]:
        info["installed_models"] = oa.installed_models()
        info["loaded_models"] = [m.get("name") or m.get("model") for m in oa.loaded_models()]
    return info


def human(i):
    out = ["=== MACHINE ===",
           f"OS:      {i['os']}",
           f"CPU:     {i['cpu']} ({i['cpu_threads']} threads)",
           f"RAM:     {i['ram_gb']} GB" if i["ram_gb"] else "RAM:     unknown"]
    out.append("")
    out.append("=== GPU ===")
    if i["gpus"]:
        for g in i["gpus"]:
            v = f"{g['vram_gb']} GB VRAM" if g.get("vram_gb") else "VRAM unknown"
            used = f", {g['vram_used_gb']} GB in use" if g.get("vram_used_gb") is not None else ""
            out.append(f"{g['name']} — {v}{used}")
    else:
        out.append("No NVIDIA/AMD GPU detected — models would run on CPU, which is far too slow.")
    out += ["", "=== DISK ==="]
    out.append(f"Models directory: {i['models_dir']}  ({i['models_dir_size_gb']} GB used)")
    if i["disk"]:
        out.append(f"Free on that volume: {i['disk']['free_gb']} GB of {i['disk']['total_gb']} GB")
    out += ["", "=== OLLAMA ==="]
    if i["ollama_binary"]:
        note = "" if i["ollama_on_path"] else "  (installed but NOT on this shell's PATH)"
        out.append(f"Binary:  {i['ollama_binary']}{note}")
    else:
        out.append("Binary:  not found on PATH nor in the usual install locations")
    out.append(f"Running: {'yes' if i['ollama_running'] else 'no'}")
    if i["installed_models"]:
        out.append(f"Installed models ({len(i['installed_models'])}):")
        out += [f"  · {m}" for m in i["installed_models"]]
    elif i["ollama_running"]:
        out.append("Installed models: none")
    if i["loaded_models"]:
        out.append(f"Currently in memory: {', '.join(i['loaded_models'])}")
    return "\n".join(out)


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
        data = collect()
        print(json.dumps(data, indent=2) if "--json" in sys.argv else human(data))
    except BrokenPipeError:
        pass
    _quiet_broken_pipe()
