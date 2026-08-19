# redcoder evals

An eval loop for the redcoder harness + its local model (default **leviathan** = Qwen3.8‑27B),
built from Anthropic's published methodology:
[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
· [Create strong empirical evaluations](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)
· [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).

## Why it's built this way

- **tasks → trials → graders → transcripts.** Start small (~15–50 tasks from real failures), not
  hundreds — a slow local model makes that the right scale anyway.
- **Grade the OUTCOME, not the path.** We never assert a specific tool‑call sequence (Anthropic
  calls that too brittle — agents find valid alternate routes). Graders check what the agent
  *produced*: final answer, resulting workspace files, and whether it used a tool at all. Partial
  credit for multi‑part tasks.
- **Deterministic Python graders first** (`judge.py`) — objective, reproducible, bias‑free. That
  is the "impartial third‑party judge." An LLM‑as‑judge is only for subjective dimensions and, per
  Anthropic, **must be a different model family than the model under test.** leviathan is Qwen, so
  the judge is never a local Qwen model — it's the cross‑family reviewer (Opus) reading the saved
  transcripts. Tasks needing that are flagged `review=True`.
- **Isolated, clean environment per trial** (a fresh temp workspace) to avoid shared‑state noise.
- **pass@k / pass^k** capture non‑determinism: pass@k = perfect on ≥1 trial; pass^k = perfect on
  every trial (the reliability bar).
- **Read the failing transcripts** — the highest‑value step. Every failure is classified
  *model‑limitation* (leviathan genuinely can't) vs *harness/prompt‑bug* (we caused it). Only the
  latter get fixed; we don't fight the model's ceiling.

## Run

```bash
python evals/run_evals.py --model leviathan -k 1              # fast first signal
python evals/run_evals.py --model leviathan -k 3              # reliability (pass^k)
python evals/run_evals.py --model drago    -k 1 --only grounding,multistep
```

Needs Ollama running with the model present. Each run writes `runs/<model>-<stamp>/` with a
`summary.json` and per‑task `transcripts/`. Tasks run in `--sealed` with `-y` (auto‑approve) so
tool calls execute non‑interactively; they use file tools + text answers only, so the suite runs
on Windows *or* Kali. Security‑capability tasks ask the model to *write* a command (nmap, /dev/tcp),
not run it — testing correctness + that the abliterated model complies.

## Files

- `tasks.py`  — the capability tasks (tool‑selection, grounding, multi‑step, edit‑precision,
  search, know‑when‑not‑to‑tool, failure‑honesty, security‑capability, instruction‑following).
- `judge.py`  — deterministic grader helpers.
- `run_evals.py` — the runner (isolated workspaces, k trials, pass@k/pass^k, transcript capture).

## The loop

1. Run leviathan → per‑category pass rates + saved transcripts.
2. Read failures → classify model vs harness/prompt.
3. Fix harness/prompt bugs → re‑run → confirm improvement, no regression.
4. Add new tasks from new failures; watch for saturation (100% = the eval stopped signaling).
