# redcoder eval-loop — autonomous session findings

Running log of the autonomous loop-engineering session (2026-08-19). Newest at top of each
section. Restore point before this session: git tag `pre-autonomous-20260819` (commit efa6b96)
and `~/Desktop/redcoder-backups/redcoder.py.20260819-114805.bak`.

**Safety invariant this whole session:** every model run on this open PC uses `--no-shell`
(the agent can WRITE commands but never EXECUTE one) and runs in a throwaway temp workspace.
No online-with-execution. Kali's real capabilities are unaffected (`--no-shell` is opt-in and
off for normal launches — verified in code).

## Result headlines
- **Baseline set (15 tasks):** leviathan **15/15** once two *grader* bugs were fixed (UTF-8
  mojibake hid tool-use detection; a regex `\b` bug rejected a correct nmap command). The model
  itself failed nothing. This is the CORE-Bench "42%→95% after fixing grading" trap — caught by
  reading transcripts, exactly as Anthropic prescribes.
- Baseline is **saturated (100%)** → per Anthropic, raise difficulty to keep the eval signaling.

## Method (grounded in Anthropic)
- tasks → trials → graders → transcripts; grade the OUTCOME (files + answer + tool-use), not the
  tool path; partial credit; pass@k / pass^k; isolated clean workspace per trial.
- Impartial judge = deterministic Python first; subjective dims reviewed cross-family (Opus on
  transcripts), never a local Qwen judging Qwen.

## Log
- **11:48** — Backup + tag. Kicked off baseline k=3 (reliability / pass^k). Building harder task
  sets: `tasks_hard.py` (deep multi-step, long-context, distractors, ambiguity→ask, tricky
  tool-selection, multi-file edit, indirection, precision) and `tasks_router.py` (Kali
  tool-identification-from-intent — also the basis for the small-model experiment).
