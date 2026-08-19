# Redcoder model roster & naming convention

Redcoder models are named after **dragons / mythical beasts**. The name should hint at the
model's *character* (size, speed, capability), not just sound cool.

## In use

| Name | Base (abliterated) | ~Disk | Character |
|---|---|---|---|
| **leviathan** ⭐ | `huihui_ai/Qwen3.8-abliterated:27b` | 18 GB | **Default.** Colossal, powerful, *ponderous*. 27B **dense** — most capable, but ~9–10 tok/s and slow prefill (~230 t/s). The "slow but smart" Opus-style pick. |
| **drago** | `huihui_ai/qwen2.5-coder-abliterate:14b` | 9 GB | Young dragon. 14B coder — fast & light (~61 tok/s, snappy prefill). The fast alternative when you want speed over raw capability. |

`DEFAULT_MODEL` in `redcoder.py` = **leviathan**. On a fresh machine where it isn't installed
yet, redcoder offers to download it on launch (and to free space by removing another model if
the disk is tight). Switch anytime with `/model`.

## Reserved for future models (approved names)

Pick the name to match the beast's nature when we add the model:

- **tiamat** — the primordial dragon, mother of all dragons. Reserve for a true apex / flagship model (pure "most powerful").
- **bahamut** — king of dragons. Clean, regal "top of the line."
- **fafnir** — Norse treasure-hoarding dragon. Deeper-cut / cooler, less on-the-nose.

### More in the bank (unused, thematically on-brand)
wyvern · hydra · kraken · fenrir · jörmungandr (world-serpent, for something huge) ·
quetzalcoatl · basilisk · chimera · cerberus · ryujin

> Rule of thumb: the *fast MoE* models (a3b) suit agile beast names; the *heavy dense*
> models suit the colossal, ponderous ones (leviathan, jörmungandr). Let the name tell the
> truth about the model, same as `leviathan` does.
