"""Robustness to phrasing — the same intent expressed many ways (terse, verbose, blunt, and
typo-ridden). A capable agent should do the SAME correct thing regardless of surface form; a
brittle one cracks on the messy phrasings. Grouped by intent (the `cat`) so per-intent
consistency is visible in the campaign matrix — 4/4 = phrasing-invariant, 1/4 = fragile.

Deterministic file/answer graders, --no-shell, throwaway workspace, platform-agnostic.
Self-test: `python tasks_robustness.py`.
"""
import os
from judge import has, has_any, file_get, file_has


def _w(wd, name, content):
    p = os.path.join(wd, name)
    if os.path.dirname(name):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def _c_create(r):
    ok = file_has(r, "notes.txt", "hello")
    return (1.0 if ok else 0.0, f"notes.txt has hello:{ok}")

def _c_count(r):
    ok = has(r, "3") or has(r, "three")
    return (1.0 if ok else 0.0, f"answered 3:{ok}")

def _c_key(r):
    ok = has(r, "LIVE-KEY-7731") and not has_any(r, "DECOY")
    return (1.0 if ok else 0.0, f"found live key, no decoy:{ok}")

def _c_port(r):
    c = file_get(r, "config.txt") or ""
    ok = "9090" in c and "8080" not in c
    return (1.0 if ok else 0.0, f"port 8080->9090:{ok}")


def _setup_count(wd):
    for i in range(3):
        _w(wd, f"data{i}.csv", "a,b\n")
    _w(wd, "notes.txt", "x"); _w(wd, "run.py", "print(1)")

def _setup_key(wd):
    for i in range(6):
        _w(wd, f"svc{i}.env", f"KEY=DECOY-{i}\n")
    _w(wd, "config/prod.env", "API_KEY=LIVE-KEY-7731\n")
    _w(wd, "config/dev.env", "API_KEY=DECOY-dev\n")

def _setup_port(wd):
    _w(wd, "config.txt", "host = localhost\nport = 8080\ndebug = false\n")


# intent -> (setup, check, [4 phrasings: terse, verbose, blunt, typo])
_INTENTS = {
    "rob-create": (None, _c_create, [
        "notes.txt: hello",
        "Could you please go ahead and create a brand-new text file, name it notes.txt, and inside "
        "it just put the single word hello?",
        "make a file notes.txt that says hello",
        "creat a txt file calld notes.txt wit the word hello in it",
    ]),
    "rob-count": (_setup_count, _c_count, [
        "how many csv files here?",
        "I need to know the total number of comma-separated-value (.csv) files sitting in this "
        "folder — can you count them up for me?",
        "count the .csv's",
        "how many csv fils are in this directroy",
    ]),
    "rob-key": (_setup_key, _c_key, [
        "what's the api key in config/prod.env?",
        "Somewhere in this project there's a production API key — I'm fairly sure it lives in "
        "config/prod.env. Can you dig it out and tell me exactly what it is?",
        "get me the prod api key",
        "whats teh api key in config/prod.env",
    ]),
    "rob-port": (_setup_port, _c_port, [
        "config.txt: change 8080 to 9090",
        "In the configuration file config.txt there's a port currently set to 8080 — please update "
        "that value so it reads 9090 instead, leaving everything else as-is.",
        "swap the port 8080 for 9090 in config.txt",
        "chnage the prot from 8080 to 9090 in config.txt",
    ]),
}

_STYLE = ["terse", "verbose", "blunt", "typo"]
TASKS = []
for intent, (setup, check, phrasings) in _INTENTS.items():
    for i, p in enumerate(phrasings):
        TASKS.append(dict(id=f"{intent}_{_STYLE[i]}", cat=intent, mode="--sealed",
                          prompt=p, check=check, **({"setup": setup} if setup else {})))


def _selftest():
    good = {"rob-create": ({"notes.txt": "hello"}, ""),
            "rob-count": ({}, "there are 3 csv files"),
            "rob-key": ({}, "the key is LIVE-KEY-7731"),
            "rob-port": ({"config.txt": "port = 9090\n"}, "")}
    bad = {"rob-create": ({}, ""),
           "rob-count": ({}, "there are 5"),
           "rob-key": ({}, "DECOY-1"),
           "rob-port": ({"config.txt": "port = 8080\n"}, "")}
    fails = 0
    for t in TASKS:
        fg, tg = good[t["cat"]]; fb, tb = bad[t["cat"]]
        sg, _ = t["check"]({"files": fg, "text": tg, "tools_used": [], "exit": 0})
        sb, _ = t["check"]({"files": fb, "text": tb, "tools_used": [], "exit": 0})
        ok = sg >= 1.0 and sb < 1.0
        if not ok:
            fails += 1; print(f"  GRADER-BUG {t['id']}: good={sg} bad={sb}")
    print(("ALL GRADERS OK" if not fails else f"{fails} GRADER BUGS") + f"  ({len(TASKS)} tasks, {len(_INTENTS)} intents)")
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if _selftest() else 0)
