#!/usr/bin/env python3
"""Re-grade saved transcripts with the CURRENT graders — no GPU, no re-running the models.

Loop-engineering efficiency: when a grader improves (e.g. vague-inference now checks tool-naming),
apply it retroactively to transcripts already on disk instead of re-spending an hour of inference.

Limitation: saved transcripts hold the TEXT + tool calls, not the workspace files (deleted after
each trial). So FILE-based graders can't be reconstructed from text — those are skipped and keep
their original score. Text-based tasks (vague-inference, ask-vs-act, recklessness, cmdwrite,
router) re-grade faithfully.

Usage:  python regrade.py runs/campaign-judgment-YYYYMMDD-HHMMSS
"""
import importlib, os, re, sys, glob
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)

# task ids whose grader reads result["files"] — cannot re-grade from text ("ALL" = whole set is file-based)
FILE_BASED = {
    "tasks_judgment": {"wf_scan_dump", "traj_db_refs", "traj_distract", "traj_chain"},
    "tasks_fileops": "ALL", "tasks_recovery": "ALL", "tasks_robustness": "ALL",
    "tasks_cmdwrite": set(), "tasks_router": set(),
}
_HDR = re.compile(r"# task=(\S+)\s+cat=(\S+)\s+score=([\d.]+)")


def _parse(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    m = _HDR.search(raw)
    if not m:
        return None
    tid, cat, old = m.group(1), m.group(2), float(m.group(3))
    body = raw.split("=" * 70, 1)[1] if "=" * 70 in raw else raw
    tools = re.findall(r"▸\s+(\w+)", body)
    return {"id": tid, "cat": cat, "old": old,
            "result": {"text": body, "files": {}, "tools_used": tools, "exit": 0}}


def main():
    campdir = sys.argv[1]
    models = [d for d in sorted(os.listdir(campdir)) if os.path.isdir(os.path.join(campdir, d))]
    task_mods = {}
    print(f"# RE-GRADE {campdir}\n")
    for model in models:
        for tsdir in sorted(glob.glob(os.path.join(campdir, model, "*"))):
            ts = os.path.basename(tsdir)
            tdir = os.path.join(tsdir, "transcripts")
            if not os.path.isdir(tdir):
                continue
            if ts not in task_mods:
                task_mods[ts] = {t["id"]: t for t in importlib.import_module(ts).TASKS}
            tasks = task_mods[ts]
            fb = FILE_BASED.get(ts, set())
            # aggregate trials per task
            byid = {}
            for path in sorted(glob.glob(os.path.join(tdir, "*_trial*.txt"))):
                p = _parse(path)
                if not p:
                    continue
                byid.setdefault(p["id"], []).append(p)
            cat_old, cat_new = {}, {}
            changed = []
            for tid, trials in byid.items():
                cat = trials[0]["cat"]
                olds = [t["old"] for t in trials]
                if fb == "ALL" or tid in fb or tid not in tasks:
                    news = olds                                     # can't re-grade -> keep original
                else:
                    news = []
                    for t in trials:
                        try:
                            s, _ = tasks[tid]["check"](t["result"])
                        except Exception:
                            s = t["old"]
                        news.append(round(float(s), 3))
                o_pass = 1 if max(olds) >= 1.0 else 0
                n_pass = 1 if max(news) >= 1.0 else 0
                cat_old.setdefault(cat, []).append(o_pass)
                cat_new.setdefault(cat, []).append(n_pass)
                if o_pass != n_pass or round(sum(olds)/len(olds), 2) != round(sum(news)/len(news), 2):
                    changed.append((tid, round(sum(olds)/len(olds), 2), round(sum(news)/len(news), 2)))
            print(f"## {model} / {ts}")
            for cat in sorted(cat_new):
                o, n = cat_old[cat], cat_new[cat]
                flag = "  <-- changed" if sum(o) != sum(n) else ""
                print(f"   {cat:16} pass@k {sum(o)}/{len(o)} -> {sum(n)}/{len(n)}{flag}")
            for tid, o, n in changed:
                print(f"      · {tid:18} avg {o} -> {n}")
            print()


if __name__ == "__main__":
    main()
