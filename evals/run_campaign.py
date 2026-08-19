#!/usr/bin/env python3
"""Campaign runner — drives many tasksets across the model size-ladder, unattended.

Loop-engineering harness: runs (model x taskset x k trials), grades the OUTCOME deterministically,
saves every transcript for the cross-family judge (Opus) to read, and writes an incremental
matrix so partial data survives an interruption. Model-outer ordering + explicit unload between
models keeps VRAM/RAM pressure bounded (the machine is unattended — must not wedge).

SAFE: every trial is --no-shell (writes commands, never executes) in a throwaway temp workspace
(run_evals.run_trial). Nothing runs against anything.

Usage:
    python run_campaign.py --tasksets tasks_judgment --k 2
    python run_campaign.py --tasksets tasks_fileops,tasks_recovery --models 1.7b,4b,8b,drago,leviathan --k 2
"""
import argparse, json, os, re, subprocess, sys, time, importlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import run_evals as R
import redcoder as RC

# ladder shorthand -> ollama ref (small ones are general qwen3-abliterated; drago/leviathan are roster keys)
LADDER = {
    "1.7b": "huihui_ai/qwen3-abliterated:1.7b",
    "4b":   "huihui_ai/qwen3-abliterated:4b",
    "8b":   "huihui_ai/qwen3-abliterated:8b",
    "drago": "drago",
    "leviathan": "leviathan",
    "coder30b": "huihui_ai/qwen3-coder-abliterated:30b",
}
# which tasksets are security/Kali (need the Kali system prompt for a faithful test)
KALI_TASKSETS = {"tasks_router", "tasks_judgment", "tasks_cmdwrite"}


def _unload(ref):
    try:
        subprocess.run([RC._ollama_bin(), "stop", RC.resolve_model(ref)],
                       capture_output=True, timeout=30)
    except Exception:
        pass
    time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasksets", required=True, help="comma list of task modules")
    ap.add_argument("--models", default="1.7b,4b,8b,drago,leviathan", help="comma list of ladder keys/refs")
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--tag", default="", help="label for this campaign's run dir")
    ap.add_argument("--only", default="", help="comma list of task ids to include (across tasksets)")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    tasksets = [t.strip() for t in args.tasksets.split(",") if t.strip()]
    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    campdir = os.path.join(HERE, "runs", f"campaign-{args.tag+'-' if args.tag else ''}{stamp}")
    os.makedirs(campdir, exist_ok=True)
    print(f"# CAMPAIGN {campdir}\n# tasksets={tasksets} models={model_keys} k={args.k}\n", flush=True)

    mods = {ts: importlib.import_module(ts) for ts in tasksets}
    results = {}   # model_key -> taskset -> [rows]
    t_start = time.time()

    for mkey in model_keys:
        ref = LADDER.get(mkey, mkey)
        results[mkey] = {}
        print(f"\n{'#'*60}\n# MODEL {mkey}  ({ref})\n{'#'*60}", flush=True)
        for ts in tasksets:
            R.KALI_CTX = ts in KALI_TASKSETS          # faithful Kali prompt for security sets
            tdir = os.path.join(campdir, R_model_slug(mkey), ts, "transcripts")
            os.makedirs(tdir, exist_ok=True)
            rows = []
            tasklist = [t for t in mods[ts].TASKS if not only or t["id"] in only]
            print(f"\n--- {ts}  (kali={R.KALI_CTX}, {len(tasklist)} tasks)", flush=True)
            for t in tasklist:
                scores = []
                for i in range(args.k):
                    try:
                        tr = R.run_trial(t, ref, args.timeout)
                    except Exception as e:
                        tr = {"score": 0.0, "note": f"trial-crash: {type(e).__name__}: {e}",
                              "seconds": 0.0, "tools": [], "exit": -99, "text": str(e)}
                    scores.append(tr["score"])
                    with open(os.path.join(tdir, f"{t['id']}_trial{i+1}.txt"), "w", encoding="utf-8") as f:
                        f.write(f"# task={t['id']} cat={t['cat']} score={tr['score']} "
                                f"note={tr['note']} {tr['seconds']}s\n# prompt: {t['prompt']}\n"
                                f"{'='*70}\n{tr['text']}")
                avg = round(sum(scores) / len(scores), 3)
                passk = max(scores) >= 1.0
                rows.append({"id": t["id"], "cat": t["cat"], "avg": avg, "pass@k": passk,
                             "scores": scores})
                mark = "PASS" if passk else "fail"
                print(f"  [{mark}] {t['id']:18} {t['cat']:16} avg={avg}", flush=True)
            results[mkey][ts] = rows
            # incremental save after every (model, taskset) so an interruption keeps partial data
            with open(os.path.join(campdir, "results.json"), "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
        _unload(ref)     # free this model's memory before the next one loads (unattended safety)

    # ---- summary matrix ----
    print(f"\n\n# ===== CAMPAIGN SUMMARY (pass@k) =====  ({(time.time()-t_start)/60:.1f} min)", flush=True)
    for ts in tasksets:
        print(f"\n## {ts}", flush=True)
        head = "  " + "category".ljust(18) + "".join(m.rjust(12) for m in model_keys)
        print(head, flush=True)
        cats = {}
        for mkey in model_keys:
            for row in results[mkey].get(ts, []):
                cats.setdefault(row["cat"], {}).setdefault(mkey, []).append(row["pass@k"])
        for cat in sorted(cats):
            cells = ""
            for mkey in model_keys:
                v = cats[cat].get(mkey, [])
                cells += (f"{sum(v)}/{len(v)}" if v else "-").rjust(12)
            print("  " + cat.ljust(18) + cells, flush=True)
        # overall row
        cells = ""
        for mkey in model_keys:
            allrows = results[mkey].get(ts, [])
            p = sum(1 for r in allrows if r["pass@k"])
            cells += (f"{p}/{len(allrows)}" if allrows else "-").rjust(12)
        print("  " + "OVERALL".ljust(18) + cells, flush=True)
    print(f"\n# saved {campdir}/results.json", flush=True)


def R_model_slug(m):
    return re.sub(r"[^\w.-]", "_", m)


if __name__ == "__main__":
    main()
