# Model shortlist, by VRAM tier

**Reviewed: 2026-09-01.** This file exists so the common case does not need the full
research pass of `setup.md` step 5 — that pass is expensive, and it runs on the model
this skill exists to spare.

Treat it as a starting point with a shelf life. If today is more than roughly six
months after the date above, **do not use it**: say so and run the deep path instead.
A stale shortlist is worse than no shortlist, because it reads as authoritative.

Every entry still has to pass `selfcheck.py`. A tag can change size or lose its tool
support between revisions of this file, and the measurement is what catches that.

## How to read this

Sizes are the download size of the tag, not the VRAM used at runtime: add several GB
for the KV cache at long context. The `code` profile may exceed VRAM if the model is
MoE — the experts spill to system RAM and it stays usable. The others should fit.

## 16 GB

| Profile | Tag | Size | Context | Parameters |
|---|---|---|---|---|
| `code` | `qwen3.6:35b-a3b-coding` | 21 GB | 65536 | temperature=1.0 top_p=0.95 top_k=40 min_p=0.01 |
| `fast` | `qwen3.5:9b` | 6.6 GB | 32768 | temperature=1.0 top_p=0.95 top_k=40 |
| `text` | `gemma4:12b` | 7.6 GB | 32768 | temperature=1.0 top_p=0.95 top_k=64 |
| `tiny` | `qwen3.5:2b` | 2.7 GB | 16384 | temperature=1.0 top_p=0.95 top_k=40 |

Measured on an RTX 4080 SUPER, 2026-09-01: 73.5 / 99.1 / 67.5 / 258.3 tok/s, the coder
at 55% resident and the rest fully in VRAM. Flash attention was off for that run, so
the coder's residency should improve with it on.

## 8-12 GB

Drop the `code` profile to a model that fits — a 14B coder at Q4_K_M, or the `fast`
model doing double duty — and keep `text` and `tiny` as above. Verify with
`selfcheck.py`: below 8 GB the coding profile usually cannot be made to work, and
saying so is more useful than installing something that runs on CPU.

## 24 GB and above

The 16 GB set still applies and leaves room; spend the headroom on context for `code`
before spending it on a larger model, because truncated context degrades a good model
faster than a smaller model does.

## Parameters

The values above are what the vendors recommended at the review date. **Never carry
them over to a different model.** Sampling recommendations are per family and get
revised; a low temperature on a family that asks for 1.0 degrades output silently, with
no error to notice.
