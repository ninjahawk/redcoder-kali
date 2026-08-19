#!/usr/bin/env python3
"""Eval runner for redcoder — drives `redcoder -p` on each capability task in an isolated
workspace, grades the OUTCOME deterministically, and reports pass@k / pass^k per category.

Methodology (Anthropic — Demystifying evals for AI agents):
  tasks -> trials -> graders -> transcripts.
  - Each trial runs in a fresh temp workspace (clean, isolated) to avoid shared-state noise.
  - We grade what the agent produced (files + final answer + whether it used a tool), never a
    rigid tool-call sequence.
  - k trials capture non-determinism:  pass@k = succeeded at least once;  pass^k = every time.
  - Transcripts are saved so failures can be READ (the highest-value step) and each classified
    model-limitation vs harness/prompt-bug.

Usage:
    python run_evals.py --model leviathan -k 1
    python run_evals.py --model drago -k 3 --only grounding,multistep
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from judge import strip_ansi                          # noqa: E402
import tasks as tasks_mod                             # noqa: E402

TEXT_EXT = {".txt", ".py", ".json", ".md", ".csv", ".cfg", ".ini", ".sh", ""}


def read_workspace(wd):
    out = {}
    for root, _, files in os.walk(wd):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, wd).replace("\\", "/")
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            try:
                if os.path.getsize(p) > 200_000:
                    continue
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    out[rel] = f.read()
            except Exception:
                pass
    return out


def run_trial(task, model, timeout):
    wd = tempfile.mkdtemp(prefix=f"eval_{task['id']}_")
    if task.get("setup"):
        task["setup"](wd)
    mode = task.get("mode", "--sealed")
    # --no-shell: the agent can WRITE commands (tested) but never EXECUTE one — safe on an open
    # box. It still has the file tools. Force UTF-8 both ways so the ▸ tool markers survive.
    cmd = [sys.executable, os.path.join(REPO, "redcoder.py"),
           "-p", task["prompt"], "-m", model, mode, "--no-shell", "-y"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, env=env)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or ""), (e.stderr or "") + "\n[TIMEOUT]", -9
    elapsed = time.time() - t0
    text = strip_ansi((out or "") + "\n" + (err or ""))
    files_after = read_workspace(wd)
    result = {
        "text": text,
        "files": files_after,                          # graders read both seeds and new files
        "tools_used": re.findall(r"▸\s+(\w+)", text),
        "exit": code,
        "seconds": round(elapsed, 1),
        "workdir": wd,
    }
    try:
        score, note = task["check"](result)
    except Exception as e:
        score, note = 0.0, f"grader-error: {type(e).__name__}: {e}"
    return {"score": round(float(score), 3), "note": note,
            "seconds": result["seconds"], "tools": result["tools_used"],
            "exit": code, "text": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="leviathan")
    ap.add_argument("-k", type=int, default=1, help="trials per task")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", default="", help="comma-separated task ids to run")
    ap.add_argument("--outdir", default=os.path.join(HERE, "runs"))
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    tasklist = [t for t in tasks_mod.TASKS if not only or t["id"] in only]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(args.outdir, f"{args.model}-{stamp}")
    tdir = os.path.join(outdir, "transcripts")
    os.makedirs(tdir, exist_ok=True)

    print(f"# redcoder evals  model={args.model}  k={args.k}  tasks={len(tasklist)}", flush=True)
    print(f"# out: {outdir}\n", flush=True)
    summary = []
    for t in tasklist:
        trials = []
        for i in range(args.k):
            tr = run_trial(t, args.model, args.timeout)
            trials.append(tr)
            with open(os.path.join(tdir, f"{t['id']}_trial{i+1}.txt"), "w", encoding="utf-8") as f:
                f.write(f"# task={t['id']}  cat={t['cat']}  score={tr['score']}  "
                        f"note={tr['note']}  {tr['seconds']}s\n"
                        f"# prompt: {t['prompt']}\n{'='*70}\n{tr['text']}")
        scores = [x["score"] for x in trials]
        passk = max(scores) >= 1.0                      # pass@k: perfect on at least one trial
        passall = all(s >= 1.0 for s in scores)         # pass^k: perfect every trial
        avg = round(sum(scores) / len(scores), 3)
        row = {"id": t["id"], "cat": t["cat"], "avg": avg, "pass@k": passk,
               "pass^k": passall, "review": t.get("review", False),
               "notes": [x["note"] for x in trials],
               "secs": [x["seconds"] for x in trials]}
        summary.append(row)
        mark = "PASS" if passk else "FAIL"
        flag = " (review)" if t.get("review") else ""
        print(f"  [{mark}] {t['id']:16} {t['cat']:22} avg={avg:<4} "
              f"pass@k={passk} pass^k={passall}{flag}  {trials[0]['note']}", flush=True)

    # aggregate
    cats = {}
    for row in summary:
        cats.setdefault(row["cat"], []).append(row)
    print("\n# per-category pass@k", flush=True)
    for cat, rows in sorted(cats.items()):
        pk = sum(1 for r in rows if r["pass@k"]) / len(rows)
        print(f"  {cat:24} {sum(1 for r in rows if r['pass@k'])}/{len(rows)}  ({pk:.0%})", flush=True)
    overall = sum(1 for r in summary if r["pass@k"]) / len(summary) if summary else 0
    print(f"\n# OVERALL pass@k: {sum(1 for r in summary if r['pass@k'])}/{len(summary)} "
          f"({overall:.0%})", flush=True)
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n# transcripts + summary.json -> {outdir}", flush=True)


if __name__ == "__main__":
    main()
