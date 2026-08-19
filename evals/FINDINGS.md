# redcoder eval-loop — autonomous session findings

Running log of the autonomous loop-engineering session (2026-08-19). Newest at top of each
section. Restore point before this session: git tag `pre-autonomous-20260819` (commit efa6b96)
and `~/Desktop/redcoder-backups/redcoder.py.20260819-114805.bak`.

**Safety invariant this whole session:** every model run on this open PC uses `--no-shell`
(the agent can WRITE commands but never EXECUTE one) and runs in a throwaway temp workspace.
No online-with-execution. Kali's real capabilities are unaffected (`--no-shell` is opt-in and
off for normal launches — verified in code).

## Result headlines
- **Baseline set (15 tasks):** leviathan **15/15**, and at **k=3 it's 15/15 pass^k** — perfect
  on every trial, fully reliable/deterministic (temperature 0.4 but the answers are stable).
  This is after fixing two *grader* bugs (UTF-8 mojibake hid tool-use detection; a regex `\b`
  bug rejected a correct nmap command) — the model failed nothing; the CORE-Bench grading trap,
  caught by reading transcripts.
- Baseline **saturated** → raised difficulty. `tasks_hard` (k=3) running now to find real edges;
  `tasks_router` + the size ladder next.

## Method (grounded in Anthropic)
- tasks → trials → graders → transcripts; grade the OUTCOME (files + answer + tool-use), not the
  tool path; partial credit; pass@k / pass^k; isolated clean workspace per trial.
- Impartial judge = deterministic Python first; subjective dims reviewed cross-family (Opus on
  transcripts), never a local Qwen judging Qwen.

## Log
- **~11:55** — Built `tasks_hard.py` (10 stress tasks) + `tasks_router.py` (12 Kali
  tool-ID tasks). **Self-tested all 22 graders against reference correct answers → 0 bugs**
  before spending any model time (the discipline that caught the earlier grader defects).
  Read Anthropic's context-engineering guide (right-altitude prompts, minimize bloat,
  canonical examples, just-in-time retrieval, attention budget) to inform any prompt fixes.
  Pulled small abliterated models for the size ladder: 1.7b landed; 8b/4b hit transient
  registry 503s, retrying. Ladder for the router experiment: 1.7b · 4b · 8b · 14b(drago) ·
  27b(leviathan) [· 30b-a3b coder]. Baseline k=3 in progress (all passes so far).

## Persistent-bar TUI — decision (honesty)
The always-live bottom bar with type-while-working needs a full-screen architecture (scroll
region + threaded input) that is **terminal-dependent and cannot be validated without a live
interactive terminal**, which this autonomous session doesn't have. Shipping an unverified
full-screen rewrite as the forced default risks breaking the primary interface (and could
differ Windows vs Kali). Plan: implement it **behind a `--live` flag (opt-in)** with the
current, working bar as the default + fallback, unit-test the layout math, and leave it for
live validation. One-line flip to default once confirmed. The current collapse-on-submit bar
already works and stays the default.
