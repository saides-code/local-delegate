# Local Delegate

A Claude Code plugin that hands the mechanical parts of a job to a **second Claude Code running on
local Ollama models**, in the same repository, with the same tools — and at **no subscription-token
cost**.

It is not a mode you enter and leave. It is a reflex that lives inside ordinary work: while you write
a feature, follow another skill, or investigate a bug, a repetitive piece occasionally breaks off.
That piece goes in the queue; you carry on.

## What gets delegated

| Delegate | Keep |
|---|---|
| Mechanical refactors across files you already located | Architectural decisions |
| Test generation for a repair whose cause you found | A bug whose cause you have not found yet |
| Translations, docstrings, comments, changelogs | Anything under-specified |
| Classification and extraction from long files | Anything you would have to read three files to specify |

The line is always the same: **who makes the decision**, not which file gets touched. A mechanical
refactor inside an authentication file delegates like any other. *Choosing* the token scheme does not.

## Requirements

- **[Claude Code](https://claude.com/claude-code)** — it is what drives the local model
- **[Ollama](https://ollama.com/download)**, installed and running
- **Python 3** — standard library only, no dependencies
- A GPU with enough VRAM to hold one model at a time. How much is enough is *measured*, not assumed:
  setup photographs the machine and picks models to fit it.

Works on Windows, macOS and Linux. The scripts run from PowerShell, cmd or any POSIX shell.

## Install

```
/plugin marketplace add saides-code/local-delegate
/plugin install local-delegate@saides-code
```

Then, in any repository, just ask for something worth delegating — or say *"give it to the local
one"*. The skill triggers on its own; the first run walks you through setup.

<details>
<summary>Install without the marketplace</summary>

Copy `skills/local-delegate/` into `~/.claude/skills/` (user-wide) or `.claude/skills/` (one project).
The skill works the same; you just do not get updates.

</details>

## First run

There is no baked-in model list, deliberately. Tags change size, new generations ship, vendor
recommendations get revised — so the choice is made with today's data, on **your** machine:

1. `check_machine.py` reports OS, CPU, RAM, GPU with VRAM, free disk and Ollama's state.
2. You confirm it — automatic detection is wrong more often than people expect.
3. You pick a direction: disk space or performance.
4. Claude researches current models that fit the measured VRAM and proposes them.
5. They are installed, given tuned parameters, and verified with a real task.

`python scripts/selfcheck.py` prints, per profile, real tokens/second, how much of the model actually
sits in VRAM, and whether tool calling works. Two minutes of measurement beats half an hour of
guessing.

## The four profiles

| Profile | For |
|---|---|
| `code` | multi-file refactors, repairs with a known cause, test generation |
| `fast` | short agentic tasks, scripts, glue code, exploring the repo |
| `text` | rewrites, translations, docstrings, comments, changelogs, READMEs |
| `tiny` | classification, structured extraction, throwaway tasks — the only one that runs in parallel |

Which model backs each profile is decided at setup and differs from machine to machine. The profile is
chosen by **the nature of the task**, never by which model happens to be loaded.

## Queue, then flush

Evicting and reloading a model costs tens of seconds, so tasks are not run as they come up:

```bash
python "$LA" queue code "In src/parser.ts, tokenize() does not handle escape sequences inside strings…"
python "$LA" queue code "In tests/parser.test.ts add cases for strings with escapes. 'npm test' must pass."
python "$LA" queue text "Translate README.md from Italian to English, keeping code blocks untouched."
python "$LA" list
python "$LA" flush
```

`flush` runs `code → fast → text → tiny`, loading each model exactly once. Coding runs first, while the
card is entirely its own.

**One model in VRAM at a time.** Every rule in the skill follows from that single constraint — including
the one that matters most: *never downgrade a coding task to whatever is already loaded.* Reloading the
coder costs less than reviewing a diff worth throwing away.

## Safety rails

- The working tree **must be clean** before a run. That is what makes every line of the resulting
  `git diff` attributable to the local agent, and throwable away with one `git checkout .`.
- The local agent runs with a restricted tool set: no `rm`, no `git commit`, no `git push`, no `sudo`,
  no network.
- `run --ro` gives a read-only agent for analysis: no writes, no working tree to clean up.
- Output is never accepted unseen — read the diff, run the tests, read the agent's closing summary of
  what it could **not** do.

## Repository layout

```
.claude-plugin/
  marketplace.json          this repo is also its own single-plugin marketplace
  plugin.json               plugin manifest
skills/
  local-delegate/
    SKILL.md                when to delegate, and the equally important when not to
    references/
      setup.md              guided first-run model selection
      update.md             revisiting the choice when better models ship
    scripts/                Python 3, standard library only
      local_agent.py        run / queue / flush / warm / unload / ps
      config.py             profiles, parameters, measurements, project notes
      check_machine.py      hardware and Ollama detection
      make_profile.py       build a tuned Ollama model from a base tag
      ollama_api.py         thin Ollama HTTP client
      selfcheck.py          measure speed, VRAM residency, tool calling
      backup_models.py      save and restore the model set
```

### Two config files, deliberately

| File | What | Commit? |
|---|---|---|
| `<repo>/.local-delegate/project.md` | what this project delegates, what it never does, conventions | **yes** — it is the portable part |
| `<repo>/.local-delegate/config.json` | model tags, parameters, measurements | **no** — one machine only |
| `*.log`, `queue.jsonl` | transient | **no** |

`python scripts/config.py gitignore-hint` prints the lines; the skill offers to write them, on your
go-ahead. `.gitignore` is your file.

## License

MIT — see [LICENSE](LICENSE).
