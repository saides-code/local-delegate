# Checking for better models

Run this when the user asks whether newer models are worth switching to, or when
`python scripts/config.py show` says the last look was over a month ago.

The config file is what makes this cheap. It records which model backs each profile,
**why it was chosen**, and what the last selfcheck actually measured. So this is not
research from scratch: it is a comparison against a recorded baseline, and against
reasons you can re-read instead of reconstruct.

```bash
S="<this skill's directory>/scripts"
python "$S/config.py" show      # current mapping, reasons, measurements, last scan
python "$S/config.py" due       # exit 0 if a look is due
```

## 1. Do not scan on a hair trigger

New generations do not ship weekly and this research costs real tokens. If the last
scan was under 30 days ago and nothing prompted the question, say so and stop —
`config.py show` prints the date and what was concluded last time, which is usually
the whole answer. Scan anyway when the user asks directly, or when a selfcheck
started reporting problems.

## 2. Compare against what is recorded, not against a memory

For each profile, read from the config: the current tag, the reason it was picked,
its measured tokens/second, its VRAM fraction. Then look at what exists today:

1. `https://ollama.com/library/<current-tag>` — is it still there? Deprecated? Are
   there new sizes or quantisations?
2. The family's newer generation, if one shipped, on its own tag page.
3. The official model card of any candidate, **for the sampling parameters** — a new
   generation frequently changes them, and carrying over the old values is a silent
   way to make a better model perform worse.

The same rules from setup still apply, and they are what most candidates fail on:
size is read from the individual tag page, never from a round-up; without `tools` the
model is inert here whatever it scores; Q4_K_M is the floor; Q8 on a small model
loses to Q4 on a bigger one.

## 3. A switch needs a concrete reason

Newer is not a reason. Propose replacing a model only when you can name the gain:

- measurably better at **the job that profile does** (not on a general leaderboard)
- more context in the same VRAM
- the same quality in less VRAM, freeing room for the KV cache
- the current tag is deprecated or gone from the registry
- the current model is failing its selfcheck

If nothing meets that bar, that is a real result and worth recording — it is what
stops the next session from redoing the same search:

```bash
python "$S/config.py" record-scan --note "qwen3.5:9b still lists no tools capability; \
gemma4:12b unchanged; nothing worth switching to"
```

Write the *reason*, not just "no changes". A note saying why a candidate was rejected
saves the next scan from evaluating it again.

## 4. Swap safely: install, verify, and only then remove

Never remove the old model before the new one has proved itself. The order matters
because a swap can fail in ways a model card cannot warn you about — a broken chat
template, tool calls that never fire, an offload that does not fit.

```bash
python "$S/make_profile.py" code <new-tag> 65536 <params from the card> \
    --reason "why this is better than the previous one"
python "$S/selfcheck.py" code
```

`make_profile.py` keeps the old tag in the config as `previous_model`, so a rollback
is one command with the old parameters, which are still recorded.

Compare the selfcheck numbers with the ones the config already held:

- tool calling gone → **roll back immediately**, the profile is unusable
- noticeably fewer tokens/second, or a worse VRAM fraction → roll back unless the
  quality gain is large and the user accepts the trade
- better or equal → keep it, and only now offer to remove the old model, after
  `backup_models.py` has written its restore report

Then record what happened:

```bash
python "$S/config.py" record-scan --note "code: qwen3-coder:30b → <new>, \
+40% tok/s at the same VRAM, tool calling verified"
```

## 5. Report to the user

Say which profiles changed and which stayed, with the reason in each case — including
the ones you deliberately left alone. "I looked and there is nothing better for `text`,
because X" is a useful answer, not an empty one, and the config now holds it so the
question does not need re-answering next month.
