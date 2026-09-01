---
name: local-delegate
description: >-
  Hand the mechanical parts of a job to a local agent (Claude Code running on Ollama) instead of
  spending subscription tokens on them. This is not a separate mode, it is a reflex to use INSIDE
  any other work — reach for it while you are already coding, following another skill, writing
  documentation or investigating a bug, whenever a repetitive, well-scoped or objectively
  verifiable piece comes up, such as mechanical refactors, test generation, rewrites, translations,
  docstrings, changelogs, comments, classification, extraction from long files and first drafts.
  Use it whenever the user lists several things to do in a row, because those should be queued and
  grouped by model rather than run one at a time, and whenever they say "give it to the local one",
  "let Ollama do it", "delegate", "use the local model", "save tokens" or "do not waste context".
  When unsure, open it and read — it holds both the criteria for delegating and the equally
  important ones for not doing so.
---

# Delegate to a local model

Runs a second Claude Code on local models through Ollama, in the same repository and with the same
tools. It costs no subscription tokens.

**One model in VRAM at a time** — every rule below follows from that single constraint. Which models
fit is decided during setup by measuring the actual machine, so nothing here assumes a particular GPU.
`python scripts/config.py show` prints what was chosen and why.

## First time on this machine?

If the `local-*` models do not exist yet, or the user wants to reconsider which models to use,
**read `references/setup.md` and follow it**.

It opens with a choice, and the choice matters: the full research pass is expensive and runs on *this*
model, which is the one the skill exists to spare. Offer the fast path first — detect the GPU, take a
set from `references/shortlist.md`, install, verify — and keep the nine-step version for unusual
hardware or a stale shortlist.

Do not improvise a model list from memory. Tags change size, new generations ship, and vendor
recommendations get revised — that procedure exists because the choice has to be made with today's
data, not with what you remember.

## The skill's folder

Everything lives in `.local-delegate/` in the repository, because what a project needs differs from
project to project: one repo wants a strong coder, another wants translation quality. Outside a git
repository it falls back to `~/.local-delegate/`.

| File | What | Commit? |
|---|---|---|
| `project.md` | what to delegate here, what never to, conventions | yes |
| `config.json` | model tags, parameters, measurements | **no — machine only** |
| `*.log`, `queue.jsonl` | transient | **no** |

`config.json` is machine-exclusive and must be told to the user as such. The tags it names depend on
what is installed on that box and on how much VRAM it has, and the measurements come from that GPU —
none of it transfers. Only `project.md` is portable.

`python scripts/config.py where` prints this for the repo you are in — run it rather than guessing.

**Read `project.md` before your first delegation in a repository that has one.** It is where a previous
session recorded what worked here and what burned them.

When the user asks whether better models exist now, or `config.py show` says the last look was over a
month ago, follow `references/update.md`.

## Git

Only if the project is a git repository — outside one there is nothing to do, and the scripts say so
rather than inventing work.

`project.md` is the part that travels: it records what this project delegates, and it stays useful on
any machine. Everything else under `.local-delegate/` describes one box and must not be committed.

At the end of setup, and the first time you delegate in a repository that has not been set up yet,
show the user the lines and offer to write them:

```bash
python scripts/config.py gitignore-hint     # the lines, or "already covered"
python scripts/local_agent.py gitignore     # writes them, on their go-ahead
```

Write them yourself only when the user agrees — `.gitignore` is their file. If they decline, drop it:
nothing breaks, the untracked files simply show up in `git status`.

## Finding the scripts

They live in `scripts/`, next to this SKILL.md. Use the path you loaded this skill from rather than
a fixed one: the skill may be installed as a user skill, inside a plugin, or inside a project, and a
hardcoded path breaks.

```bash
# bash / zsh
LA="<this skill's directory>/scripts/local_agent.py"
python "$LA" ps

# PowerShell
$LA = "<this skill's directory>\scripts\local_agent.py"
python $LA ps
```

The `$LA` shorthand is used below for brevity; substitute the full path if your shell
does not do variables the same way.

Installed as a plugin, that directory is `${CLAUDE_PLUGIN_ROOT}/skills/local-delegate` —
the variable is set for you, and it beats writing out an install path that carries a
version number in it.

They are Python 3, standard library only — no dependencies, and they run from PowerShell, cmd or any
shell without needing a POSIX environment.

If the launcher cannot be found, `LOCAL_AGENT_CLAUDE_BIN` names the command that starts
Claude Code, quoted as you would in a shell — `"C:\path\claude.exe"`, or
`"node" "C:\path\cli.js"` for an npm install. Prefer a real executable over a `.cmd` or
`.bat` shim: those run through `cmd.exe`, which truncates every argument at its first
newline, and the prompt carrying the task and the project rules is very much
multi-line.

## 1. Do not burn in preparation the tokens you are saving

This is the mistake that defeats the whole point: reading half the repository to write a perfect
prompt costs more than the delegated task. To prepare a delegation, **locate, do not read**. A grep,
an ls, a glance at a few lines around the right spot is enough. The local agent reads the file
contents itself, on its own context, at no cost to you.

If you notice you have opened three whole files just to work out what to delegate, the task probably
was not worth delegating: do it yourself, you are already inside the problem.

## 2. Delegate or not

**Delegate** when the task is scoped to files you have already identified; mechanical or repetitive;
objectively verifiable (tests pass, the diff reads clearly); or long to read and short to think about.

**Do not delegate** when the task is an architectural decision; a bug whose cause you have not found
yet; or under-specified — a local model does not ask for clarification, it guesses.

When in doubt, do not delegate: reviewing a local model's wrong output costs more than doing the task
directly.

Be careful not to let that list grow. An exclusion applied broadly collapses delegation altogether and
puts you back to doing everything yourself, which is this skill's silent failure mode. In particular
**the subject matter of a file is not a criterion**. A mechanical refactor in an authentication file,
with precise instructions and tests covering it, delegates like any other. What does not delegate is
*deciding* how to protect something: choosing a token scheme, a permission model, where to validate.
The line is always the same — who makes the decision, not which file gets touched.

**The thing being asked for is not exempt.** The examples above are all side-pieces
breaking off some larger work, and that framing quietly suggests the main artefact stays
yours. It does not. `text` exists for documents, and a README, a changelog or a
translation is usually the deliverable itself. Deciding the structure is your judgement;
filling it in against a structure you have already decided is a `text` task, however
important the document is.

Watch for the excuses, because they are always the same five, and each one sounds
reasonable in the moment:

> *it's the deliverable · it needs judgement · I'm already in the file · specifying it
> costs as much as writing it · the quality bar is too high*

The first two are almost never true once the outline exists. The third and fourth are
sometimes true — that is §1, and it is a real limit, not a loophole. The fifth is never
true: a bar you can state is a bar you can put in the prompt, and a bar you cannot state
was not a bar.

A usable threshold: **a whole document, or more than about fifty lines written to a
structure you have already settled, is a `text` task.** Below that, §1 wins and you write
it yourself. If you notice you are drafting something long and have not asked which
profile it belongs to, you have already made the mistake.

The most common borderline case is a bug. **Diagnosis and repair separate**: you find the cause, the
mechanical repair that follows gets delegated. "Login times out sometimes" does not delegate. "In
`src/auth.ts:42` the error branch is missing its `clearTimeout`, add it and cover the case with a
test" does.

## 3. Composing with work already underway

This is not a mode you enter and leave. It is a reflex that lives inside ordinary work: while you
write a feature, while you follow another skill, while you investigate a problem, a mechanical piece
occasionally breaks off. That piece goes in the queue and you carry on without changing the subject.

Do not announce that you are "entering delegation mode", do not stop what you are doing to open a
parenthesis about delegating, do not ask permission for each individual task: queue it and move on.
You will report all of it together at flush time.

**When another skill is active**, the split is clean: the other skill decides *what* to do and to what
quality bar, this one decides *who executes* the mechanical parts that follow. Never delegate the
piece that needs the other skill's judgement — the design choice, the document's structure, the site's
architecture — because that is exactly what that skill exists for. Delegate what is left once the
decision is made.

Some recurring shapes:

- building a site, structure already decided → alt text, page translations and repetitive components
  go in the queue
- working on a document, needing figures out of long files → extraction goes to `tiny` or `fast`, the
  writing stays yours
- finished diagnosing a bug → the repair and its tests go to `code`, and this is where delegation pays
  off most
- the user asks for three things in a row → all queued, one single flush

**Before ending a stretch of work, check the queue.** If something is still in it, either flush it or
say so explicitly. Leaving tasks queued without mentioning them is the worst way to finish: the user
believes they are done.

There is an opposite case too, and it deserves recognising: if the mechanical piece takes two minutes
and you are already inside that file with the context loaded, doing it is cheaper than describing it
to someone else. See §1 — delegation has a fixed preparation cost, and below a certain size it does
not pay for itself.

## 4. The four profiles

| Profile | Model | For |
|---|---|---|
| `code` | the coding model, long context | multi-file refactors, repairs with a known cause, test generation |
| `fast` | the quick agentic model | short agentic tasks, scripts, glue code, exploring the repo |
| `text` | the language model | rewrites, translations, docstrings, comments, changelogs, READMEs |
| `tiny` | the smallest one | classification, structured extraction, throwaway tasks, the only one that runs in parallel |

Which models actually back these profiles is decided during setup and may differ from machine to
machine — `python scripts/selfcheck.py` prints the current mapping. The profile is chosen by **the
nature of the task**, never by which model happens to be loaded.

## 5. Queue, then flush

Evicting and reloading a model costs tens of seconds. Tasks are queued and executed in
a batch, grouped by model.

```bash
python "$LA" queue code "..." --verify "npm test"
python "$LA" queue text "..."
python "$LA" list
python "$LA" flush
```

`flush` goes heaviest to lightest, loading each model once. A model is evicted only
when the next group needs a different one, and the last one stays resident after the
flush — the next task is usually for the same profile, and reloading a large coder is
the dominant cost of delegating at all. `unload` frees the card when you actually want
it free.

Use `run` for a genuinely isolated task.

## 5b. Give it a verification command

`--verify "<command>"` is the single most valuable thing you can add to a delegation.
It changes the contract: the task is not finished when a file is written, it is
finished when that command exits 0.

The agent is told to run it and fix what it reports. Then the script runs it too, and
on failure sends the task back to the local model with the error attached — up to
three times by default, `--attempts` to change it. All of that costs local tokens,
which are free.

Without it you receive a draft and pay expensive-model attention to review it. With it
you receive either verified work or an explicit, loud statement that verification never
passed. Both are useful; a silent draft is not.

Pick something that actually fails when the work is wrong: `npm test`, `pytest -q`,
`python -c "import mymodule"`, `npx tsc --noEmit`. A type check alone will not catch a
function that was invented.

On Windows the command runs in PowerShell, not `cmd.exe`. Windows PowerShell 5.1 has no
`&&`, so chain with `;` — or set `LOCAL_AGENT_VERIFY_SHELL=cmd` for that run.

## 5c. What the local agent may do

Inside the working directory it has the authority the operator already granted this
session: read, write, **and run commands**, with no prompts — nobody is there to answer
one. That last one is the point. An agent that cannot run `python -c "import x"` cannot
find out whether the API it just called exists, and inventing plausible APIs is the
most expensive mistake it can make, because it survives type checks and lint and fails
only at runtime.

Blocked at every level, regardless of mode: commits, pushes, resets and checkouts,
package installs, network fetches, `rm`, `sudo`. Claude Code evaluates deny rules
before any permission mode — deny, then ask, then allow — so these hold.

Two different reasons sit in that list, and they are worth keeping apart. `rm` and the
git verbs that rewrite the working tree are blocked because they **destroy
attributability**: the whole safety story is that the tree was clean, so the diff is
the agent's and `git checkout .` undoes it. An agent that can stash or reset erases the
work and the evidence together. Push, `sudo` and the network are blocked because they
**reach outside the tree**.

**Be honest with the user about what this is: a net, not a cage.** Bash rules match by
command prefix, and Claude Code's file rules explicitly do not reach subprocesses that
open files themselves. The agent may run `python`, so a determined one walks straight
past every rule. This stops a confused model from doing something irreversible; only an
OS-level sandbox stops a malicious one. Do not describe it as a security boundary.

`--allow-installs` is the one deliberate exception, off by default. Without it a task
that has to add a dependency before its tests can pass fails at the install step, and
the retry loop spends all three attempts on a failure that was never the model's fault.
Whether an agent may touch the lockfile is a project decision — turn it on for that
task, not globally.

`--ro` switches to read-only: reads and read-only shell commands, no edits. Use it for
every analysis, which is also where delegation pays best.

`--add-dir <path>` lets it read something outside the repo — a library's source under
`site-packages` is often exactly what settles a question about an API. Writing there is
denied explicitly, because `--add-dir` on its own follows the current permission mode
and would otherwise grant edits outside the repository.

## 6. The rules that protect coding quality

These come before any speed optimisation. Batching exists to recover dead time, not to change which
model does what.

1. **Never two models in VRAM at once.** Preloading the small model while the coder is running steals
   its VRAM and pushes more of it into system RAM: slower coding *and* less usable context. If you use
   `warm`, it always comes after `unload`, never before.
2. **Never downgrade a coding task to whatever is already loaded.** If a `code` task appears once you
   have moved on to the light model, put it back in the `code` queue and do another round. This holds
   especially when the user is the one suggesting it to save time: explain that the light model on a
   refactor produces a diff worth throwing away, and that reloading costs less than redoing.
3. **One model in VRAM at a time.** Two resident models on one card means extra
   offload for both, which slows the coding model down for no gain.

## 7. Write the prompt long, not short

A local model will not find the right spot in a large repository on its own. Give it
**the path, the function, the expected behaviour, and the command that has to go green**.

Do not write terse prompts to save tokens — the local model's tokens are free, and a
vague prompt is paid for later in expensive-model review. Spell out the contract, the
edge cases, and what must not change. Name the file that already does the same thing
correctly, if there is one: "copy the exact form used in `backend/reader.py`" prevents
an invented API far better than any instruction to be careful.

Split a task with more than three independent steps: tool-calling reliability compounds
downward along a loop, so two three-step tasks succeed far more often than one six-step
task.

Project rules load themselves. `AGENTS.md`, `CLAUDE.md` and `.local-delegate/project.md`
are read from the repository and appended to every delegation, so conventions the
project cares about reach the local agent without being pasted by hand.

Related tasks share a session per profile, so a second task can see what the first one
built. `--fresh` starts a clean one when the context is no longer relevant.

## 8. Worked example

The user says: *"I need to fix the escape-sequence bug in the parser, we need tests for it, and while
you're at it the README is still in Italian, put it in English."*

```bash
LA="<this skill's directory>/scripts/local_agent.py"
git status --porcelain              # must be empty, see §9
grep -n "inString" src/parser.ts    # locate, do not read the whole file

python "$LA" queue code "In src/parser.ts, tokenize() does not handle escape sequences inside strings: a backslash-quote closes the string instead of being literal. Fix it, keeping the signature and the behaviour outside strings unchanged."
python "$LA" queue code "In tests/parser.test.ts add cases for strings with escapes. 'npm test' must pass."
python "$LA" queue text "Translate README.md from Italian to English, keeping the structure and leaving code blocks, commands and identifiers untouched."
python "$LA" list
python "$LA" flush
```

One flush: the two `code` tasks run back to back with the coder loaded, then it is evicted and the
language model comes up for the README. Two loads instead of three.

Then the verification in §10, and the report to the user.

## 9. The working tree must be clean

The script refuses to start if there are uncommitted changes. This is not fussiness: it is what makes
every line of the resulting `git diff` attributable to the local agent, and throwable away with a
single `git checkout .`.

If the tree is dirty, do not work around the check. Tell the user and offer to commit or stash — the
decision is theirs, they are their changes.

To iterate on a delegation you already started — which is the normal case, since the
first pass is rarely right — pass `--baseline <commit>`. The changes are then treated
as this delegation's own work and remain attributable to that commit, so you can send a
correction back instead of having to commit code you have just judged defective.

Every refusal exits non-zero. A caller that only checks the status will not read one as
success.

## 10. Always verify

A local agent's output is never accepted unseen:

1. Read the whole diff. `flush` prints it including newly created files, which a plain
   `git diff` would omit — and a new module is the most common shape of a delegated task.
2. Run the test suite.
3. Read the agent's closing summary: it states what it could **not** do, and whether the
   verification command passed. If it did not, the work is not confirmed, however
   confident the summary sounds.
4. If the diff is out of scope or confused, `git checkout .` and do the task yourself. Do not hand-patch
   the work of a model that misunderstood — it costs more than redoing it.

Close by telling the user, in a few lines: what you delegated and to which profile, what you kept and
why, the test results, and what is still open.

## 11. When something goes wrong

These are configuration failures, not model failures. Do not work around them and do not pretend the
task was done.

| Message | What to do |
|---|---|
| `Ollama is not answering` | Ollama is not running: say so, do not delegate |
| `Model 'local-*' is missing` | Setup has not been run: see `references/setup.md` |
| `Working tree is dirty` | See §9 |
| `claude is not on PATH` | Claude Code is what drives the local model; it must be installed |
| A profile behaves worse than it used to | `python scripts/config.py show` has the previous measurements to compare against |
| The agent runs but produces nonsense | Run `python scripts/selfcheck.py`: usually truncated context or CPU offload, and it shows up there in two minutes |

If delegation is not possible, do the task normally and say so in one line: the user asked for the
work, not for the delegation.

Whenever something is slow or strange and you cannot tell why, `scripts/selfcheck.py` answers in a
couple of minutes — per profile it reports real speed, how much of the model sits in VRAM, and whether
tool calling works. Two minutes of measurement beats half an hour of guessing.
