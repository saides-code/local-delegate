#!/usr/bin/env python3
"""Check that the plugin is installable and that its metadata agrees with itself.

Cheap to run, and it catches the failures that are expensive to discover after a
release: a renamed skill directory, a version bumped in one manifest and not the
other, a `source` pointing at a directory that is not there any more.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
errors = []


def fail(msg):
    errors.append(msg)


def load(rel):
    p = ROOT / rel
    if not p.is_file():
        fail(f"{rel} is missing")
        return None
    raw = p.read_bytes()
    # PowerShell's `Set-Content -Encoding utf8` writes a BOM, and json.loads rejects
    # one outright. Easy to introduce from Windows and invisible in every editor.
    if raw.startswith(b"\xef\xbb\xbf"):
        fail(f"{rel} starts with a UTF-8 BOM; write it as UTF-8 without one")
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        fail(f"{rel} is not valid JSON: {e}")
        return None


plugin = load(".claude-plugin/plugin.json")
market = load(".claude-plugin/marketplace.json")

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

if plugin:
    if not KEBAB.match(plugin.get("name", "")):
        fail(f"plugin name {plugin.get('name')!r} is not kebab-case")
    if "version" not in plugin:
        fail("plugin.json has no version")

if market:
    for field in ("name", "owner", "plugins"):
        if field not in market:
            fail(f"marketplace.json has no {field!r}")
    if not KEBAB.match(market.get("name", "")):
        fail(f"marketplace name {market.get('name')!r} is not kebab-case")
    if not isinstance(market.get("owner"), dict) or "name" not in market.get("owner", {}):
        fail("marketplace owner needs a name")
    for entry in market.get("plugins", []):
        src = entry.get("source")
        if isinstance(src, str):
            if not src.startswith("./"):
                fail(f"source {src!r} must start with './'")
            elif not (ROOT / src).is_dir():
                fail(f"source {src!r} does not resolve to a directory")
        if plugin and entry.get("name") == plugin.get("name"):
            if entry.get("version") != plugin.get("version"):
                fail(f"version drift: marketplace {entry.get('version')!r} "
                     f"vs plugin.json {plugin.get('version')!r}")

# Every skill must carry frontmatter with a name and a description: that description
# is the only thing Claude sees when deciding whether the skill is relevant.
skills = sorted((ROOT / "skills").glob("*/SKILL.md")) if (ROOT / "skills").is_dir() else []
if not skills:
    fail("no skills/*/SKILL.md found")

for skill in skills:
    rel = skill.relative_to(ROOT).as_posix()
    text = skill.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        fail(f"{rel} has no YAML frontmatter")
        continue
    fm = m.group(1)
    name = re.search(r"^name:\s*(\S+)", fm, re.M)
    if not name:
        fail(f"{rel} frontmatter has no name")
    elif not KEBAB.match(name.group(1)):
        fail(f"{rel} name {name.group(1)!r} is not kebab-case")
    if not re.search(r"^description:", fm, re.M):
        fail(f"{rel} frontmatter has no description")
    # Referenced files have to exist, or the skill sends Claude to a dead path.
    for ref in re.findall(r"`(references/[\w./-]+|scripts/[\w./-]+)`", text):
        if not (skill.parent / ref).exists():
            fail(f"{rel} references {ref!r}, which does not exist")

# The setup measures the machine, so the instructions must not assume one: a hardcoded
# GPU or VRAM figure in the procedure sends someone else down a path chosen for
# hardware they do not have.
#
# shortlist.md is exempt by design. It is organised *by* VRAM tier, and its
# measurements are cited with the card they were taken on -- that attribution is the
# point, not a leak. Everything else describes what to do, not what you have.
HARDWARE = re.compile(r"RTX [0-9]{4}|GTX [0-9]{4}|\b(?:8|12|16|24|32) ?GB VRAM")
EXEMPT = {"shortlist.md"}
for doc in sorted((ROOT / "skills").rglob("*.md")):
    if doc.name in EXEMPT:
        continue
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if HARDWARE.search(line):
            rel = doc.relative_to(ROOT).as_posix()
            fail(f"{rel}:{n} names specific hardware; check_machine.py detects it instead")

# Runtime state is machine-specific and must never be committed.
tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
for path in tracked.stdout.splitlines():
    if re.search(r"(^|/)\.local-delegate/|__pycache__|\.pyc$", path):
        fail(f"{path} is tracked but is runtime state; see .gitignore")

if errors:
    print("FAILED")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"OK - {len(skills)} skill(s), manifests consistent")
