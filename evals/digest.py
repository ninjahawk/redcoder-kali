#!/usr/bin/env python3
"""Trajectory digest — compact, cross-model view of saved transcripts for the impartial judge.

`regrade.py` gives the deterministic numbers; this gives the QUALITATIVE picture the numbers can't:
per task, each model's tool sequence + final answer, side by side, so the cross-family judge (Opus)
can score the subjective dims (inference / tool_choice / trajectory / completion) fairly and fast.

Usage:
    python digest.py runs/campaign-judgment-YYYYMMDD-HHMMSS
    python digest.py <campdir> --taskset tasks_judgment --cat vague-inference
    python digest.py <campdir> --task vague_recon
"""
import argparse, glob, os, re, sys

LADDER = ["1.7b", "4b", "8b", "drago", "leviathan", "coder30b"]
_MARK = ("»", "▸", "✗", "✓", "⚠", "!", "┌", "│", "└")


def _order(models):
    return sorted(models, key=lambda m: LADDER.index(m) if m in LADDER else 99)


def _extract(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    body = raw.split("=" * 70, 1)[1] if "=" * 70 in raw else raw
    lines = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).rstrip() for ln in body.splitlines()]
    tools = []
    for ln in lines:
        m = re.match(r"\s*▸\s+(\w+)\s*(.*)", ln)
        if m:
            arg = m.group(2)[:48]
            tools.append(f"{m.group(1)}({arg})" if arg else m.group(1))
    looped = any("repeated the same action" in ln for ln in lines)
    # final answer = trailing run of non-marker prose lines
    ans, run = "", []
    for ln in lines:
        s = ln.strip()
        if not s:
            if run:
                ans = " ".join(run); run = []
            continue
        if s[0] in _MARK or s.startswith("ERROR") or s.startswith("✗"):
            if run:
                ans = " ".join(run); run = []
            continue
        run.append(s)
    if run:
        ans = " ".join(run)
    return {"tools": tools, "answer": ans[:320], "looped": looped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("campdir")
    ap.add_argument("--taskset", default="tasks_judgment")
    ap.add_argument("--cat", default="")
    ap.add_argument("--task", default="")
    ap.add_argument("--trial", type=int, default=1)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # transcripts hold ▸/box chars
    except Exception:
        pass

    models = _order([d for d in os.listdir(args.campdir)
                     if os.path.isdir(os.path.join(args.campdir, d))])
    # gather task ids + cats from any model's transcripts
    tasks = {}
    for m in models:
        for p in glob.glob(os.path.join(args.campdir, m, args.taskset, "transcripts", f"*_trial{args.trial}.txt")):
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.readline()
            mm = re.search(r"# task=(\S+)\s+cat=(\S+)", head)
            if mm:
                tasks.setdefault(mm.group(1), mm.group(2))
    ids = [t for t in tasks if (not args.cat or tasks[t] == args.cat) and (not args.task or t == args.task)]
    ids.sort(key=lambda t: (tasks[t], t))

    for tid in ids:
        print(f"\n{'='*78}\n### {tid}   [{tasks[tid]}]")
        # print the prompt from the first available transcript
        for m in models:
            p = os.path.join(args.campdir, m, args.taskset, "transcripts", f"{tid}_trial{args.trial}.txt")
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    f.readline(); pl = f.readline()
                print("  " + pl.strip())
                break
        for m in models:
            p = os.path.join(args.campdir, m, args.taskset, "transcripts", f"{tid}_trial{args.trial}.txt")
            if not os.path.exists(p):
                continue
            e = _extract(p)
            loop = " [LOOPED]" if e["looped"] else ""
            chain = " → ".join(e["tools"]) if e["tools"] else "(no tools)"
            print(f"\n  ── {m}{loop}")
            print(f"     tools: {chain[:150]}")
            print(f"     ans:   {e['answer']}")


if __name__ == "__main__":
    main()
