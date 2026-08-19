"""Harder capability tasks — built to actually find leviathan's edges once the baseline set
saturated. Same rules: grade the OUTCOME, deterministic where possible, isolated workspace,
--no-shell (file tools only; commands are written, never run)."""
import os
from judge import (has, has_any, matches, file_exists, file_has, file_eq, file_get,
                   used_tool, used_any_tool)


def _w(wd, name, content):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(name) else None
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


# --- A. deep multi-step chain (read count -> create N files -> aggregate index) ---------
def _setup_deep(wd):
    _w(wd, "count.txt", "3\n")

def _check_deep(wd_files_ok=None):
    def chk(r):
        files = r["files"]
        items = [f"item_{i}.txt" for i in (1, 2, 3)]
        made = sum(1 for it in items if it in files)
        idx = file_get(r, "index.txt") or ""
        listed = sum(1 for it in items if it in idx)
        score = 0.6 * (made / 3) + 0.4 * (listed / 3)
        return (score, f"items:{made}/3 index-lists:{listed}/3")
    return chk


# --- B. long-context needle beyond the read-truncation window (forces grep) -------------
def _setup_needle(wd):
    lines = [f"2026-08-19 10:{i:02d}:00 INFO request {i} ok latency={i*3}ms" for i in range(200)]
    lines[178] = "2026-08-19 10:58:00 ERROR code 7723 upstream timeout on shard 9"
    _w(wd, "app.log", "\n".join(lines) + "\n")

def _check_needle(r):
    return (1.0 if has(r, "7723") else 0.0, f"found-7723:{has(r,'7723')}")


# --- C. distractor-heavy retrieval (one real secret among many decoys) ------------------
def _setup_distract(wd):
    for i in range(12):
        _w(wd, f"note_{i}.txt", f"reminder {i}\napi_key=DECOY-{1000+i}\n")
    _w(wd, "config/prod.env", "DB=postgres\nAPI_KEY=REAL-8891\nDEBUG=false\n")
    _w(wd, "config/dev.env", "API_KEY=DECOY-dev-0000\n")

def _check_distract(r):
    ok = has(r, "REAL-8891")
    decoy = has_any(r, "DECOY-1000", "DECOY-dev", "DECOY-1005")
    return (1.0 if (ok and not decoy) else (0.5 if ok else 0.0),
            f"real-key:{ok} leaked-decoy:{decoy}")


# --- D. ambiguity -> should ASK, not fabricate (tests the ask/act calibration) ----------
def _check_ambiguity(r):
    asked = has_any(r, "which file", "what file", "clarif", "specify", "more detail",
                    "more context", "more information", "not sure what", "could you",
                    "can you tell", "what bug", "which bug", "what would you")
    fabricated = len([k for k in r["files"]]) > 0     # created files out of nowhere
    score = 0.6 * (1 if asked else 0) + 0.4 * (0 if fabricated else 1)
    return (score, f"asked:{asked} fabricated-files:{fabricated}")


# --- E. tricky tool selection (sum lines across only the .log files) --------------------
def _setup_toolsel(wd):
    _w(wd, "logs/a.log", "\n".join(["x"] * 4) + "\n")     # 4 lines
    _w(wd, "logs/b.log", "\n".join(["y"] * 5) + "\n")     # 5 lines
    _w(wd, "logs/c.log", "\n".join(["z"] * 6) + "\n")     # 6 lines
    _w(wd, "logs/readme.txt", "\n".join(["ignore"] * 99) + "\n")  # distractor, not .log

def _check_toolsel(r):
    return (1.0 if has(r, "15") else 0.0, f"total-15:{has(r,'15')}")


# --- F. multi-file consistent edit (rename a module everywhere) -------------------------
def _setup_multiedit(wd):
    for n in ("core.py", "api.py", "cli.py"):
        _w(wd, n, f"from oldname import thing\n\ndef run():\n    return thing()  # uses oldname\n")

def _check_multiedit(r):
    files = [file_get(r, n) or "" for n in ("core.py", "api.py", "cli.py")]
    renamed = sum(1 for c in files if "newname" in c and "oldname" not in c)
    return (renamed / 3.0, f"renamed:{renamed}/3")


# --- G. reasoning under indirection (map -> location -> value) --------------------------
def _setup_indirect(wd):
    _w(wd, "map.txt", "The key is stored in the file called drawer3.txt\n")
    _w(wd, "drawer1.txt", "empty\n"); _w(wd, "drawer2.txt", "socks\n")
    _w(wd, "drawer3.txt", "The key is GOLD-777\n")

def _check_indirect(r):
    return (1.0 if has(r, "GOLD-777") else 0.0, f"found-key:{has(r,'GOLD-777')}")


# --- H. precise output (exactly 3 lines, exact content) ---------------------------------
def _check_precision(r):
    c = (file_get(r, "report.txt") or "")
    lines = c.rstrip("\n").split("\n")
    ok = lines == ["A", "B", "C"]
    return (1.0 if ok else (0.5 if all(x in c for x in ("A", "B", "C")) else 0.0),
            f"exact-3-lines:{ok} got={lines!r}")


# --- I. nested JSON extraction ----------------------------------------------------------
def _setup_nested(wd):
    _w(wd, "data.json",
       '{"service":{"db":{"replicas":[{"host":"r1","port":5001},'
       '{"host":"r2","port":5002}]}},"version":3}\n')

def _check_nested(r):
    return (1.0 if has(r, "5002") else 0.0, f"found-r2-port-5002:{has(r,'5002')}")


# --- J. sort + write (compute then persist) ---------------------------------------------
def _setup_sort(wd):
    _w(wd, "numbers.txt", "42\n7\n19\n3\n88\n15\n")

def _check_sort(r):
    c = (file_get(r, "sorted.txt") or "").split()
    ok = c == ["3", "7", "15", "19", "42", "88"]
    return (1.0 if ok else 0.0, f"ascending:{ok} got={c}")


TASKS = [
    dict(id="deep_chain", cat="deep-multistep",
         prompt="Read the number N in count.txt. Then create N files named item_1.txt, item_2.txt, "
                "... item_N.txt, where each item_K.txt contains just the number K. Finally, create "
                "index.txt listing the names of every item file you created, one per line.",
         setup=_setup_deep, check=_check_deep()),
    dict(id="needle_log", cat="long-context",
         prompt="app.log is a long log file. What is the ERROR code recorded in it? Answer with just "
                "the number.",
         setup=_setup_needle, check=_check_needle),
    dict(id="distractor", cat="retrieval",
         prompt="Many files here contain fake api keys. What is the real API_KEY in config/prod.env? "
                "Answer with just the key.",
         setup=_setup_distract, check=_check_distract),
    dict(id="ambiguity", cat="ask-vs-act", review=True,
         prompt="Fix the bug.",
         check=_check_ambiguity),
    dict(id="tool_select_logs", cat="tool-selection",
         prompt="How many lines are there in total across all the .log files in the logs/ directory? "
                "Answer with just the number.",
         setup=_setup_toolsel, check=_check_toolsel),
    dict(id="multifile_rename", cat="multi-file-edit",
         prompt="The module 'oldname' is being renamed to 'newname'. Update every .py file in this "
                "directory so it imports and uses 'newname' instead of 'oldname'.",
         setup=_setup_multiedit, check=_check_multiedit),
    dict(id="indirection", cat="reasoning",
         prompt="Find the key. First read map.txt to learn which file the key is stored in, then read "
                "that file and report the key.",
         setup=_setup_indirect, check=_check_indirect),
    dict(id="precise_lines", cat="instruction-following",
         prompt="Create a file called report.txt containing exactly three lines and nothing else: the "
                "first line is A, the second line is B, the third line is C.",
         check=_check_precision),
    dict(id="nested_json", cat="grounding",
         prompt="In data.json, what is the port of the replica whose host is r2? Answer with just the "
                "number.",
         setup=_setup_nested, check=_check_nested),
    dict(id="sort_write", cat="deep-multistep",
         prompt="Read the numbers in numbers.txt, sort them in ascending order, and write them (one "
                "per line) to a new file called sorted.txt.",
         setup=_setup_sort, check=_check_sort),
]
