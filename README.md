# local-delegate

A Claude Code plugin that hands the mechanical parts of a job to a **local agent** —
Claude Code itself, running on Ollama — instead of spending subscription tokens on them.

Since Ollama speaks the Anthropic Messages API, the local agent is not a stripped-down
chatbot on the side. It is a full Claude Code session with the same tools, in the same
repository, reading files, editing them and running your tests. It just runs on a model
on your own GPU.

## Install

```
/plugin marketplace add saides-code/local-delegate
/plugin install local-delegate@saides-code
```

Then, in any repository, ask for something worth delegating — or say *"give it to the
local one"*. The skill triggers on its own, and the first run walks you through setup.

<details>
<summary>Without the marketplace</summary>

Copy `skills/local-delegate/` into `~/.claude/skills/` (user-wide) or `.claude/skills/`
(one project), or download `local-delegate.skill` from
[Releases](https://github.com/saides-code/local-delegate/releases). The skill behaves
identically; you just do not get updates.

</details>

## Requirements

- **[Ollama](https://ollama.com) 0.14.0+** — it needs the Anthropic-compatible
  `/v1/messages` endpoint, added in January 2026
- **Claude Code**
- **Python 3.9+** — standard library only, no dependencies, no virtualenv
- **A GPU.** Without one the models fall back to CPU and delegating is slower than doing
  the work yourself. The setup says so rather than installing anything.

Works on Linux, macOS and Windows. On Windows the scripts run from PowerShell or cmd —
no POSIX shell needed.

## What it actually does

**Decides what to delegate, and what not to.** Mechanical refactors, test generation,
rewrites, translations, extraction from long files go out. Architectural decisions, bugs
whose cause is still unknown, and under-specified work stay in — a local model does not
ask for clarification, it guesses.

**Batches by model.** Tasks are queued and run grouped, heaviest model first, so each
model loads once per batch instead of once per task. On a single card that is the
difference between seconds of overhead and minutes.

**Never trades coding quality for speed.** A coding task is never downgraded to whichever
model happens to be resident, even when that would save a reload — including when the
user asks for it. Reloading costs seconds; a refactor done by the wrong model costs the
review plus the redo.

**Verifies its own work, locally.** See below — this is the part that decides whether
delegating pays for itself at all.

## Verification, not drafts

A delegation is finished when a command you name exits 0, not when a file is written:

```bash
python scripts/local_agent.py queue code "Fix escape handling in tokenize()" --verify "npm test"
```

The agent is told to run it and fix what it reports; then the script runs it again, and
on failure sends the task back to the local model with the error attached — three times
by default. All of that costs local tokens, which are free.

Without it you get a draft, and reviewing a draft costs attention from exactly the model
this plugin exists to spare. With it you get either verified work or a loud, explicit
statement that verification never passed. Both are useful. A silent draft is not.

Pick something that actually fails when the work is wrong — `pytest -q`, `npm test`,
`python -c "import mymodule"`. A type check alone will not catch an invented API.

## Setup: it researches, it does not assume

There is deliberately **no hardcoded model list**. Such a list is wrong within months:
tags change size, new generations ship, and vendors revise their recommended sampling
parameters. Instead the setup measures your machine and researches candidates against
rules that do hold:

- **No tool calling, no use.** Every profile drives Claude Code, which cannot read or
  write a file without function calling. A model that is excellent but lacks tool support
  is not a compromise here — it is inert.
- **Q4_K_M is the floor.** Below it, tool-call reliability degrades before conversational
  quality does: the model sounds sharp and gets the calls wrong.
- **Read sizes from the individual tag page**, never from a round-up guide.
- **Look up the sampling parameters.** Several families officially want temperature 1.0.
  A wrong value degrades output silently, with no error to notice.
- **Q8 on a small model loses** to Q4 on a bigger one: you are paying VRAM for parameters,
  not decimal places.

It also works out which capabilities *you* need — from the project it was invoked in, and
by asking — so it does not spend VRAM on strengths you will never use.

For the common case there is a fast path: `references/shortlist.md` holds a dated,
known-good set per VRAM tier, so an ordinary machine can be set up with one approval
instead of a research pass per candidate. It expires on purpose — past six months the
deep path runs instead, because a stale shortlist reads as authoritative.

## The profiles

| Profile | For |
|---|---|
| `code` | multi-file refactors, repairs with a known cause, test generation |
| `fast` | short agentic tasks, scripts, glue code, exploring the repo |
| `text` | rewrites, translations, docstrings, changelogs, READMEs |
| `tiny` | classification, extraction, throwaway tasks — the only one run in parallel |
| `vision` | screenshots, diagrams, anything the other four cannot see |

Which model backs each is decided per machine. `--as <name>` builds a challenger
alongside the incumbent, so two candidates can be measured before one is kept.

## Everyday use

You do not normally run the scripts by hand — you describe the work and the skill
triggers. Underneath:

```bash
python scripts/local_agent.py queue code "In src/parser.ts, tokenize() ..." --verify "npm test"
python scripts/local_agent.py queue text "Translate README.md to English ..."
python scripts/local_agent.py list
python scripts/local_agent.py flush     # code → fast → text → tiny, one load per group
```

## Safety rails

- The working tree **must be clean** before a run, so the resulting `git diff` is entirely
  the local agent's and can be discarded with one `git checkout .`.
  `--baseline <commit>` lets you iterate on the agent's own uncommitted work instead of
  having to commit code you have just judged defective.
- Inside the repository the agent has full authority, **including running commands**.
  Without that it cannot verify anything, which was the original failure mode: it wrote
  code, could not run an import check, and invented library APIs that survived type
  checking and failed at runtime.
- Blocked regardless of mode: `rm`, `git commit`/`push`/`reset`/`checkout`/`stash`,
  package installs (unless `--allow-installs`), `sudo`, and the network. Directories
  added with `--add-dir` are readable but explicitly not writable.
- Every refusal exits non-zero, so a script or a background caller cannot read one as
  success.

> **The deny list is a net, not a sandbox.** Bash rules match by command prefix, and
> Claude Code's file rules do not reach subprocesses that open files themselves — and the
> agent may run `python`. It stops a confused model from doing something irreversible; it
> does not stop a determined one. For real isolation, use an OS-level sandbox.

## What it writes, and what travels

In each repository you use it, under `.local-delegate/`:

| File | Travels to another machine? |
|---|---|
| `project.md` — what to delegate here, what never to, conventions | **yes** — commit it |
| `config.json` — model tags, parameters, measurements | no |
| `*.log`, `queue.jsonl`, `sessions.json` | no |

`project.md` is the accumulated understanding of the project: it is what makes a second
machine, or a colleague, start where you left off. `config.json` names tags that depend on
what is installed on one box and holds speeds measured on one GPU, so it stays put.

At the end of setup the skill shows you those lines and offers to add them to `.gitignore`.
It never edits that file without asking, and it skips the subject entirely when the project
is not a git repository.

## Scripts

All Python 3, standard library only, each runnable on its own:

| Script | Does |
|---|---|
| `check_machine.py` | CPU, RAM, GPU and VRAM, disk, Ollama state, installed models |
| `make_profile.py` | builds a `local-<profile>` model with an explicit context and parameters |
| `selfcheck.py` | per profile: load time, tok/s, how much sits in VRAM, whether tool calling answers |
| `local_agent.py` | the engine — `run`, `queue`, `flush`, `warm`, `unload`, `gitignore` |
| `backup_models.py` | writes a restore report before you remove any model |
| `config.py` | what is configured, why, when it was last reviewed, where it lives |
| `runtime.py` | console encoding and launcher discovery, shared by the rest |

`selfcheck.py` is worth knowing about: it catches the failures a model card cannot warn you
about — a model silently running on CPU, a broken chat template, tool calls that never fire
— in about two minutes. It refuses to measure while a download is still writing, because
the same model reads four times slower under disk contention and the number would be
recorded as if it were real.

## Layout

```
.claude-plugin/         plugin manifest, and the repo's own single-plugin marketplace
skills/local-delegate/
  SKILL.md              when to delegate, how to batch, how to verify
  references/           setup.md, update.md, shortlist.md
  scripts/              the Python tools above
tests/                  fake Ollama + fake launcher; the scripts run as real subprocesses
```

## Contributing

`.github/workflows/validate.yml` runs on every push, on Linux **and** Windows — the
encoding guard, the launcher discovery and the command-line limits all exist because of
failures that only appear there. It checks that the scripts compile, the manifests and
frontmatter are valid and agree on a version, referenced paths exist, no runtime files are
committed, and **no hardware assumptions appear in the instructions** — the setup measures
the machine, so a hardcoded GPU or VRAM figure would send someone else down the wrong path.

Then it runs the tests: `python -m unittest discover -s tests`.

## Releasing

```bash
git tag v1.1.0 && git push --tags
```

CI builds `local-delegate.skill` and attaches it to the release.

## Licence

MIT.
