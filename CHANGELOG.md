# Changelog

All notable changes to this plugin are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-01

The first real use of 1.0.0 produced a fault log with eighteen findings. Delegation was
measured as a net cost on writing tasks and a net win on reading tasks: five of fourteen
delegations were clean first time, and the local agent caught none of its own defects
because it could not run anything. This release is the response.

### Added

- **Verification as the exit condition.** `--verify "<cmd>"` makes the task finished
  only when that command exits 0. The agent runs it; the script runs it again; on
  failure the task goes back to the local model with the error attached, up to
  `--attempts` times. Local tokens are free, so the loop runs there instead of costing
  an expensive-model round trip.
- **Session continuity.** The session id is captured per profile and resumed, so task
  eight knows what task seven built. `--fresh` opts out.
- **Project rules travel with the task.** `AGENTS.md`, `CLAUDE.md` and `project.md` are
  loaded and appended to every delegation, instead of being pasted in by hand.
- **`--allow-installs`**, off by default: without it a task that must add a dependency
  before its tests pass fails at the install step, and burns every retry on it.
- **`--baseline <commit>`** allows a second pass over the agent's own uncommitted work,
  so iterating on a defective delegation no longer means committing it first.
- **`vision` profile** and **`--as <name>` challenger builds**, so a second candidate
  for one profile can be measured against the incumbent instead of overwriting it.
- **`LOCAL_AGENT_CLAUDE_BIN`** names the launcher command when discovery cannot find it.
- **A test suite** that drives the scripts as subprocesses against a fake Ollama and a
  fake launcher, plus CI on Linux *and* Windows.

### Fixed

- **The system prompt never reached the agent.** `GUARDRAILS` was defined and never
  passed: `--append-system-prompt` was missing from the command. Every instruction
  about spending free local tokens, and the explicit ban on calling a library API
  without verifying it exists, was dead code. This was the single most consequential
  defect in the release.
- **Full authority in the working directory.** The agent ran under `acceptEdits`, which
  accepts file edits but still prompts for Bash — and nobody is there to answer. It
  could not run a test, an import check, or anything else, so it never verified its own
  work. Now `bypassPermissions`, with the deny list intact (deny is evaluated before
  any mode).
- **`--add-dir` granted writes outside the repository.** Additional directories follow
  the current permission mode, so under `bypassPermissions` the agent could edit them.
  Matching `Edit(//path/**)` deny rules now make them genuinely read-only.
- **A flush lost every task that never started.** The queue was archived
  unconditionally, so a missing model or a dirty tree discarded the work. Tasks that
  failed before the agent ran stay queued; tasks whose agent ran are archived even on a
  non-zero exit, because re-running them would apply their edits twice.
- **The clean-tree check ran per task, not per batch.** The first task in a flush
  legitimately dirties the tree, so every task after it was refused — which is exactly
  the two-task batch the worked example tells you to queue.
- **A refusal exited 0**, indistinguishable from success to any caller checking the
  status. Every refusal now exits 2.
- **Encoding crash on Windows.** 88 unguarded non-ASCII print sites; the installer died
  on a box-drawing character before downloading anything. `runtime.fix_console()` in
  every entry point.
- **`cfgmod.CONFIG` does not exist** — every successful profile build ended in an
  `AttributeError` traceback. Now `cfgmod.config_path()`.
- **The residency check could never fire.** It compared `local-text` against the API's
  `local-text:latest`, so `vram_pct` stayed `None`, both dependent branches were dead,
  and the pass printed "No issues found" without having checked anything.
- **The flush diff omitted created files** — the commonest shape of a delegated task.
  `git add -N` on untracked files, and they are listed explicitly.
- **The queue evicted the model it had just loaded.** Eviction now happens only when
  the next group needs a different model, and residency is re-armed after a flush.
- **Launcher discovery crashed** when a bare launcher sat beside versioned directories
  (a `str` sort key against `int` ones), and now prefers a real executable over a
  `.cmd`/`.bat` shim — those run through `cmd.exe`, which truncates every argument at
  its first newline and would silently drop the task and all project rules.
- **Measurements taken during a download** read four times slower and were stored as if
  real; `selfcheck` now refuses to measure while a pull is in flight (`--anyway`
  overrides). The tuning variables it records are labelled as the client shell's, since
  Ollama does not report its own configuration.
- **The fifth profile could not be built** although the setup told you to propose one.
- **The verification command ran through `cmd.exe` on Windows**, via `shell=True`. It now
  runs in PowerShell with the native exit code forwarded, because PowerShell otherwise
  reports the script's own status — and a verification step that always passes is worse
  than none.
- **A stored session id could outlive the session it named**, leaving the profile stuck
  resuming an id that no longer resolved. A run that fails that way now drops the id and
  retries once without it.
- **The flush summary listed the skill's own runtime files** under `.local-delegate/` as
  though they were the agent's new work, polluting the very diff the reviewer is told to
  trust.
- **A flush emitted nothing until it exited.** Python block-buffers stdout when it is not
  a terminal, so a delegation redirected to a file or run in the background was
  indistinguishable from a hang for its whole duration. Output is line-buffered now.
- **CI fails on anything defined and never used**, which is the exact shape of the
  `GUARDRAILS` defect.

### Known limitations

- **Delegation is serial.** `flush` blocks, and there is no way to run several copies
  of a small model at once. Returning a handle instead of a result changes every
  caller; doing it badly would be worse than the honest block.
- **No download progress.** Ollama's `-partial` blob is pre-allocated at full size and
  reads as complete from the first second, so the obvious signal lies.
- **No bandwidth probe**, so the setup's download time estimate is still a guess.
- The deny list is a net, not a sandbox. Bash rules match by prefix and file rules do
  not reach subprocesses that open files themselves; the agent may run `python`. It
  stops a confused model, not a determined one.

## [1.0.0] - 2026-08-31

First release, packaged as an installable Claude Code plugin.

### Added

- `local-delegate` skill: queue mechanical work and run it on local Ollama models
  through a second Claude Code, at no subscription-token cost.
- Four profiles — `code`, `fast`, `text`, `tiny` — chosen by the nature of the task.
- Queue-then-flush execution, grouped by model, heaviest to lightest.
- Guided setup that measures the machine, researches current models, and verifies them.
- Scripts, Python 3 standard library only.

[1.1.0]: https://github.com/saides-code/local-delegate/releases/tag/v1.1.0
[1.0.0]: https://github.com/saides-code/local-delegate/releases/tag/v1.0.0
