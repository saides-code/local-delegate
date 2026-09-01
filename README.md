# local-delegate

**Your GPU is sitting idle. Put it to work on the boring half of your coding.**

A plugin for [Claude Code](https://claude.com/claude-code) that quietly hands the
repetitive parts of a job to a second AI running on your own computer — so they cost
you nothing, and your paid usage goes on the work that actually needs it.

---

## The problem, in plain terms

When you work with Claude, you are paying for its attention — by subscription limit or
by token. But a large part of any coding session isn't clever. It's *chores*:

- rename this across forty files
- write the tests for the bug we just fixed
- translate the README into English
- read this 1,200-line file and tell me what's in it

None of that needs the expensive model. It needs *a* model, and a careful one.

Meanwhile there is a graphics card in your machine doing nothing, and free software
that will run a very capable AI on it. This plugin connects the two.

## What it actually does

You keep talking to Claude exactly as you do now. Behind the scenes, when a chore comes
up, Claude writes it down instead of doing it. When a few have piled up, it hands them
to the local AI, which does them on your GPU, checks its own work, and reports back.

> **You:** *"Fix the escape-sequence bug in the parser, add tests for it, and the README
> is still in Italian — put it in English."*
>
> Claude finds the bug itself (that part needs judgement), then queues the mechanical
> follow-ups: the repair, the tests, the translation. The local model does all three,
> runs `npm test` until it passes, and hands back a diff. **Cost to your plan: the
> diagnosis only.**

The trick that makes it work: Ollama speaks the same API Claude does, so the local agent
isn't a chatbot bolted on the side — **it is Claude Code**, with all its normal tools,
in your repository, reading files, editing them and running your tests. It just thinks
with a model on your GPU instead of one in a datacentre.

## What it's good at, and what it isn't

Being honest about this matters more than selling it. A local model is smaller than
Claude, and it does not ask for clarification — it guesses.

| Give it | Keep it yourself |
|---|---|
| Reading a long file and reporting what's in it | Deciding how something should be designed |
| A repair whose cause you already found | A bug you haven't diagnosed yet |
| Writing tests against a clear contract | Anything vague or under-specified |
| Renames, translations, docstrings, changelogs | Judgement calls of any kind |
| Sorting and extracting from bulk text | Work you'd have to explain three times |

The rule underneath all of it: **delegate the doing, never the deciding.**

Measured on a real session, reading tasks were the clearest win — two of them replaced
about 1,400 lines of source that would otherwise have been read into the expensive
model's memory.

---

## Install

You need three things first:

1. **[Claude Code](https://claude.com/claude-code)** — this is a plugin for it
2. **[Ollama](https://ollama.com)** 0.14.0+ — the free program that runs AI on your GPU
3. **A graphics card.** Without one everything falls back to the CPU and is slower than
   doing the work yourself. The setup tells you so rather than wasting your time.

Python 3.9+ is used for the plumbing — standard library only, nothing to install.
Works on Windows, macOS and Linux.

Then, in Claude Code:

```
/plugin marketplace add saides-code/local-delegate
/plugin install local-delegate@saides-code
```

## First run: it measures, it doesn't assume

The first time you ask it to do something, it sets itself up — and this part is
deliberately not a canned list of models.

Any hardcoded recommendation is wrong within months: models are replaced, download sizes
change, and vendors revise the settings their own models need. So instead it looks at
*your* machine — graphics card, memory, disk — and picks models that actually fit it,
checking each one against rules that don't go stale:

- **No tool support, no use.** The local agent has to open and edit files. A model that
  can't call tools isn't a compromise here, it's useless.
- **Don't over-compress.** Squeeze a model too hard and it still *sounds* fluent while
  quietly getting tool calls wrong — the worst possible failure.
- **Read the real numbers**, from the model's own page, not from a round-up article.
- **Use the settings the model's authors published.** The wrong temperature degrades
  output silently, with no error to tell you.

For an ordinary machine there's a fast path: a dated, known-good set per graphics-card
size, so setup is one approval instead of a research session. It expires on purpose —
after six months it does the full research again, because a stale list that *looks*
authoritative is worse than no list.

Then it measures what it installed: real speed, whether the model actually fits in the
card, whether tool calling works. Roughly two minutes, and it catches the failures a
spec sheet can't warn you about.

## Using it

Mostly you don't. You describe your work, and the skill triggers itself when a chore
appears. You can also just say *"give that to the local one"*.

It picks a profile by the **kind** of job, never by whichever model happens to be
loaded:

| Profile | For |
|---|---|
| `code` | multi-file refactors, repairs with a known cause, test generation |
| `fast` | short agentic tasks, scripts, glue code, exploring the repo |
| `text` | rewrites, translations, docstrings, changelogs, READMEs |
| `tiny` | classification, extraction, throwaway jobs |
| `vision` | screenshots and diagrams |

---

## The interesting part

### Verification is the exit condition

This is the design decision the whole thing rests on.

A delegation is **not** finished when a file is written. It's finished when a command
you named exits 0:

```bash
python scripts/local_agent.py queue code "Fix escape handling in tokenize()" \
                                         --verify "npm test"
```

The local agent is told to run it and fix what it reports. Then the script runs it
*again* — trust but verify — and on failure sends the task back to the local model with
the error attached. Three rounds by default.

All of that costs local tokens, which are free. The alternative is that you get a
plausible draft and spend expensive attention reviewing it, which defeats the point.
You get either verified work, or a loud statement that verification never passed.

Pick something that genuinely fails when the work is wrong — `pytest -q`, `npm test`,
`python -c "import mymodule"`. A type check alone will not catch an invented function.

### One model in the card at a time

Everything about the batching follows from this single constraint. Swapping a large
model in and out costs about twenty seconds, so tasks aren't run as they arrive — they
queue, and run grouped by model, heaviest first:

```bash
python scripts/local_agent.py queue code "..."   # enqueue
python scripts/local_agent.py queue text "..."   # enqueue
python scripts/local_agent.py list               # look before you leap
python scripts/local_agent.py flush              # code → fast → text → tiny
```

Each model loads exactly once per batch, and the last one **stays** loaded — evicting
the model you just warmed up is how you pay the twenty seconds twice.

The rule that outranks the optimisation: a coding task is never downgraded to whichever
model is already resident, even when that would save a reload, and even if you ask.
Reloading costs seconds. A refactor done by the wrong model costs the review plus the
redo.

### Every change stays attributable

The working tree must be clean before the agent writes anything. That's not fussiness —
it's what makes the resulting `git diff` *entirely* the agent's work, so you can read it
in one pass and throw it away with a single `git checkout .` if it's wrong.

(If you need to iterate on a delegation that already ran, `--baseline <commit>` lets you,
without having to commit code you've just judged defective.)

### What the agent may and may not do

Inside your repository it has real authority: read, write, **and run commands**. That
last one isn't a convenience — without it the agent can't run a test or check that a
function it's about to call actually exists, and inventing plausible-looking APIs is the
single most expensive mistake it can make. It survives type-checking and linting and
fails only at runtime, on your time.

Blocked regardless: `rm`, `git commit`/`push`/`reset`/`checkout`/`stash`, package
installs (unless you pass `--allow-installs`), `sudo`, and the network. Folders you
share with `--add-dir` are readable but explicitly not writable.

> **A net, not a cage.** These rules match commands by prefix and don't follow a script
> into a subprocess — and the agent is allowed to run `python`. They stop a *confused*
> model from doing something irreversible. They are not a security boundary against a
> determined one. Run it on a repository you could afford to lose, or use a real sandbox.

---

## What it leaves in your project

A `.local-delegate/` folder:

| File | Commit it? |
|---|---|
| `project.md` — what this project delegates, what it never does, house conventions | **Yes.** This is the part that travels |
| `config.json` — which models, which settings, measured speeds | No — it describes one machine |
| logs, queue, session state | No |

The skill offers to add the right lines to your `.gitignore` and never edits that file
without asking.

## The scripts

Python 3, standard library only, each runnable on its own:

| Script | Does |
|---|---|
| `check_machine.py` | CPU, RAM, GPU and VRAM, disk, Ollama state, installed models |
| `make_profile.py` | builds a `local-<profile>` model with an explicit context and settings |
| `selfcheck.py` | per profile: load time, speed, how much fits in the card, does tool calling work |
| `local_agent.py` | the engine — `run`, `queue`, `flush`, `warm`, `unload` |
| `backup_models.py` | a restore report, written before you remove anything |
| `config.py` | what's configured, why, and when it was last reviewed |
| `runtime.py` | console encoding and launcher discovery |

`selfcheck.py` earns its place: it refuses to measure while a download is still running,
because the same model reads four times slower under disk contention and the number
would otherwise be recorded as though it were real.

## Layout

```
.claude-plugin/         plugin manifest, and this repo's own marketplace
skills/local-delegate/
  SKILL.md              when to delegate — and when not to
  references/           setup.md, update.md, shortlist.md
  scripts/              the Python tools above
```

## Contributing

CI runs on every push, on Linux **and** Windows — the encoding guard, the launcher
discovery and the command-line limits all exist because of failures that only appear
there. It checks that the scripts compile, that the manifests and frontmatter agree,
that referenced paths exist, that no runtime files are committed, that nothing is
defined and never used, and that **no hardware assumptions leak into the instructions** —
the setup measures your machine, so a hardcoded card or memory figure would send someone
else down the wrong path.

The test suite lives on the [`develop`](../../tree/develop) branch, where a fake Ollama
and a fake launcher let the scripts be driven end to end.

## Licence

MIT.
