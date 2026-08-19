#!/usr/bin/env python3
"""Small-model experiment: how small a model still accurately identifies the right Kali tool
and writes a correct command from a plain-English intent? Runs the tasks_router set across a
size ladder and prints a task x model matrix + per-model accuracy.

Safe: every run is --no-shell (writes commands, never executes) in a throwaway workspace.
Fair: --no-think is forced (the runner does this) so Qwen3 hybrids aren't penalized by thinking
traces polluting the JSON protocol.
"""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))     # repo root, to reuse redcoder's resolver
import run_evals as R
import tasks_router as TR
import redcoder as RC                          # for resolve_model + _ollama_bin (import-safe)


def _unload(model):
    """Free a model's VRAM/RAM before loading the next one (avoid cumulative pressure)."""
    ref = RC.resolve_model(model)
    try:
        subprocess.run([RC._ollama_bin(), "stop", ref], capture_output=True, timeout=30)
    except Exception:
        pass
    time.sleep(3)

LADDER = [
    ("1.7b",          "huihui_ai/qwen3-abliterated:1.7b"),
    ("4b",            "huihui_ai/qwen3-abliterated:4b"),
    ("8b",            "huihui_ai/qwen3-abliterated:8b"),
    ("14b-drago",     "drago"),
    ("27b-leviathan", "leviathan"),
]
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 300

results = {}   # label -> {task_id: (score, seconds)}
for label, model in LADDER:
    print(f"\n### {label} ({model})", flush=True)
    row = {}
    for t in TR.TASKS:
        tr = R.run_trial(t, model, TIMEOUT)
        row[t["id"]] = (tr["score"], tr["seconds"])
        print(f"  {'PASS' if tr['score']>=1 else 'fail'} {t['id']:16} {tr['seconds']:>5}s  {tr['note']}", flush=True)
    results[label] = row
    _unload(model)                             # free this model before the next loads

# --- matrix ---
tasks = [t["id"] for t in TR.TASKS]
labels = [l for l, _ in LADDER]
print("\n\n# ===== Kali tool-router: task x model (✓ = correct tool+command) =====", flush=True)
head = "  " + "task".ljust(16) + "".join(l.rjust(15) for l in labels)
print(head, flush=True)
for tid in tasks:
    cells = "".join(("✓" if results[l][tid][0] >= 1 else "·").rjust(15) for l in labels)
    print("  " + tid.ljust(16) + cells, flush=True)
print("  " + "-" * (16 + 15 * len(labels)), flush=True)
totals = "".join(f"{sum(1 for tid in tasks if results[l][tid][0]>=1)}/{len(tasks)}".rjust(15) for l in labels)
print("  " + "ACCURACY".ljust(16) + totals, flush=True)
avg_s = "".join(f"{sum(results[l][tid][1] for tid in tasks)/len(tasks):.1f}s".rjust(15) for l in labels)
print("  " + "avg latency".ljust(16) + avg_s, flush=True)

out = os.path.join(HERE, "runs", f"ladder-router-{time.strftime('%Y%m%d-%H%M%S')}.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump({l: {k: v[0] for k, v in row.items()} for l, row in results.items()}, f, indent=2)
print(f"\n# saved {out}", flush=True)
