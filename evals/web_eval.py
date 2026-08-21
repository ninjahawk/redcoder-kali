#!/usr/bin/env python3
"""Live end-to-end eval of redcoder's web_search INSIDE the real agent loop, graded by an
IMPARTIAL separate model (LLM-as-judge, the pattern Anthropic documents for evals).

Setup that keeps it honest:
  - Model under test (generator): leviathan (Qwen3.8-abliterated:27b) — answers current-fact
    questions it can only get right by looking them up, running the FULL tool loop live.
  - Judge: a DIFFERENT model (qwen3.5:9b). It never sees the trajectory or that this is
    "redcoder" — only the question, an out-of-band REFERENCE answer, and the candidate answer.
    So it grades correctness, blind to how the answer was produced (no self-preference).
  - Ground truth is established INDEPENDENTLY (a separate retrieval path, out of band) and
    hard-coded below — the judge never trusts the very tool under test.
  - Tool-use ("did web_search actually fire, and return results") is a DETERMINISTIC check
    read from the message list, kept separate from the judge's correctness call.

Run:  python evals/web_eval.py        (leviathan must be loaded in Ollama; ~1-3 min)
"""
import io, json, os, sys, time, contextlib, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import redcoder as RC

GEN_MODEL = "leviathan"          # under test — what's loaded on this PC
JUDGE_MODEL = "qwen3.5:9b"       # impartial judge — a DIFFERENT model, given ground truth

# Ground truth, established out of band from canonical sources (python.org / go.dev / nodejs.org)
# on 2026-08-21 — NOT via redcoder's own scraper, so the eval isn't circular.
CASES = [
    {"id": "python",
     "q": "Use web_search to find the exact latest stable release version of Python 3, "
          "then tell me just that version number.",
     "truth": "Python 3.14.7 (latest stable, released Aug 5 2026)"},
    {"id": "go",
     "q": "Use web_search to find the current latest stable version of the Go programming "
          "language, then report the exact version number.",
     "truth": "go1.27.0 (current latest stable Go)"},
    {"id": "node",
     "q": "Use web_search to find the current Node.js LTS version number, then report it.",
     "truth": "Node.js LTS is v24.19.0 (the current non-LTS release line is v26.7.0)"},
]


def run_agent(model, question):
    """Run one full agent turn in-process and read the result from the message list."""
    RC._NET_MODE = "online"; RC._LIVE = False; RC._C = False; RC._NO_SHELL = False
    messages = [{"role": "system", "content": RC.build_system(model)},
                {"role": "user", "content": question}]
    buf = io.StringIO()
    t0 = time.time()
    err = None
    with contextlib.redirect_stdout(buf):
        try:
            RC.agent_turn(model, messages, RC.Approver(True))   # auto-approve
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    dt = time.time() - t0

    used_search = search_ok = False
    for m in messages:
        c = str(m.get("content", ""))
        if m["role"] == "user" and c.startswith("OBSERVATION (web_search)"):
            used_search = True
            if "no results parsed" not in c:
                search_ok = True
    # final answer = last assistant message that is NOT a tool call
    final = ""
    for m in reversed(messages):
        if m["role"] == "assistant":
            try:
                act = RC.extract_action(m["content"], None)
            except Exception:
                act = None
            if not act:
                final = str(m["content"]).strip()
                break
    return {"final": final, "used_search": used_search, "search_ok": search_ok,
            "secs": round(dt, 1), "err": err, "log": buf.getvalue()}


def judge(case, answer):
    """Impartial grader: a different model, given only Q + reference + candidate answer."""
    rubric = (
        "You are an impartial grader. Decide whether the CANDIDATE ANSWER states the same "
        "key fact (the version number) as the REFERENCE. Judge ONLY the fact, not style or "
        "extra words, and ignore how the answer was obtained. Formatting differences are fine "
        "(\"3.14.7\" == \"Python 3.14.7\"). If the candidate gives a DIFFERENT version number "
        "than the reference, it is INCORRECT. If it gives no version, it is INCORRECT.\n\n"
        f"QUESTION: {case['q']}\n"
        f"REFERENCE (ground truth): {case['truth']}\n"
        f"CANDIDATE ANSWER: {answer or '(no answer)'}\n\n"
        "Reply with ONLY a JSON object: "
        "{\"verdict\": \"CORRECT\" | \"INCORRECT\", \"reason\": \"<one sentence>\"}")
    payload = {"model": JUDGE_MODEL,
               "messages": [{"role": "user", "content": rubric}],
               "stream": False, "think": False, "format": "json",
               "options": {"temperature": 0}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.load(r)
    txt = d.get("message", {}).get("content", "")
    try:
        return json.loads(txt)
    except Exception:
        return {"verdict": "PARSE_ERROR", "reason": txt[:200]}


def main():
    print(f"# redcoder web_search live eval")
    print(f"  generator (under test): {GEN_MODEL}  ->  {RC.resolve_model(GEN_MODEL)}")
    print(f"  impartial judge:        {JUDGE_MODEL}\n")

    results = []
    print("## generation (full agent loop, live web)")
    for c in CASES:
        r = run_agent(GEN_MODEL, c["q"])
        results.append((c, r))
        flag = "ok" if r["used_search"] and r["search_ok"] else "!!"
        print(f"  [{c['id']:6}] {r['secs']:5}s  web_search={r['used_search']} results={r['search_ok']} {flag}"
              + (f"  ERR {r['err']}" if r["err"] else ""))
        print(f"           answer: {(r['final'][:160] or '(none)')}")

    print(f"\n## impartial judge ({JUDGE_MODEL}) — blind to trajectory, given ground truth")
    passed = 0
    verdicts = []
    for c, r in results:
        v = judge(c, r["final"])
        verdicts.append((c, r, v))
        ok = v.get("verdict") == "CORRECT"
        passed += ok
        print(f"  [{c['id']:6}] {v.get('verdict'):9}  — {v.get('reason')}")

    used = sum(r["used_search"] for _, r in results)
    okres = sum(r["search_ok"] for _, r in results)
    print(f"\n## result")
    print(f"  web_search fired:     {used}/{len(CASES)}")
    print(f"  returned live results:{okres}/{len(CASES)}")
    print(f"  judged CORRECT:       {passed}/{len(CASES)}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_eval_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump([{"case": c, "run": {k: v for k, v in r.items() if k != "log"},
                    "verdict": vd} for c, r, vd in verdicts], f, indent=2)
    print(f"  full record -> {out}")


if __name__ == "__main__":
    main()
