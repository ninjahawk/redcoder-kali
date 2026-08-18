> **Status (2026-08-17):** Phases 0 and 1 shipped, plus a dangerous-command backstop not
> in the original plan. What actually landed in `redcoder.py`:
>
> - `MAX_STEPS = 25`, `MAX_TOOL_OUTPUT = NUM_CTX*4//8` (4096 chars @ 8192 ctx),
>   `COMPACT_AT = max(4096, 0.85*NUM_CTX)`, `KEEP_RECENT = 8` — the budget fix below.
> - Loop detection: fingerprint each call; nudge on the 2nd identical call, hard-stop and
>   force a final answer on the 3rd. (Nudges rather than returning a cached result.)
> - Compaction summary must list "ACTIONS ALREADY COMPLETED".
> - `run_shell` confirms dangerous commands even in auto mode (`_DANGEROUS_PATTERNS`).
>
> **Deferred / not yet done** (good next steps, need a live model to validate):
> - A–B–A–B *alternation* detection — only consecutive repeats are caught so far.
> - Moving observations from `role:"user"` to `role:"tool"` (Part 1 #4 below).
> - Pagination instead of truncation for `read_file` / `grep`.

# Plan: making Redcoder reliable on a 14B

Written 2026-08-17. Target: `huihui_ai/qwen2.5-coder-abliterate:14b` at Q4_K_M, 8192 ctx,
on a 12 GB GPU, from a Kali live USB.

The observed symptom — "it repeatedly does the same thing over and over" — is not
mysterious and is mostly **not the model's fault**. The harness has no loop detection,
and its context budget is misconfigured badly enough to cause structural amnesia.

---

## Part 1 — Diagnosis

Ranked by how much I think each contributes.

### 1. Context budget is broken (the dominant cause)

| Setting | Value | As % of the 8192-token window |
|---|---|---|
| `MAX_TOOL_OUTPUT` | 20000 chars | **~5000 tok = 61%** |
| `COMPACT_AT` | 6144 tok | 75% |
| System prompt | ~3644 chars | ~911 tok = 11% |

System prompt + **one** maximum-size tool result = ~5911 tok, which is **96% of the
compaction threshold**. So a single large `grep` or `read_file` can trigger compaction
immediately.

This is a regression I introduced: `MAX_TOOL_OUTPUT = 20000` was written for
`NUM_CTX = 32768`, where it was ~15% of the window. Dropping the context to 8192 for
VRAM reasons made it 61% and I did not rescale it.

### 2. Compaction causes the repetition, then feeds itself

`maybe_compact` (redcoder.py ~1090) replaces `messages[1:-6]` with a prose summary and
keeps only `KEEP_RECENT = 6` messages verbatim. Combined with #1 this fires constantly.

The failure is a closed loop:

```
big tool output -> compaction fires -> detailed history replaced by lossy prose
  -> model no longer knows it already read that file -> reads it again
  -> another big tool output -> compaction fires -> ...
```

Worse, `summarize_messages` uses **the same 14B** to write the summary, under context
pressure. It drops exactly the specifics (paths, what was already done) that prevent
redoing work.

### 3. No loop detection anywhere

`agent_turn` is `for _ in range(MAX_STEPS)` with `MAX_STEPS = 200` and **no comparison
of the current action to any previous action**. Nothing stops 200 identical
`read_file` calls. Every reliable small-model harness has this; ours has none.

Standard definition: same tool + same arguments 3+ consecutive turns = stuck.

### 4. Tool results are injected as `role: "user"`

redcoder.py ~1180:

```python
messages.append({"role": "user", "content": f"OBSERVATION ({action['name']}):\n{result}"})
```

To a 14B, a new `user` turn reads as *the human speaking again*. A common consequence is
that the model restarts the original request from the top — which looks exactly like the
repetition being reported. Large models see through the framing; small ones often do not.

### 5. No ledger of completed actions

Nothing in context ever states plainly "you have already done X, Y, Z". The model has to
infer it from transcript history — precisely what compaction destroys.

### 6. `MAX_STEPS = 200` is far too generous

For a 14B, a run needing 200 tool calls has already failed. It just gives a loop room to
burn.

---

## Part 2 — The fixes, in order

Ordered by (impact / effort). Phases 0–2 are most of the win.

### Phase 0 — Fix the budget (one-line changes, largest single win)

Derive limits from `NUM_CTX` so they can never drift out of proportion again:

```python
MAX_STEPS       = 40                      # was 200
MAX_TOOL_OUTPUT = NUM_CTX * 4 // 8        # chars ~= 12% of window (4096 @ 8192 ctx)
KEEP_RECENT     = 8                       # was 6; cheap now that results are small
```

Also raise `COMPACT_AT` to `0.80 * NUM_CTX` **after** shrinking tool output, so
compaction becomes rare rather than routine.

Read/grep tools should paginate rather than truncate: return the first N lines plus
`"...showing 1-80 of 412 lines; call again with offset=80 for more"`. Truncation loses
information silently; pagination tells the model how to get the rest.

### Phase 1 — Deterministic loop detection in the harness

Do **not** rely on the model to notice it is looping. Enforce it in code.

```python
import hashlib, json as _json

def action_fingerprint(action):
    return hashlib.sha1(
        _json.dumps([action["name"], action.get("arguments", {})],
                    sort_keys=True).encode()
    ).hexdigest()[:12]
```

In `agent_turn`, keep `seen = {}` (fingerprint -> count) and `recent = []`:

- **2nd identical call:** do not re-run the tool. Return the *cached* previous result
  plus an explicit nudge:
  `"OBSERVATION: you already ran this exact call. Result was: <cached>. Do not call it
  again — use this result, or take a different action, or give your final answer."`
- **3rd identical call:** stop the turn, print a diagnostic, hand control back to the user.
- Also detect **A-B-A-B alternation** (a 2-cycle), not just consecutive repeats.

Caching the result is what makes this safe: the model still gets its answer, it just
cannot burn steps or re-run side-effecting commands.

### Phase 2 — Fix the observation channel

Two changes:

1. Try `role: "tool"` first (Ollama supports it on `/api/chat`). If the model handles it,
   observations stop looking like user turns. Keep `user` as a fallback behind a flag.
2. Whatever the role, make every observation end with an explicit next-step directive.
   Small models respond far better to a closed instruction than to
