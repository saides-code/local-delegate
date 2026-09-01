# local-delegate

A Claude Code plugin that hands repetitive coding work to a second Claude Code running
on local Ollama models, so it costs no subscription tokens.

## Why

Much of a coding session is chores. Chores do not need the expensive model. Your GPU is
idle.

## Requirements

- **Claude Code** — this is a plugin for it
- **Ollama 0.14.0+** — it needs the Anthropic-compatible `/v1/messages` endpoint
- **Python 3.9+** — must be installed; standard library only, no packages to add
- **A GPU** with enough VRAM. On CPU this is slower than doing the work yourself, and
  the setup says so rather than wasting your time

Works on Windows, macOS and Linux.

## Install

```
/plugin marketplace add saides-code/local-delegate
/plugin install local-delegate@saides-code
```

The skill triggers itself, and the first run walks you through setup.

## Use

You normally just describe your work. The profile is chosen by the kind of job, never by
whichever model happens to be loaded.

| Profile | For |
| --- | --- |
| `code` | multi-file refactors, repairs with a known cause, test generation |
| `fast` | short agentic tasks, scripts, glue code, exploration |
| `text` | rewrites, translations, docstrings, changelogs, READMEs |
| `tiny` | classification, extraction, throwaway jobs |
| `vision` | screenshots and diagrams |

Underneath:

```bash
python scripts/local_agent.py queue code "Fix escape handling in tokenize()" --verify "npm test"
python scripts/local_agent.py queue text "Translate README.md to English"
python scripts/local_agent.py list
python scripts/local_agent.py flush
```

## What to delegate

| Give it | Keep it yourself |
| --- | --- |
| Reading a long file | Deciding how something should be designed |
| A repair with a known cause | A bug you have not diagnosed yet |
| Writing tests against a contract | Anything under-specified |
| Renames, translations, docs | Judgement calls of any kind |
| Sorting and extracting from bulk text | Work you would have to explain three times |

**Delegate the doing, never the deciding.**

## How it works

### It is Claude Code, not a chatbot

Ollama speaks the Anthropic API, so the local agent is Claude Code itself — same tools,
same repository, reading files and running your tests. It just thinks with a model on
your GPU.

### Verification is the exit condition

A task is finished when a command you name exits 0, not when a file is written.
`--verify "npm test"` makes the agent run it and fix what it reports; the script then
runs it again and, on failure, sends the task back with the error attached. Local tokens
are free, so that loop costs you nothing. You get verified work, or a loud statement that
verification never passed.

### One model in VRAM at a time

Swapping a large model costs about twenty seconds, so tasks are queued and run grouped by
model, heaviest first — each loads once per batch, and the last one stays resident. A
coding task is never downgraded to whichever model is already loaded.

### Every change stays attributable

The working tree must be clean before the agent writes. That is what makes the resulting
`git diff` entirely its work, reviewable in one pass and undoable with `git checkout .`.

### What it may and may not do

Inside the repository it can read, write and **run commands** — without that it cannot
verify anything, and inventing plausible APIs is its most expensive failure. Blocked
regardless: `rm`, `git commit`/`push`/`reset`/`checkout`/`stash`, package installs
(unless `--allow-installs`), `sudo`, and the network.

> **A net, not a cage.** These rules match commands by prefix and do not follow a script
> into a subprocess — and the agent may run `python`. They stop a confused model from
> doing something irreversible. They are not a security boundary. Run it on a repository
> you could afford to lose.

## Layout

```
.claude-plugin/         plugin manifest, and this repo's own marketplace
skills/local-delegate/
  SKILL.md              when to delegate — and when not to
  references/           setup.md, update.md, shortlist.md
  scripts/              the Python tools
```

## Licence

MIT.
