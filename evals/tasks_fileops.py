"""File-operations robustness — the class of task the user hit failing (create a text file).

Probes create/overwrite/append/read-then-write/multi-file/nested-dir/unicode/spaces behavior with
DETERMINISTIC file-based graders (check the resulting workspace, not the chatter). Every run is
--no-shell, so the model MUST use the file tools (write_file/edit_file/read_file) — it cannot fall
back to a shell one-liner. Platform-agnostic (creates files in a throwaway workspace), so the same
graders are valid on Windows and Kali. Self-test at the bottom: `python tasks_fileops.py`.
"""
import os
from judge import file_get, file_exists, file_has, file_eq, has


def _w(wd, name, content):
    p = os.path.join(wd, name)
    if os.path.dirname(name):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def _norm(s):
    return (s or "").strip().replace("\r\n", "\n")


# ---- checks ---------------------------------------------------------------------------
def _c_simple(r):
    ok = file_eq(r, "notes.txt", "hello world") or _norm(file_get(r, "notes.txt")) == "hello world"
    return (1.0 if ok else 0.0, f"notes.txt=={file_get(r,'notes.txt')!r}")

def _c_spaces(r):
    c = file_get(r, "my report.txt")
    ok = c is not None and "draft" in c.lower()
    return (1.0 if ok else 0.0, f"'my report.txt' exists:{c is not None}")

def _c_nested(r):
    ok = file_has(r, "logs/2026/app.log", "started")
    return (1.0 if ok else 0.0, f"logs/2026/app.log has 'started':{ok}")

def _c_py(r):
    c = (file_get(r, "hello.py") or "")
    ok = "print" in c and "hello" in c.lower()
    return (1.0 if ok else 0.0, f"hello.py prints hello:{ok}")

def _c_overwrite_port(r):
    c = file_get(r, "config.txt") or ""
    ok = "9090" in c and "8080" not in c
    return (1.0 if ok else 0.0, f"port 8080->9090:{ok} content={c!r}")

def _c_multi(r):
    ok = all(file_has(r, f"{n}.txt", n) for n in ("a", "b", "c"))
    return (1.0 if ok else 0.0, f"a/b/c.txt each name themselves:{ok}")

def _c_append(r):
    c = file_get(r, "notes.txt") or ""
    ok = "line1" in c and "line2" in c
    return (1.0 if ok else 0.0, f"kept line1 & added line2:{ok} content={c!r}")

def _c_read_then_write(r):
    ok = file_eq(r, "doubled.txt", "84") or "84" in (file_get(r, "doubled.txt") or "")
    return (1.0 if ok else 0.0, f"doubled.txt has 84:{ok} content={file_get(r,'doubled.txt')!r}")

def _c_json(r):
    import json
    c = file_get(r, "config.json")
    try:
        d = json.loads(c)
        ok = d.get("name") == "redcoder" and str(d.get("version")) in ("1", "1.0")
    except Exception:
        ok = False
    return (1.0 if ok else 0.0, f"config.json valid+keys:{ok} content={c!r}")

def _c_unicode(r):
    c = file_get(r, "résumé.txt")
    ok = c is not None and "cv" in c.lower()
    return (1.0 if ok else 0.0, f"unicode filename created:{c is not None}")

def _c_rename(r):
    ok = file_has(r, "new.txt", "secret42") and not file_exists(r, "old.txt")
    part = file_has(r, "new.txt", "secret42")           # content preserved even if old.txt lingers
    return (1.0 if ok else (0.6 if part else 0.0), f"new.txt has content:{part} old.txt gone:{not file_exists(r,'old.txt')}")

def _c_csv(r):
    c = file_get(r, "data.csv") or ""
    ok = "id,name" in c.replace(" ", "") and "1,alice" in c.replace(" ", "").lower()
    return (1.0 if ok else 0.0, f"data.csv header+row:{ok} content={c!r}")

def _c_dir_readme(r):
    ok = file_has(r, "project/README.md", "hi")
    return (1.0 if ok else 0.0, f"project/README.md says hi:{ok}")

def _c_count(r):
    ok = has(r, "3") or has(r, "three")
    return (1.0 if ok else 0.0, f"answered 3:{ok}")

def _c_overwrite_keep(r):
    ok = file_eq(r, "keep.txt", "replaced") or _norm(file_get(r, "keep.txt")) == "replaced"
    return (1.0 if ok else 0.0, f"keep.txt overwritten to 'replaced':{ok} content={file_get(r,'keep.txt')!r}")

def _c_empty(r):
    c = file_get(r, "placeholder.txt")
    ok = c is not None and _norm(c) == ""
    return (1.0 if ok else 0.0, f"placeholder.txt empty:{ok}")


TASKS = [
    dict(id="fo_simple", cat="create", mode="--sealed",
         prompt="Create a file named notes.txt containing exactly: hello world", check=_c_simple),
    dict(id="fo_spaces", cat="create", mode="--sealed",
         prompt="Create a text file called my report.txt with the text: draft", check=_c_spaces),
    dict(id="fo_nested", cat="create-nested", mode="--sealed",
         prompt="Create a file at logs/2026/app.log containing the single word: started", check=_c_nested),
    dict(id="fo_py", cat="create", mode="--sealed",
         prompt="Write a Python file hello.py that prints hello when run.", check=_c_py),
    dict(id="fo_overwrite_port", cat="edit", mode="--sealed",
         prompt="Change the port in config.txt from 8080 to 9090, leaving the rest unchanged.",
         setup=lambda wd: _w(wd, "config.txt", "host = localhost\nport = 8080\ndebug = false\n"),
         check=_c_overwrite_port),
    dict(id="fo_multi", cat="multi-file", mode="--sealed",
         prompt="Create three files a.txt, b.txt and c.txt, each containing just its own letter name (a, b, c).",
         check=_c_multi),
    dict(id="fo_append", cat="edit", mode="--sealed",
         prompt="Add a second line that says line2 to notes.txt, keeping the existing line1.",
         setup=lambda wd: _w(wd, "notes.txt", "line1\n"), check=_c_append),
    dict(id="fo_read_write", cat="read-then-write", mode="--sealed",
         prompt="Read the number in value.txt, double it, and write the result into doubled.txt.",
         setup=lambda wd: _w(wd, "value.txt", "42\n"), check=_c_read_then_write),
    dict(id="fo_json", cat="create", mode="--sealed",
         prompt="Create config.json — a JSON object with name set to redcoder and version set to 1.",
         check=_c_json),
    dict(id="fo_unicode", cat="create", mode="--sealed",
         prompt="Create a file named résumé.txt containing: cv", check=_c_unicode),
    dict(id="fo_rename", cat="edit", mode="--sealed",
         prompt="Rename old.txt to new.txt, preserving its contents.",
         setup=lambda wd: _w(wd, "old.txt", "secret42\n"), check=_c_rename),
    dict(id="fo_csv", cat="create", mode="--sealed",
         prompt="Create data.csv with a header row id,name and one data row: 1,alice", check=_c_csv),
    dict(id="fo_dir_readme", cat="create-nested", mode="--sealed",
         prompt="Make a folder called project and put a README.md inside it that just says hi.",
         check=_c_dir_readme),
    dict(id="fo_count", cat="inspect", mode="--sealed",
         prompt="How many .log files are in this directory?",
         setup=lambda wd: [ _w(wd, f"x{i}.log", "") for i in range(3) ] and _w(wd, "readme.txt", "x"),
         check=_c_count),
    dict(id="fo_overwrite_keep", cat="overwrite", mode="--sealed",
         prompt="Create keep.txt with the content: replaced",
         setup=lambda wd: _w(wd, "keep.txt", "important original\n"), check=_c_overwrite_keep),
    dict(id="fo_empty", cat="create", mode="--sealed",
         prompt="Create an empty file called placeholder.txt", check=_c_empty),
]


# ---- self-test the graders against reference-correct (and wrong) results before model time -----
def _selftest():
    good = {
        "fo_simple": {"notes.txt": "hello world"},
        "fo_spaces": {"my report.txt": "draft"},
        "fo_nested": {"logs/2026/app.log": "started"},
        "fo_py": {"hello.py": "print('hello')"},
        "fo_overwrite_port": {"config.txt": "host = localhost\nport = 9090\ndebug = false\n"},
        "fo_multi": {"a.txt": "a", "b.txt": "b", "c.txt": "c"},
        "fo_append": {"notes.txt": "line1\nline2\n"},
        "fo_read_write": {"doubled.txt": "84"},
        "fo_json": {"config.json": '{"name": "redcoder", "version": 1}'},
        "fo_unicode": {"résumé.txt": "cv"},
        "fo_rename": {"new.txt": "secret42"},
        "fo_csv": {"data.csv": "id,name\n1,alice\n"},
        "fo_dir_readme": {"project/README.md": "hi"},
        "fo_count": {"_answer": "there are 3 .log files"},
        "fo_overwrite_keep": {"keep.txt": "replaced"},
        "fo_empty": {"placeholder.txt": ""},
    }
    fails = 0
    for t in TASKS:
        files = {k: v for k, v in good[t["id"]].items() if not k.startswith("_")}
        text = good[t["id"]].get("_answer", "")
        r = {"files": files, "text": text, "tools_used": [], "exit": 0}
        s, note = t["check"](r)
        # and a WRONG result should NOT score 1.0
        rw = {"files": {}, "text": "", "tools_used": [], "exit": 0}
        sw, _ = t["check"](rw)
        ok = s >= 1.0 and sw < 1.0
        if not ok:
            fails += 1
            print(f"  GRADER-BUG {t['id']}: good={s} wrong={sw}  ({note})")
        else:
            print(f"  ok {t['id']:20} good=1.0 wrong={sw}")
    print(("ALL GRADERS OK" if not fails else f"{fails} GRADER BUGS") + f"  ({len(TASKS)} tasks)")
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if _selftest() else 0)
