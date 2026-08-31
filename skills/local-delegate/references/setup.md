# Guided model setup

Run this the first time, or whenever the user wants to reconsider which models to use. It is a
conversation in nine steps: the user decides at every fork, you bring the data.

Do not skip steps and do not jump ahead to naming models. **The right list depends on this machine
and on today** — tags change size, new generations ship, vendor recommendations get revised. Any
model list you already hold in your head is out of date.

All scripts are Python 3, standard library only:

```bash
S="<this skill's directory>/scripts"
```

## 1. Photograph the machine

```bash
python "$S/check_machine.py"          # add --json if you want to parse it
```

Reports OS, CPU, RAM, GPU with VRAM, free disk, and Ollama's state with the models already present.
Show the result to the user and **ask them to confirm it**. Automatic detection is wrong more often
than people expect — external GPUs, hybrid laptop graphics, VMs, WSL seeing half the RAM. If the user
corrects a figure, theirs wins.

From here on the number that governs everything is **VRAM**. System RAM is the overflow reserve for
MoE models that spill out of the card, not a substitute for it.

## 2. Ollama: is it there? is it running?

**If the script says it is not on PATH**, do not conclude it is missing: on Windows it is nearly
always installed but invisible to the shell. The script already checks the usual install locations
and will say "installed but NOT on this shell's PATH" — in that case use the full path it printed, or
have the user add it to PATH. Reinstalling on top of a working install only creates confusion.

**If it is genuinely absent**, propose installing it and wait for approval:

| System | How |
|---|---|
| Windows | download the installer from ollama.com/download and run it (needs the GUI, not doable from a shell) |
| macOS | `brew install ollama`, or the app from ollama.com/download |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |

**If it is installed but not running**, start it: `ollama serve` in the background, or simply open the
app on Windows and macOS, which keeps it alive. Then re-run `check_machine.py` before continuing.

## 3. Ask about priorities

Two directions, and they change the whole list:

- **Disk space** — more compact models, tighter quantisation, possibly fewer profiles. Sensible on a
  full disk, or when delegation is occasional.
- **Performance** — latest generation, the most the VRAM can hold, long contexts. More gigabytes and
  longer downloads.

Tell the user how much space is free (they just saw it in step 1) so the choice is informed. If they
hesitate, go with performance: the bottleneck in this setup is model quality, not disk.

## 4. Look at what is already installed

If models are present, do not ignore them — some may cover a profile with no download at all. For each:

```bash
ollama show <name>
```

Read **capabilities** (`tools`, `vision`, `thinking`), size, context and quantisation. Then sort each
one into exactly one of three buckets:

- **it covers a profile** better than, or as well as, a fresh candidate → use it, and say so: a profile
  that costs no download is worth pointing out
- **it is good at something the four profiles do not cover** — vision is the common case → **propose an
  extra profile for it**. The four are a starting point, not a ceiling
- **it is a weaker duplicate** of something you are about to install, or serves nothing → set it aside
  as unused. Do not act on it yet

**Keeping everything is the default.** Do not ask the user to choose between ignoring, reusing and
deleting — that is three decisions at the point where they have the least information. Sort the models
yourself with the rule above, mention in one line which ones you are reusing, and leave the deletion
question for step 8, where it becomes a single yes-or-no with a number attached.

## 4b. Work out what this machine will actually be asked to do

Before picking models, work out which *capabilities* matter here. Two people with the
same GPU need different models, and choosing a generically good one wastes VRAM on
strengths they will never use.

Read the evidence in front of you, in this order:

1. **The project you were invoked in.** Languages and frameworks in use, the test
   command, whether there are images or design assets (vision), notebooks or numerical
   code (mathematical precision), non-English content or user-facing copy (language
   quality), how large the files are (context length).
2. **What the user has been asking for in this session.** If the conversation has been
   about refactors and tests, coding weight goes up; if it has been about documents and
   translations, language weight does.
3. **`.local-delegate/project.md`**, if it already exists — a previous session may have
   written down what this project delegates.

Do not claim to know more than you do here. You can see this project and this
conversation; you cannot see the user's other work. So turn the reading into a short
proposal and **ask them to correct it**:

> From this repo I see TypeScript with jest, a lot of user-facing copy in Italian, and
> no image or numerical work. So I would weight coding and language quality heavily,
> skip vision entirely, and not pay for mathematical strength. Does that match how you
> expect to use this?

Their answer, not your inference, is what gets recorded. Ask once, and only about the
capabilities that would actually change the shortlist.

Capabilities worth weighing, and what each one costs:

| Capability | Matters when | Cost if you get it wrong |
|---|---|---|
| coding + tool calling | always — every profile drives Claude Code | a model without tools is inert here |
| long context | large files, repo-wide work | KV cache eats VRAM; too little truncates silently |
| language quality | translations, docs, user-facing copy | a coding model writes stilted prose |
| mathematical precision | numerical code, data work | rarely needed; do not pay VRAM for it by default |
| vision | screenshots, design assets, PDFs | a whole extra profile; only if they really work that way |
| speed | many short tasks | a slow model makes delegation pointless |

If a capability they confirm falls outside the four standard profiles — vision is the
usual one — **propose a fifth profile for it** rather than bending an existing one.

Write the outcome down before moving on:

```bash
python "$S/config.py" init-project      # creates .local-delegate/project.md
```

Fill in what the project is, what gets delegated here, what must never be, and any
convention the local agent has to respect (the test command that must stay green, files
that are off limits). That file is portable and worth committing: it survives a change
of models and a change of machine, which is exactly why it is kept separate from the
model configuration.

Everything this skill writes goes in `.local-delegate/` in the repo, because what each
project needs differs. Of those files only `project.md` is portable: `config.json` is
machine-exclusive and is handled at step 8. `python "$S/config.py" where` lists them.

## 5. Research each candidate online

This is the step that makes the setup good, and the one it is most tempting to skip. For **every**
candidate model, open:

1. `https://ollama.com/library/<tag>` — real size in GB, quantisation, native context, capabilities
2. the official model card (Hugging Face, or the vendor's documentation) — **the recommended sampling
   parameters**

Rules paid for in blood:

- **Read the size on the individual tag page, never from a ranking or a round-up guide.** Aggregators
  hand out wrong numbers casually, and a wrong one makes you pick a model that does not fit or that
  wastes VRAM.
- **Without `tools`, a model is useless in every profile.** All profiles drive Claude Code, which
  cannot read or write a single file without function calling. A model that is excellent but has no
  tool support is not a compromise here — it is inert.
- **Q4_K_M is the floor.** Below it, tool-calling reliability degrades before conversational quality
  does: the model sounds sharp and gets the calls wrong.
- **Q8 on a small model is a bad trade.** Q4 on a bigger model beats it: you are paying VRAM for
  parameters, not for decimal places.
- **Never assume the temperature.** Several families officially recommend 1.0, not a low value
  "because it is code". A wrong one degrades output silently, without ever raising an error. Look it
  up, every time.

Then judge each candidate **against the profile's job and the weights from step 4b**, not in the
abstract: `code` needs solid tool calling and long context; `text` needs language quality and only
moderate context; `tiny` needs to be genuinely small, because running several copies at once is its
only reason to exist. A model that is excellent at something this user confirmed they never do is not
a better choice — it is the same VRAM spent worse.

## 6. Do the VRAM arithmetic

- One model in VRAM at a time. The total on disk may exceed it; the single loaded model may not, or
  barely.
- The loaded model has to leave room for the KV cache: at 64K context that is several GB. For `code`
  it is fine to graze the limit (MoE models spill into RAM and stay usable); for the others it is not.
- Scale the whole shortlist to the VRAM measured in step 1. A card with 8 GB, 16 GB or 24 GB supports
  a different set entirely, and on a machine with no GPU the honest answer is that delegation is not
  worth it — say so rather than installing something that will run on CPU.
- `tiny` only makes sense if two copies fit in VRAM together. If the smallest available model does not
  allow that, **say so and propose dropping `tiny`**: a profile that cannot run in parallel is just a
  worse `text`.
- Recommend the environment variables that widen the margin: `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`.

## 7. Propose the list

Present a table: profile, tag, size, chosen context, and **one line on why that model for that job**.
Add the total download and, on a slow connection, a rough time. Mark the profiles covered by models
already present, which cost nothing.

Wait for approval. If the user swaps a model, redo step 5 for the new one — never recycle another
model's parameters.

## 8. Install, then ask the one deletion question

Install first, so the user is never left without models:

```bash
python "$S/make_profile.py" code <tag> 65536 temperature=1.0 top_p=0.95 top_k=40 min_p=0.01 \
    --reason "why this model for this job, in one line"
```

The values above are **an example of shape, not a recommendation**: use the ones you read on the cards,
now. Repeat per profile.

`--reason` is not decoration. It gets stored in `~/.local-delegate/config.json` and read months later
by the update flow, which needs to know what problem this model was chosen to solve before it can
judge whether a newer one solves it better. A future session cannot reconstruct your reasoning; it can
only read it.

Then, and only then, look at the models you set aside as unused in step 4. If together they take a
meaningful amount of space, ask **one** question: these N models are unused and occupy X GB — remove
them, or keep them? If they take little space, or there are none, do not ask at all: an unnecessary
question about deletion only invites a mistake.

If the user says remove, the order is not negotiable:

```bash
python "$S/backup_models.py"          # FIRST. Writes report.md and restore.sh
```

Show them the report, in particular the section listing models that **cannot be re-downloaded** — if
there are any, have them confirm those by name, one at a time. Only then:

```bash
ollama rm <name>                      # one at a time, only the confirmed ones
```

Tell the user where the report is and that `restore.sh` puts everything back.

## 9. Selfcheck — never take a download on trust

Right after installing, before declaring the setup done:

```bash
python "$S/selfcheck.py"
```

A couple of minutes. Per profile it measures load time, tokens per second, **how much of the model
actually sits in VRAM**, and whether tool calling truly answers, then evicts each model so memory is
left clean.

What to look for in the issues it reports:

- **`no tool calling`** — the profile is unusable, not merely slow: Claude Code cannot open a file with
  that model. Go back to step 5 and replace it.
- **`runs entirely on CPU`** — it does not fit. Drop a quantisation level or a size.
- **`only N% in VRAM`** — partial offload. Expected and fine on `code`; on the others it means the
  model is too big for its job.
- **low `tok/s`** — under 5 tokens per second an agent is unusable: every task becomes slower than
  doing it by hand.

It also prints the tokens processed locally during the check. That is a tiny probe: use it to confirm
the loop works and to give a sense of scale, and **do not present it to the user as an estimate of
savings**, which only real tasks can show.

Then the end-to-end test, in a clean git repository:

```bash
python "$S/local_agent.py" run --ro fast "Summarise in ten lines what this project does."
```

If it answers, the loop is genuinely closed: selfcheck tests Ollama, this tests Claude Code on top of
Ollama.

`selfcheck.py` writes its measurements into the config as it goes, so the update flow can later compare
a candidate against what the current model really does rather than against a model card's claims.

Finally, record that the landscape was surveyed today:

```bash
python "$S/config.py" record-scan --note "initial setup: <what you picked and what you rejected, briefly>"
python "$S/config.py" show
```

Close by handling git, and only if this project is a repository.

`project.md` travels with the repo and is worth committing: it is what the user carries
to another machine, or hands to a colleague. `config.json` does not — its tags depend on
what is installed here and its measurements came from this GPU.

Show the user the lines and ask:

```bash
python "$S/config.py" gitignore-hint
```

If they agree, write them:

```bash
python "$S/local_agent.py" gitignore
```

Say in one sentence why: the model configuration is specific to this machine, the
project notes are not. If they would rather not touch `.gitignore`, leave it — nothing
breaks, those files just show up as untracked.

Report back to the user: which model backs which profile and why, the selfcheck results with any
issues, what was removed and where the restore report is, the environment variables to set, where the
two configuration files live and what each is for, and that `references/update.md` is how they ask for
a fresh look at newer models later.
