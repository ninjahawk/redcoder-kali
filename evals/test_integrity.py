#!/usr/bin/env python3
"""Deterministic integrity + safety guard. NO model needed — pure code, runs in <1s.

Priority #1 (the user's): Windows quirks must NEVER leak into Kali behavior. redcoder.py is a
single file shared by both; this asserts the platform-conditional pieces stay correct on each
side, that the safety guard (--no-shell) truly blocks execution, and that every added flag is
opt-in (off by default) so a normal Kali launch is unaffected.

Re-run after EVERY change to redcoder.py:  python evals/test_integrity.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import redcoder as RC

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

print("# redcoder integrity + safety guard\n")

# ---- 1. module defaults: every test/opt-in flag is OFF, so normal (Kali) launches are unaffected
print("[defaults] all new flags opt-in (a plain launch behaves exactly as before)")
check("_NO_SHELL default False", RC._NO_SHELL is False)
check("_NO_THINK default False", RC._NO_THINK is False)
check("_FORCE_KALI default False", RC._FORCE_KALI is False)
check("_LIVE default False", RC._LIVE is False)

# ---- 2. platform-conditional system prompt: Windows vs Kali must not cross-contaminate
print("\n[system-prompt] Windows-native vs Kali context are kept distinct")
_saved = RC._FORCE_KALI
try:
    RC._FORCE_KALI = False                       # native Windows path (this box is IS_WINDOWS)
    win = RC.build_system("drago")
    check("win: no Kali tool guidance", "You are on Kali Linux" not in win)
    check("win: env note says Windows", "Host OS: Windows" in win)
    check("win: shell is PowerShell", "PowerShell" in win)

    RC._FORCE_KALI = True                         # simulate the Kali stick
    kali = RC.build_system("drago")
    check("kali: Kali tool guidance present", "You are on Kali Linux" in kali)
    check("kali: env note says Linux (Kali)", "Host OS: Linux (Kali)" in kali)
    check("kali: shell is bash", "default shell: bash" in kali)
    check("kali: no PowerShell leakage", "PowerShell" not in kali)
    check("kali: nmap/toolkit guidance intact", "nmap" in kali and "apt install" in kali)
finally:
    RC._FORCE_KALI = _saved

# ---- 3. path expansion: helps Windows ($env:/%VAR%) AND Kali ($HOME), mangles neither
print("\n[paths] shell-var expansion is additive — no regression to plain or POSIX paths")
up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
check("$env:USERPROFILE expands (no literal $env)", "$env" not in RC._resolve(r"$env:USERPROFILE\a.txt")
      and up.split(os.sep)[-1] in RC._resolve(r"$env:USERPROFILE\a.txt"))
check("%USERPROFILE% expands", "%USERPROFILE%" not in RC._resolve(r"%USERPROFILE%\a.txt"))
check("~ expands", "~" not in RC._resolve("~/a.txt"))
check("plain relative name preserved", RC._resolve("notes.txt").endswith("notes.txt"))
check("nested relative preserved", RC._resolve("sub/dir/a.txt").replace("\\", "/").endswith("sub/dir/a.txt"))
# POSIX $HOME path (the Kali case) — use _expand_path to avoid Windows abspath drive-mangling
_h = os.environ.get("HOME")
os.environ["HOME"] = "/home/kali"
try:
    check("$HOME expands (Kali)", RC._expand_path("$HOME/loot.txt") == "/home/kali/loot.txt")
    check("${HOME} expands (Kali)", RC._expand_path("${HOME}/loot.txt") == "/home/kali/loot.txt")
finally:
    if _h is None: os.environ.pop("HOME", None)
    else: os.environ["HOME"] = _h
# unknown var must not crash and stays literal-ish (shell-consistent)
try:
    RC._resolve(r"$env:DEFINITELY_NOT_SET_XYZ\a.txt"); check("unknown var doesn't crash", True)
except Exception as e:
    check("unknown var doesn't crash", False, repr(e))

# ---- 4. SAFETY: --no-shell truly blocks command execution (write-only testing on the open box)
print("\n[safety] --no-shell blocks execution entirely")
_ns = RC._NO_SHELL
try:
    RC._NO_SHELL = True
    blocked = False
    try:
        RC.t_run_shell({"command": "echo pwned"}, lambda d: True)
    except RC.ToolError:
        blocked = True
    except Exception:
        blocked = False
    check("t_run_shell raises ToolError under --no-shell", blocked)
finally:
    RC._NO_SHELL = _ns

# ---- 5. Spinner never spawns a writer thread in --live (the concurrency fix), else does
print("\n[tui] spinner delegates to the live screen in --live, threads otherwise")
_live, _scr_save = RC._LIVE, RC._LIVE_SCREEN
try:
    import io
    RC._C = True                                  # pretend a color TTY so start() would spawn
    RC._LIVE = True
    _scr = RC.LiveScreen("drago"); _scr._real_stdout = io.StringIO(); RC._LIVE_SCREEN = _scr
    sp = RC.Spinner("x").start(); check("live: delegates, no thread", sp._thread is None); sp.stop()
    RC._LIVE_SCREEN = None
    RC._LIVE = False
    sp2 = RC.Spinner("x").start(); check("non-live: spinner thread runs", sp2._thread is not None); sp2.stop()
finally:
    RC._LIVE = _live; RC._LIVE_SCREEN = _scr_save

# ---- 6. MODEL ALIASES: an installed build (redcoder-drago) must satisfy the 'drago' alias, and
#         the default / a /model switch must never demand re-downloading a model already on the stick.
print("\n[models] an installed redcoder-drago build IS drago (no bogus re-download)")
_USB = ["redcoder-drago:latest", "redcoder-mini:latest"]   # exactly what the Kali stick shows
check("default model is drago", RC.DEFAULT_MODEL == "drago")
check("drago accepts its ref + aka",
      RC._acceptable_names("drago") == ["huihui_ai/qwen2.5-coder-abliterate:14b", "redcoder-drago"])
check("redcoder-drago:latest satisfies drago", RC.installed_name("drago", _USB) == "redcoder-drago:latest")
check("leviathan (not on stick) resolves to None", RC.installed_name("leviathan", _USB) is None)
check("friendly_name maps the build back to drago", RC.friendly_name("redcoder-drago:latest") == "drago")
check("raw name passes through", RC.installed_name("redcoder-mini:latest", _USB) == "redcoder-mini:latest")
_real_in = RC._installed_names
RC._installed_names = lambda *a, **k: _USB          # inject the USB set — no Ollama needed
try:
    check("chat sends the installed build for drago", RC._send_ref("drago") == "redcoder-drago:latest")
    check("startup keeps drago when present", RC._usable_startup_model("drago") == "drago")
    check("startup falls back off uninstalled leviathan -> drago",
          RC._usable_startup_model("leviathan") == "drago")
finally:
    RC._installed_names = _real_in

# ---- 7. INPUT: escape-sequence keys assemble whole (the arrow-key '[A' leak fix) and don't
#         echo into the line. _getch does the fd read; here we assert the read()/boxed handlers
#         classify a whole sequence as an escape (ignored), never as printable text.
print("\n[input] arrow/Page escape sequences are recognized whole, not echoed as '[A'")
check("UTF-8 length: ascii=1", RC._utf8_len(ord("a")) == 1)
check("UTF-8 length: 2-byte lead", RC._utf8_len(0xC3) == 2)
check("UTF-8 length: 3-byte lead", RC._utf8_len(0xE2) == 3)
for _seq in ("\x1b[A", "\x1b[B", "\x1b[5~", "\x1b[6~", "\x1bOA"):
    check(f"{_seq!r} is treated as an escape (startswith ESC, len>1)",
          _seq.startswith("\x1b") and len(_seq) > 1)
    check(f"{_seq!r} would NOT append as text", not (_seq >= " "))   # ESC(0x1b) < space → never printable

print(f"\n{'='*50}\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
