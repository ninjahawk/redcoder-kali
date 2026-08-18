#!/usr/bin/env python3
"""
Redcoder (Kali edition) — offline agentic coding CLI.

A Claude-Code-style terminal agent driven entirely by your local, abliterated
model (`redcoder`) through Ollama. It can read, write, and edit files, list
directories, search, and run shell commands on this machine — all offline.

This build is tuned for running from a Kali Linux live USB with persistence,
against a 12 GB GPU. See README.md for the one-time setup.

Design goals:
  * OFFLINE   — the agent talks only to 127.0.0.1:11434 (local Ollama). Shell
                commands run in one of two DELIBERATE network modes: SEALED
                (default — commands have NO network, enforced by a sandbox) or
                ONLINE (opt in with --online / /net online). See README.md.
  * NO LOGS   — writes nothing to disk except the files YOU ask it to change.
                No transcript, no history file, no telemetry. Memory-only.
  * SELF-CONTAINED — Python standard library only. No pip installs.

Run it by typing `redcoder` in any terminal (see the `redcoder` launcher), or:
    python3 redcoder.py [flags] [prompt...]

Flags (Claude-Code style):
    -p, --print                     non-interactive: run the prompt, print, exit
                                    (reads the prompt from args or stdin)
    -y, --auto,
    --dangerously-skip-permissions  auto-approve ALL writes/edits/shell — no prompts
    -m, --model NAME                use a different Ollama model (default: redcoder)
    --sealed, --offline             SEALED (default): shell commands have NO network
                                    at all (airgap) — enforced by a namespace
    --lab                           OFFLINE LAB: shell commands reach an isolated lab
                                    network (fake targets) but NOT the internet;
                                    verified before use (needs ./lab-net.sh up)
    --online                        ONLINE: shell commands CAN reach the internet.
                                    Deliberate opt-in.
    -C, --cwd DIR                   start in DIR instead of the current directory
    --no-color                      plain output, no ANSI colors
    --no-voice                      disable hold-Space-to-talk voice input
    -v, --version                   print version and exit
    -h, --help                      show this help

Voice: hold-Space-to-talk is Windows-only (it needs msvcrt + GetAsyncKeyState) and is
automatically disabled on Linux. The --no-voice flag is a no-op here. Nothing to install.

Examples:
    redcoder                                        interactive, asks before changes
    redcoder --dangerously-skip-permissions         interactive, never asks
    redcoder -p "explain scan.py"                   one-shot, print and exit
    git diff | redcoder -p "review this diff"       pipe input via stdin

Privacy: nothing you type at the `>` prompt is written to disk — no transcript,
history, or telemetry; it's gone when the process exits. (Passing a prompt as a
command-line argument may be recorded by your shell's own history; type it at the
prompt instead to avoid that.)
"""

import ctypes
import datetime
import difflib
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

try:
    import msvcrt  # Windows console key input (for hold-Space-to-talk)
except ImportError:
    msvcrt = None

# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
VERSION = "1.0"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TAGS_URL = "http://127.0.0.1:11434/api/tags"
DEFAULT_MODEL = "redcoder-drago"   # the 14B, built by config/Modelfile.redcoder-drago; /model to switch

# Context window. THIS IS THE SETTING THAT DECIDES WHETHER YOU GET GPU SPEED.
#
# redcoder sends num_ctx in the API options, which OVERRIDES whatever the Modelfile
# says — so raising this here silently overrides config/Modelfile.redcoder-drago too.
#
# Budget on a 12 GB card with the 14B at Q4_K_M (~8.4 GB of weights):
#     KV cache costs ~192 KB per token (48 layers x 8 KV heads x 128 dim, fp16)
#       8192 ctx -> ~1.5 GB  ->  ~9.9 GB total   fits comfortably
#      16384 ctx -> ~3.1 GB  -> ~11.5 GB total   fits, tight (use q8_0 KV cache)
#      32768 ctx -> ~6.1 GB  -> ~14.5 GB total   DOES NOT FIT — spills to CPU,
#                                                 several times slower
#
# Auto-compaction (COMPACT_AT below) handles conversations longer than the window.
# After changing this, verify with `ollama ps` — you want PROCESSOR = 100% GPU.
NUM_CTX = 8192
MAX_STEPS = 25          # a real task rarely needs more; a small model wandering does
# Derived from the context window, never a fixed number — that is how it drifted before.
# At ~4 chars/token, NUM_CTX*4//8 keeps one tool result to ~1/8 of the window (4096 chars
# @ 8192 ctx). The old fixed 20000 was ~5000 tokens = 61% of an 8192 window, so a single
# big grep/read nearly hit the compaction threshold by itself and triggered the
# compaction-driven repetition loop.
MAX_TOOL_OUTPUT = NUM_CTX * 4 // 8   # chars of tool output fed back to the model
CONSOLE_MAX_LINES = 400  # how many lines of a tool call/result to echo to the screen

# Loop control — small models get stuck repeating the same call. We fingerprint each
# tool call (name + arguments); on the 2nd identical call we inject a hard nudge, on the
# 3rd we stop the turn and force a final answer. This is enforced in code, not left to
# the prompt, because a small model will not reliably obey "don't repeat yourself".
REPEAT_NUDGE_AT = 2     # inject a "you already did this" nudge on this many identical calls
REPEAT_STOP_AT = 3      # abort the turn on this many identical calls

# Small models often signal "I am done" by inventing a fake tool (e.g. {"name":"stop"})
# instead of just replying in plain text. _coerce_action rejects such names, so they land
# in the "final answer" path; there we detect these and end the turn cleanly rather than
# printing the raw json as the reply. (TOOL_NAMES itself is defined with the tool schema.)
STOP_TOOL_WORDS = {"stop", "done", "finish", "finished", "end", "complete", "completed",
                   "final", "final_answer", "answer", "respond", "reply", "none", "noop",
                   "exit", "quit"}

# Never persist anything. Belt-and-braces: disable Ollama's REPL history too
# (we use the HTTP API so this is moot, but it costs nothing to be explicit).
os.environ.setdefault("OLLAMA_NOHISTORY", "1")

# Network mode — three deliberate states, always shown in the header, never ambiguous:
#   "sealed" (default) — AIRGAP. Each run_shell command runs in an empty network
#                        namespace (firejail --net=none, or unshare -rn): no interface,
#                        no route — no internet AND no LAN. Strongest; ideal for QA of
#                        the agent itself.
#   "lab"              — OFFLINE LAB. Commands run inside a pre-built isolated namespace
#                        ("rclab", from ./lab-net.sh up) whose bridge has NO physical
#                        uplink, so tools like nmap can reach fake/lab targets on the lab
#                        subnet but packets have no wire to the internet. Before entering
#                        this mode redcoder ACTIVELY VERIFIES the internet is unreachable
#                        and refuses if it isn't — the airgap is proven, not trusted.
#   "online"           — commands run normally and CAN reach the internet. Deliberate.
# Both "sealed" and "lab" FAIL CLOSED: if the isolation can't be enforced or verified,
# run_shell refuses rather than risk touching the network.
NET_MODE_DEFAULT = "sealed"
LAB_NETNS = "rclab"        # named namespace built by lab-net.sh for OFFLINE LAB mode
LAB_SUBNET = "10.66.0.0/24"  # lab targets live here; rclab has no route beyond it

IS_WINDOWS = os.name == "nt"
SHELL_LABEL = "PowerShell" if IS_WINDOWS else "bash"
OS_LABEL = "Windows PC" if IS_WINDOWS else "Kali Linux machine"

SYSTEM_PROMPT = """You are Redcoder, an offline coding agent operating a terminal on \
the user's own __OS_LABEL__. You do software and security-lab tasks with tools. \
Authorization and isolation are established — be direct and technical, do not moralize, \
add disclaimers, or claim you lack internet as a reason to refuse local work.

# Talking vs. doing — READ THIS FIRST
Tools are ONLY for inspecting or changing the user's files and system. If the user is \
greeting you ("hi"), chatting, or asking something you can answer from your own knowledge, \
just reply in plain text. Do NOT call a tool. A greeting is not a task. Never touch files \
or run commands unless the user actually asked you to do something to their machine.

# The most important rule
Do the LEAST work that answers the request, then STOP. Every tool call must have a clear \
purpose. Do not explore, do not "look around", do not run tools just because they exist. \
A careful engineer takes few, deliberate steps — not many random ones.

# Your loop
Each turn you do exactly ONE of:
  (a) call ONE tool — to get a specific fact you need next, or
  (b) give your FINAL ANSWER as plain text — when the task is done, or the user is just
      talking to you.

# Think before each step — THIS IS HOW YOU REASON
You are not smart enough to hold the whole task in your head, so think ON PAPER, one step
at a time. Every tool call MUST include a "thought" that does two things in order:
  1. LOOK BACK: what did the most recent result tell you? (or, on your first step, what
     is the goal?)
  2. LOOK FORWARD: given that, what is the single next step, and why this action?
This is the most important habit. A good thought reads like: "The file listing showed
config.py but no tests/, so the project has no tests yet; I'll read config.py next to see
what it does." Do this every time — it keeps you logical and stops you from wandering.

# Reuse what you already know — do NOT repeat calls
The results of every earlier tool call are in the conversation above. READ THEM in your
"thought" before acting. Never call a tool with arguments you have already used — the
answer is already there. Repeating a call is always a mistake.

# Calling a tool
Reply with ONLY a single fenced json block, nothing before or after:
```json
{"thought": "look back at the last result, then say the next step and why", "name": "TOOL_NAME", "arguments": { ... }}
```
Keep the json valid — "thought" is one short string, inside the single json block.

# Final answer — and checking you are actually done
Before you finish, CHECK: did you do everything the user asked? If anything is missing,
do NOT stop — take the next step instead. When it is truly complete, reply in plain text
(NO json block), briefly: what you did, and confirm the task is done. Concise Markdown.

# Tools
- read_file   {"path": str, "offset"?: int, "limit"?: int}   read a text file
- write_file  {"path": str, "content": str}                  create/overwrite a file
- edit_file   {"path": str, "old": str, "new": str}          replace exact text once
- list_dir    {"path"?: str}                                 list a directory
- glob        {"pattern": str}                               find files by glob (recursive)
- grep        {"pattern": str, "path"?: str}                 regex search file contents
- run_shell   {"command": str}                               run a __SHELL_LABEL__ command

# Rules
- Plan first: in your head, name the goal and the smallest set of steps to reach it.
- read_file or grep BEFORE editing, so edit_file's "old" text matches exactly.
- edit_file replaces the FIRST exact match of "old"; include enough context to be unique.
- run_shell runs __SHELL_LABEL__. Use __SHELL_LABEL__ syntax. Prefer reading files with the \
file tools over shelling out.
- Be careful with destructive commands (deleting, formatting, overwriting). Do not run \
them unless the task truly requires it; the user will be asked to confirm.
- The moment you can answer, answer. Do not keep calling tools to look thorough.
- Paths may be relative to the current working directory.
"""

# Filled in after the fact because SYSTEM_PROMPT contains literal { } braces,
# which would break an f-string.
SYSTEM_PROMPT = (SYSTEM_PROMPT
                 .replace("__OS_LABEL__", OS_LABEL)
                 .replace("__SHELL_LABEL__", SHELL_LABEL))

# Appended only on Linux. Kali ships a large native toolkit and runs here from a live
# USB, both of which change what good behaviour looks like.
KALI_NOTES = """
# This machine
You are on Kali Linux, booted from a live USB with a persistence overlay.

## Use the tools that are already here
Kali ships hundreds of preinstalled security and system tools. Before writing a script
to do something, check whether a tool already exists and prefer it:
  which <tool>            is it installed and on PATH?
  apt-cache policy <pkg>  is it packaged, and what version?
Reach for the standard toolkit (nmap, ffuf, gobuster, sqlmap, hashcat, john, netcat,
tcpdump, tshark, binwalk, radare2, gdb, openssl, jq, ripgrep, curl, git, ...) rather
than reimplementing it. If a tool is missing, `sudo apt install -y <pkg>` works and
persists across reboots.

## Disk space is limited
Everything you install or download lands on the persistence partition, which is small
(tens of GB, shared with the model weights). Run `df -h /` before installing anything
large, and tell the user if space is getting tight instead of silently filling it.

## Stay in your workspace
Work only with files inside the current working directory and files the user explicitly
names. Do not run commands that mount, format, partition, or write to raw disk devices.
If a task seems to need any of that, stop and ask the user first.

## This is not an anonymity system
Kali routes traffic normally — there is no Tor by default, no MAC randomisation. Do not
tell the user their traffic is anonymous, and do not treat this as a privacy tool.

## Privileges
The user is `kali` and has sudo. Prefix commands with sudo only when they genuinely need
root, and say why when you do.
"""

if not IS_WINDOWS:
    SYSTEM_PROMPT = SYSTEM_PROMPT.rstrip() + "\n" + KALI_NOTES


def build_system():
    """SYSTEM_PROMPT plus a line telling the model the current network mode, so it
    does not waste steps attempting work the mode won't allow."""
    if _NET_MODE == "sealed":
        net = ("# Network access\n"
               "SEALED (airgap) mode: shell commands have NO network at all — not even a "
               "LAN. Any attempt to reach a host, the internet, or a lab target WILL fail. "
               "Do only local work; do not try downloads, apt installs, or remote scans.")
    elif _NET_MODE == "lab":
        net = ("# Network access\n"
               f"LAB mode: shell commands run in an ISOLATED OFFLINE lab network. You can "
               f"reach fake/lab targets on {LAB_SUBNET} (scan, probe, exploit them freely), "
               f"but there is NO internet — public hosts and DNS are unreachable and apt "
               f"will fail. Point network tools at {LAB_SUBNET}, not at public addresses.")
    else:
        net = ("# Network access\n"
               "ONLINE mode: shell commands can reach the network. Still prefer local "
               "work — only go online when the task actually requires it.")
    return SYSTEM_PROMPT.rstrip() + "\n\n" + net

# --------------------------------------------------------------------------- #
#  Terminal color
# --------------------------------------------------------------------------- #
def _enable_vt():
    if os.name == "nt":
        try:
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            k.SetConsoleOutputCP(65001)               # UTF-8 out
            k.SetConsoleCP(65001)                      # UTF-8 in
        except Exception:
            pass
    for stream in (sys.stdout, sys.stdin, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_enable_vt()
_C = sys.stdout.isatty()
_NET_MODE = NET_MODE_DEFAULT   # "sealed" | "lab" | "online"; set in main(), toggled by /net


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if _C else text


def red(t):    return c(t, "38;5;203")
def dim(t):    return c(t, "2")
def bold(t):   return c(t, "1")
def ital(t):   return c(t, "3")
def cyan(t):   return c(t, "38;5;80")
def green(t):  return c(t, "38;5;114")
def yellow(t): return c(t, "38;5;179")
def grey(t):   return c(t, "38;5;244")
def blue(t):   return c(t, "38;5;75")
def purple(t): return c(t, "38;5;141")
def orange(t): return c(t, "38;5;215")
def pink(t):   return c(t, "38;5;211")


# --------------------------------------------------------------------------- #
#  Markdown -> terminal (ANSI) rendering
# --------------------------------------------------------------------------- #
def render_inline(text):
    """Convert inline Markdown to ANSI. When color is off, this simply strips
    the markers (bold()/yellow()/etc. return their input unchanged)."""
    text = re.sub(r"`([^`]+)`", lambda m: yellow(m.group(1)), text)          # `code`
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: bold(m.group(1)), text)      # **bold**
    text = re.sub(r"__([^_]+)__", lambda m: bold(m.group(1)), text)          # __bold__
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: ital(m.group(1)), text)  # *italic*
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",                                # [text](url)
                  lambda m: bold(m.group(1)) + dim(" (" + m.group(2) + ")"), text)
    return text


def render_md_line(line, state):
    """Render ONE Markdown line to an indented ANSI line, tracking fenced-code
    state across calls via the mutable `state` dict (so streaming works)."""
    s = line.strip()
    if s.startswith("```"):
        state["code"] = not state["code"]
        lang = s[3:].strip()
        body = (dim("┌─ " + (lang or "code") + " " + "─" * max(3, 18 - len(lang)))
                if state["code"] else dim("└" + "─" * 22))
        return "  " + body
    if state["code"]:
        return "  " + cyan("│ ") + cyan(line)
    m = re.match(r"^(#{1,6})\s+(.*)$", line)
    if m:
        return "  " + bold(green(render_inline(m.group(2))))
    if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
        return "  " + dim("─" * 22)
    m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
    if m:
        return "  " + m.group(1) + green("• ") + render_inline(m.group(2))
    m = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
    if m:
        return "  " + m.group(1) + green(m.group(2) + ". ") + render_inline(m.group(3))
    return "  " + render_inline(line)


def render_markdown(md):
    """Render a whole Markdown string to an indented, ANSI-formatted block."""
    state = {"code": False}
    return "\n".join(render_md_line(ln, state) for ln in md.split("\n"))


# --------------------------------------------------------------------------- #
#  Ollama chat (streaming)
# --------------------------------------------------------------------------- #
def ollama_chat(model, messages, on_token=None):
    """Stream a chat completion. Returns the full assistant content string.

    If on_token is given it is called with each text chunk as it arrives.
    Also collects any native tool_calls the model may emit (fallback path).
    """
    body = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.4, "num_ctx": NUM_CTX},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    content = []
    tool_calls = []
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    content.append(piece)
                    if on_token:
                        on_token(piece)
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])
                if obj.get("error"):
                    raise RuntimeError(obj["error"])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Ollama IS up; it just doesn't have this model.
            raise RuntimeError(
                f"Model '{model}' not found. Ollama is running but that model doesn't "
                f"exist. Create it (e.g. `ollama cp <existing> {model}` or "
                f"`ollama create {model} -f config/Modelfile.{model}`), or pick another "
                f"with /model."
            )
        raise RuntimeError(f"Ollama returned HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach Ollama at 127.0.0.1:11434 ({getattr(e, 'reason', e)}). "
            f"Start it with `ollama serve` (or `systemctl start ollama`)."
        )
    return "".join(content), tool_calls


# --------------------------------------------------------------------------- #
#  Action parsing (JSON protocol + native fallback)
# --------------------------------------------------------------------------- #
TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "list_dir", "glob", "grep", "run_shell",
}


def _coerce_action(obj):
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if name in TOOL_NAMES:
        args = obj.get("arguments")
        if args is None:
            args = {k: v for k, v in obj.items() if k not in ("name", "thought", "why")}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        act = {"name": name, "arguments": args if isinstance(args, dict) else {}}
        # The model's step-by-step reasoning, shown before the call. Prefer "thought";
        # accept "why" as a fallback so older phrasings still surface something.
        thought = obj.get("thought") or obj.get("why")
        if isinstance(thought, str) and thought.strip():
            act["thought"] = thought.strip()
        return act
    return None


def _iter_json_objects(text):
    """Yield each top-level {...} substring, scanning with a brace counter that
    respects string literals/escapes. Robust to fences and to '}' inside strings
    (e.g. f-string braces like {port} in a code payload) — which a regex is not."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc, j = 0, False, False, i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1
        i = j + 1


# Required string args per tool; a recovered call missing any of these is unusable.
REQUIRED_ARGS = {
    "read_file": ("path",), "write_file": ("path", "content"),
    "edit_file": ("path", "old", "new"), "list_dir": (),
    "glob": ("pattern",), "grep": ("pattern",), "run_shell": ("command",),
}
_STRING_ARG_KEYS = ("path", "content", "old", "new", "pattern", "command")


def _string_value(text, key):
    """Extract a JSON string value for `key` from possibly-malformed/truncated `text`.

    Returns (value, complete) or None if the key isn't present. `complete` is False
    when the string was cut off before its closing quote (a truncated payload). This
    reads the string body directly rather than requiring the whole surrounding object
    to be valid JSON, so a stray character elsewhere doesn't lose the value."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if not m:
        return None
    frag = text[m.end():]
    end = _json_str_end(frag)
    complete = end is not None
    escaped = frag if end is None else frag[:end]
    if end is None:
        # drop a dangling odd backslash so the partial fragment still decodes
        if (len(escaped) - len(escaped.rstrip("\\"))) % 2 == 1:
            escaped = escaped[:-1]
    try:
        return json.loads('"' + escaped + '"'), complete
    except Exception:
        return None


def recover_action(content):
    """Best-effort recovery of a tool call when strict JSON parsing failed because the
    model emitted slightly malformed or truncated JSON (common on large write payloads
    from a local model). Returns an action dict — with '_truncated' set if a value was
    cut off — or None if the text isn't a tool call at all. This is what stops a broken
    write_file from being silently dropped and mistaken for a final answer."""
    m = re.search(r'"name"\s*:\s*"([a-zA-Z_]+)"', content)
    if not m or m.group(1) not in TOOL_NAMES:
        return None
    name = m.group(1)
    args, truncated = {}, False
    for key in _STRING_ARG_KEYS:
        got = _string_value(content, key)
        if got is None:
            continue
        args[key], complete = got
        if not complete:
            truncated = True
    for key in ("offset", "limit"):  # numeric read_file args, best effort
        nm = re.search(r'"' + key + r'"\s*:\s*(\d+)', content)
        if nm:
            args[key] = int(nm.group(1))
    if any(k not in args for k in REQUIRED_ARGS.get(name, ())):
        return None
    action = {"name": name, "arguments": args}
    if truncated:
        action["_truncated"] = True
    return action


def extract_action(content, native_tool_calls):
    """Return {'name','arguments'} if the model wants a tool, else None.

    A '_truncated' key on the result means a tool call was recognized but its JSON was
    cut off mid-value — the caller must NOT execute it, but must also NOT treat it as a
    final answer (that is the silent-drop bug this guards against)."""
    # 1) native tool_calls, if the model emitted them
    for tc in native_tool_calls or []:
        fn = (tc or {}).get("function") or {}
        act = _coerce_action({"name": fn.get("name"), "arguments": fn.get("arguments")})
        if act:
            return act
    # 2) any balanced {...} object in the text (with or without ``` fences)
    for blob in _iter_json_objects(content):
        try:
            act = _coerce_action(json.loads(blob))
        except json.JSONDecodeError:
            act = None
        if act:
            return act
    # 3) recovery: a malformed/truncated tool call must not be silently dropped
    return recover_action(content)


def looks_like_action_start(prefix):
    """Cheap early check so we can hide raw JSON while streaming a tool call."""
    s = prefix.lstrip()
    return s.startswith("```") or s.startswith("{")


# --------------------------------------------------------------------------- #
#  Tools (executed locally)
# --------------------------------------------------------------------------- #
class ToolError(Exception):
    pass


def _resolve(path):
    return os.path.abspath(os.path.expanduser(path or "."))


def t_read_file(args):
    path = _resolve(args["path"])
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    offset = int(args.get("offset", 0) or 0)
    limit = args.get("limit")
    sel = lines[offset: offset + int(limit)] if limit else lines[offset:]
    out = "".join(f"{i+offset+1}\t{ln}" for i, ln in enumerate(sel))
    return out if out.strip() else "(empty file)"


def t_write_file(args, approve):
    path = _resolve(args["path"])
    content = args.get("content", "")
    exists = os.path.exists(path)
    verb = "Overwrite" if exists else "Create"
    if not approve(f"{verb} file {path}  ({len(content)} bytes)"):
        raise ToolError("User declined the write.")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return f"{'Overwrote' if exists else 'Created'} {path} ({len(content)} bytes)."


def t_edit_file(args, approve):
    path = _resolve(args["path"])
    old, new = args.get("old", ""), args.get("new", "")
    with open(path, "r", encoding="utf-8") as f:
        data = f.read()
    if old not in data:
        raise ToolError("The 'old' text was not found exactly. Read the file and retry.")
    if data.count(old) > 1:
        raise ToolError("The 'old' text appears multiple times; add more context to make it unique.")
    if not approve(f"Edit file {path}  (replace {len(old)}→{len(new)} chars)"):
        raise ToolError("User declined the edit.")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data.replace(old, new, 1))
    return f"Edited {path}."


def t_list_dir(args):
    path = _resolve(args.get("path", "."))
    entries = sorted(os.listdir(path))
    rows = []
    for name in entries:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            rows.append(f"{name}/")
        else:
            try:
                rows.append(f"{name}  ({os.path.getsize(full)} b)")
            except OSError:
                rows.append(name)
    return f"{path}\n" + "\n".join(rows) if rows else f"{path}\n(empty)"


def t_glob(args):
    pattern = args["pattern"]
    root = _resolve(args.get("path", "."))
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(fn, pattern):
                hits.append(rel)
        if len(hits) > 500:
            break
    return "\n".join(sorted(hits)) if hits else "(no matches)"


def t_grep(args):
    rx = re.compile(args["pattern"])
    root = _resolve(args.get("path", "."))
    out = []
    targets = [root] if os.path.isfile(root) else None
    if targets is None:
        targets = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for fn in filenames:
                targets.append(os.path.join(dirpath, fn))
    for fp in targets:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if rx.search(line):
                        rel = os.path.relpath(fp, root) if os.path.isdir(_resolve(args.get("path", "."))) else fp
                        out.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(out) > 300:
                            return "\n".join(out) + "\n(truncated)"
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(out) if out else "(no matches)"


# Patterns that are destructive or irreversible. A small model WILL eventually emit one
# of these; this backstop forces an explicit confirmation (even in auto mode) rather than
# trusting the system prompt alone. Not a security boundary — a guardrail against mistakes.
_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*\s+)*-\w*[rf]", "recursive/forced delete (rm -rf)"),
    (r"\bmkfs\b", "format a filesystem (mkfs)"),
    (r"\bdd\b.*\bof=", "raw disk write (dd of=)"),
    (r"\b(shred|wipefs)\b", "wipe data (shred/wipefs)"),
    (r">\s*/dev/(nvme|sd|mapper)", "write to a raw disk device"),
    (r"\b(fdisk|parted|sfdisk|gdisk)\b", "repartition a disk"),
    (r"\bmount\b|\bumount\b", "mount/unmount a filesystem"),
    (r":\(\)\s*\{.*\|.*&\s*\}", "fork bomb"),
    (r"\bchmod\s+-R\b|\bchown\s+-R\b", "recursive permission change"),
    (r"\bcryptsetup\b", "disk encryption operation"),
    (r"/dev/(nvme|sd)[a-z0-9]", "reference to a raw disk device"),
    (r"\b(shutdown|reboot|poweroff|halt)\b", "power off / reboot the machine"),
    (r"\bapt\b.*\b(remove|purge)\b|\bdpkg\b.*-r", "remove system packages"),
]


def _dangerous_reason(command):
    """Return a human reason if the command matches a dangerous pattern, else None."""
    for pat, reason in _DANGEROUS_PATTERNS:
        if re.search(pat, command):
            return reason
    return None


def _list_netns():
    """Names of the persistent network namespaces (created via `ip netns add`)."""
    try:
        out = subprocess.run(["ip", "netns", "list"], capture_output=True,
                             text=True, timeout=5)
        return {ln.split()[0] for ln in out.stdout.splitlines() if ln.strip()}
    except Exception:
        return set()


def _net_prefix(mode):
    """argv prefix that ENFORCES `mode` for a shell command, plus a status.

    Returns (prefix_argv, label, error):
      prefix_argv  argv to prepend to the real command ([] = run as-is), or None
      label        "firejail" | "unshare" | "firejail-lab" | "online" | "windows" | "none"
      error        None, or a message; if set, the caller MUST refuse (fail closed).

    - online: no wrapper.
    - sealed: empty net namespace (firejail --net=none, or unshare -rn) — airgap.
    - lab:    join the pre-built isolated 'rclab' namespace (firejail --netns).
    Isolation is Linux-only; on Windows the caller warns and does not enforce.
    """
    if mode == "online":
        return [], "online", None
    if IS_WINDOWS:
        return [], "windows", None
    fj = shutil.which("firejail")
    if mode == "sealed":
        if fj:
            return [fj, "--quiet", "--net=none", "--"], "firejail", None
        un = shutil.which("unshare")
        if un:
            # -r: rootless user namespace (no sudo); -n: fresh net namespace (lo down).
            return [un, "-rn", "--"], "unshare", None
        return None, "none", ("SEALED (airgap) mode needs firejail or unshare to remove "
                              "the network; neither is installed. Install one "
                              "(sudo apt install -y firejail) or use /net online.")
    if mode == "lab":
        if not fj:
            return None, "none", ("LAB mode needs firejail to join the isolated lab "
                                  "namespace; install it: sudo apt install -y firejail")
        if LAB_NETNS not in _list_netns():
            return None, "none", (f"LAB mode needs the '{LAB_NETNS}' namespace, which does "
                                  f"not exist. Build the offline lab first:  ./lab-net.sh up")
        return [fj, "--quiet", f"--netns={LAB_NETNS}", "--"], "firejail-lab", None
    return None, "none", f"unknown network mode: {mode}"


def _lab_egress_open():
    """True if the internet is REACHABLE from the lab namespace (i.e. UNSAFE).

    Opens a bash /dev/tcp connection to public IPs with a short timeout — needs no
    curl, ping, or root. Any successful connect means the airgap is NOT holding, so
    lab mode must be refused. Errs toward 'blocked' (safe) if it cannot test.
    """
    fj = shutil.which("firejail")
    if not fj or LAB_NETNS not in _list_netns():
        return False
    probe = ("for ip in 1.1.1.1 8.8.8.8 9.9.9.9; do "
             "timeout 2 bash -c \"exec 3<>/dev/tcp/$ip/443\" 2>/dev/null && exit 0; "
             "done; exit 1")
    try:
        r = subprocess.run([fj, "--quiet", f"--netns={LAB_NETNS}", "--",
                            "bash", "-lc", probe],
                           capture_output=True, text=True, timeout=12)
        return r.returncode == 0   # 0 = a connect succeeded = internet reachable = UNSAFE
    except Exception:
        return False


def activate_lab():
    """Attempt to enter LAB mode, verifying the airgap. Returns (ok, message).
    Refuses (ok=False) unless firejail + the rclab namespace exist AND the internet
    is provably unreachable from inside it."""
    if IS_WINDOWS:
        return False, "LAB mode is Linux-only."
    if not shutil.which("firejail"):
        return False, "LAB mode needs firejail — sudo apt install -y firejail"
    if LAB_NETNS not in _list_netns():
        return False, (f"lab namespace '{LAB_NETNS}' not found — build it first: ./lab-net.sh up")
    if _lab_egress_open():
        return False, ("REFUSED — the internet is REACHABLE from the lab namespace; the "
                       "airgap is NOT holding. Fix ./lab-net.sh / routing before lab use.")
    return True, (f"lab '{LAB_NETNS}' verified — reaches {LAB_SUBNET} targets, internet "
                  f"proven unreachable.")


def t_run_shell(args, approve):
    command = args["command"]
    danger = _dangerous_reason(command)
    if danger:
        ok = approve(f"Run shell command — {danger}:\n    {command}", force=True)
    else:
        ok = approve(f"Run shell command:\n    {command}")
    if not ok:
        raise ToolError("User declined the command.")

    if IS_WINDOWS:
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        # bash if present (Kali ships it), otherwise whatever /bin/sh is.
        argv = [shutil.which("bash") or "/bin/sh", "-lc", command]

    # Enforce the current network mode. Fail closed — never silently run with network
    # when the user asked for sealed/lab.
    prefix, backend, err = _net_prefix(_NET_MODE)
    if err:
        raise ToolError(err)
    if backend == "windows" and _NET_MODE != "online":
        print(yellow("    ⚠ network isolation is Linux-only and is NOT enforced on "
                     "Windows — this command may reach the internet"))
    elif prefix:
        argv = prefix + argv

    suffix = {"sealed": " · airgap", "lab": " · lab"}.get(_NET_MODE, "")
    spin = Spinner("running command" + suffix, color=orange).start()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=600,
        )
    finally:
        spin.stop()
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    out = out.strip() or "(no output)"
    return f"exit={proc.returncode}\n{out}"


def run_tool(action, approve):
    name = action["name"]
    args = action["arguments"]
    if name == "read_file":
        return t_read_file(args)
    if name == "write_file":
        return t_write_file(args, approve)
    if name == "edit_file":
        return t_edit_file(args, approve)
    if name == "list_dir":
        return t_list_dir(args)
    if name == "glob":
        return t_glob(args)
    if name == "grep":
        return t_grep(args)
    if name == "run_shell":
        return t_run_shell(args, approve)
    raise ToolError(f"Unknown tool: {name}")


def summarize_action(action):
    """Short one-line description for the terminal."""
    n, a = action["name"], action["arguments"]
    if n in ("read_file", "list_dir"):
        return f"{n} {a.get('path', '.')}"
    if n == "write_file":
        return f"write_file {a.get('path')}"
    if n == "edit_file":
        return f"edit_file {a.get('path')}"
    if n == "glob":
        return f"glob {a.get('pattern')}"
    if n == "grep":
        return f"grep /{a.get('pattern')}/ in {a.get('path', '.')}"
    if n == "run_shell":
        return f"run_shell {a.get('command')}"
    return n


# --------------------------------------------------------------------------- #
#  Approval
# --------------------------------------------------------------------------- #
class Approver:
    def __init__(self, auto):
        self.auto = auto

    def __call__(self, description, force=False):
        # `force` = a dangerous command. Always confirm explicitly, even in auto mode,
        # and never let "a=always" apply to it — auto-approve must not cover destructive
        # actions.
        if self.auto and not force:
            print(dim(f"    auto ✓ {description.splitlines()[0]}"))
            return True
        border = red if force else yellow
        tint = red if force else yellow
        if force:
            print(red("    ┌─ ⚠ DANGEROUS COMMAND — confirm even in auto mode ─────"))
        else:
            print(tint("    ┌─ permission ─────────────────────────────────"))
        for ln in description.splitlines():
            print(tint("    │ ") + ln)
        print(border("    └──────────────────────────────────────────────"))
        prompt = "    Run this dangerous command? [y/N] " if force else "    Allow? [Y/n/a=always] "
        try:
            ans = input(bold(prompt)).strip().lower()
        except EOFError:
            return False
        if force:
            return ans in ("y", "yes")   # dangerous: default No, no "always"
        if ans in ("a", "always"):
            self.auto = True
            return True
        return ans in ("", "y", "yes")


# --------------------------------------------------------------------------- #
#  Live streaming + full-detail display  (show everything, like Claude Code)
# --------------------------------------------------------------------------- #
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """A live animation that runs on its own thread while the model is 'thinking'
    (before/between visible output), like Claude Code's working indicator."""

    def __init__(self, label="thinking", color=purple):
        self.label = label
        self.color = color
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if _C:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = _SPIN[i % len(_SPIN)]
            avail = _term_width() - 5          # room after the "  X " prefix
            text = f"{self.label}…"
            hint = "  (Ctrl-C to interject)"
            if len(text) + len(hint) <= avail:
                shown = grey(text) + dim(hint)
            elif len(text) <= avail:
                shown = grey(text)
            else:
                shown = grey(text[:max(0, avail - 1)] + "…")
            # \033[K clears to end of line so nothing wraps or lingers.
            sys.stdout.write("\r" + self.color(f"  {frame}") + " " + shown + "\033[K")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.09)

    def set_label(self, label):
        self.label = label

    def stop(self):
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)
        if _C:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


def _json_str_end(s):
    """Index of the first unescaped '\"' in s (the end of a JSON string), or None."""
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return None


# tool -> ordered list of (json key, color, prefix) to stream live as a diff
_STREAM_PLANS = {
    "write_file": [("content", green, "+ ")],
    "edit_file": [("old", red, "- "), ("new", green, "+ ")],
}


class StreamPrinter:
    """Streams the assistant's reply live as it arrives, so SOMETHING is always moving
    on screen (no silent stalls):
      * PROSE → rendered Markdown, line by line.
      * write_file / edit_file → the payload streams live as a colored diff
        (green + additions, red - removals) while it's generated — even if the model
        prefaced it with prose.
      * any other tool call → the animated spinner keeps running until the action is
        shown by agent_turn.
    Owns its own spinner and keeps it alive across prose→tool transitions."""

    def __init__(self):
        self.raw = ""
        self.mode = None            # None | "prose" | "tool"
        self.md = {"code": False}
        self.printed = False
        self.prose_at = 0
        self.spinner = Spinner("thinking").start()
        # tool state
        self.displayed = False
        self.tool_name = None
        self.plan = None            # ordered stream targets for this tool
        self.plan_i = 0
        self.header_shown = False
        self.val_start = None       # start index of the current value's body in raw
        self.scan = 0               # next unprocessed char of the current value
        self.esc = False            # decoder is mid-escape across a token boundary
        self.search_from = 0        # where to start looking for the next plan key
        self.line_buf = ""
        self.added = 0
        self.removed = 0
        self.capped = False

    # ---- spinner helpers -------------------------------------------------- #
    def _spin(self, label):
        if self.spinner is None:
            self.spinner = Spinner(label).start()
        else:
            self.spinner.set_label(label)

    def _kill_spinner(self):
        if self.spinner is not None:
            self.spinner.stop()
            self.spinner = None

    # ---- dispatch --------------------------------------------------------- #
    def feed(self, tok):
        self.raw += tok
        if self.mode is None:
            self._decide()
            if self.mode is None:
                return
        if self.mode == "prose":
            self._pump_prose()
        else:
            self._pump_tool()

    def _decide(self):
        s = self.raw.lstrip()
        if not s:
            return
        if s[0] == "{":
            self.mode = "tool"; return
        if s.startswith("```"):
            if "\n" not in s:
                return
            lang = s.split("\n", 1)[0][3:].strip().lower()
            self.mode = "tool" if lang in ("", "json") else "prose"
            return
        self.mode = "prose"

    # ---- prose ------------------------------------------------------------ #
    def _pump_prose(self):
        while True:
            nl = self.raw.find("\n", self.prose_at)
            if nl == -1:
                break
            line = self.raw[self.prose_at:nl]
            s = line.strip()
            if s.startswith("```json") or s.startswith('{"name"') or s == "```":
                self.mode = "tool"; self._pump_tool(); return
            self._emit_prose(line)
            self.prose_at = nl + 1
        rest = self.raw[self.prose_at:].lstrip()
        if rest.startswith("```json") or rest.startswith('{"name"'):
            self.mode = "tool"; self._pump_tool()

    def _emit_prose(self, line):
        self._kill_spinner()
        print(render_md_line(line, self.md))
        self.printed = True

    # ---- tool ------------------------------------------------------------- #
    def _pump_tool(self):
        if self.tool_name is None:
            m = re.search(r'"name"\s*:\s*"([a-zA-Z_]+)"', self.raw)
            if m:
                self.tool_name = m.group(1)
                self.plan = _STREAM_PLANS.get(self.tool_name)
        # keep a live indicator running while the tool call is being generated
        if not self.displayed:
            self._spin(f"preparing {self.tool_name}" if self.tool_name else "working")
        if self.plan is not None:
            if not self.header_shown:
                pm = re.search(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', self.raw)
                if not pm:
                    return
                try:
                    path = json.loads('"' + pm.group(1) + '"')
                except Exception:
                    path = pm.group(1)
                self._kill_spinner()
                print(green("  ▸ ") + bold(blue(self.tool_name)) + (" " + orange(path) if path else ""))
                print(purple("    writing:" if self.tool_name == "write_file" else "    changes:"))
                self.header_shown = True
                self.displayed = True
            self._stream_plan()

    def _stream_plan(self):
        # Incremental: each character of the payload is decoded exactly once (the old
        # version re-decoded the whole accumulated value every token → O(n²), which is
        # what made large writes crawl to a stall).
        while self.plan_i < len(self.plan):
            key, color, prefix = self.plan[self.plan_i]
            if self.val_start is None:
                vm = re.search(r'"' + key + r'"\s*:\s*"', self.raw[self.search_from:])
                if not vm:
                    return  # this value hasn't started streaming yet
                self.val_start = self.search_from + vm.end()
                self.scan = self.val_start
                self.esc = False
            if not self._consume_value(color, prefix):
                return  # value not finished; wait for more tokens
            if self.line_buf:
                self._emit_diff(self.line_buf, color, prefix); self.line_buf = ""
            # advance to the next value in the plan; resume key search after this value
            self.plan_i += 1
            self.val_start = None
            self.search_from = self.scan

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                '"': '"', "\\": "\\", "/": "/"}

    def _consume_value(self, color, prefix):
        """Decode raw[self.scan:] as a JSON string body, emitting completed lines, until
        the closing quote or the end of what has arrived. Returns True at the closing
        quote. Escape state (self.esc) and position (self.scan) persist across tokens so
        total work is linear in the payload size."""
        raw, n = self.raw, len(self.raw)
        i, esc, out = self.scan, self.esc, []
        while i < n:
            ch = raw[i]
            if esc:
                if ch == "u":
                    if i + 4 >= n:
                        break            # need 4 hex digits; wait for more input
                    try:
                        out.append(chr(int(raw[i + 1:i + 5], 16)))
                    except ValueError:
                        out.append(raw[i + 1:i + 5])
                    i += 5; esc = False; continue
                out.append(self._ESCAPES.get(ch, ch))
                i += 1; esc = False; continue
            if ch == "\\":
                esc = True; i += 1; continue
            if ch == '"':
                self.scan = i + 1; self.esc = False
                self._push(out, color, prefix)
                return True
            out.append(ch); i += 1
        self.scan = i; self.esc = esc
        self._push(out, color, prefix)
        return False

    def _push(self, chars, color, prefix):
        if not chars:
            return
        self.line_buf += "".join(chars)
        while "\n" in self.line_buf:
            line, self.line_buf = self.line_buf.split("\n", 1)
            self._emit_diff(line, color, prefix)

    def _emit_diff(self, line, color, prefix):
        if prefix.startswith("+"):
            self.added += 1
        else:
            self.removed += 1
        if self.capped:
            return
        if self.added + self.removed > CONSOLE_MAX_LINES:
            print(dim("    … (still going; live view capped)")); self.capped = True; return
        print("    " + color(prefix + line))

    # ---- finish ----------------------------------------------------------- #
    def finish(self):
        if self.mode == "prose":
            tail = self.raw[self.prose_at:]
            if tail.strip():
                self._emit_prose(tail)
        elif self.mode == "tool":
            self._pump_tool()
            if self.displayed:
                if self.line_buf:
                    self._emit_diff(self.line_buf, *(self.plan[min(self.plan_i, len(self.plan) - 1)][1:]))
                    self.line_buf = ""
                print(green(f"    +{self.added}") + grey(" / ") + red(f"-{self.removed}"))
        self._kill_spinner()


def _echo_block(text, label, color=grey):
    """Echo a (possibly multi-line) argument/body under a labeled left gutter, capped."""
    lines = text.split("\n")
    print(purple(f"    {label}:"))
    for ln in lines[:CONSOLE_MAX_LINES]:
        print(dim("    │ ") + color(ln))
    if len(lines) > CONSOLE_MAX_LINES:
        print(dim(f"    │ … (+{len(lines) - CONSOLE_MAX_LINES} more lines)"))


def show_diff(old, new):
    """Print a colored unified diff — green + additions, red - removals — like Claude Code."""
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3))
    if not diff:
        print(dim("    (no changes)")); return
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    print(green(f"    +{added}") + grey(" / ") + red(f"-{removed}"))
    printed = 0
    for line in diff:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if printed >= CONSOLE_MAX_LINES:
            print(dim("    … (diff truncated)")); break
        if line.startswith("@@"):
            print("    " + cyan(line))
        elif line.startswith("+"):
            print("    " + green(line))
        elif line.startswith("-"):
            print("    " + red(line))
        else:
            print("    " + dim(line))
        printed += 1


def show_tool_call(action):
    """Print the action AND its full payload (a colored diff for writes/edits), so
    nothing is hidden."""
    n, a = action["name"], action["arguments"]
    print(green("  ▸ ") + bold(blue(n)) + " " + orange(summarize_action(action)[len(n) + 1:]))
    if n == "write_file":
        path = _resolve(a.get("path", ""))
        old = ""
        if os.path.exists(path):
            try:
                old = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                old = ""
        print(purple("    " + ("diff:" if old else "new file:")))
        show_diff(old, a.get("content", ""))
    elif n == "edit_file":
        print(purple("    diff:"))
        show_diff(a.get("old", ""), a.get("new", ""))
    # read_file/list_dir/glob/grep/run_shell are fully described by the header line


def show_tool_result(result):
    head = green("    ✓ ") if not result.startswith("ERROR") else red("    ✗ ")
    lines = result.split("\n")
    print(head + grey(lines[0]))
    for ln in lines[1:CONSOLE_MAX_LINES]:
        print(grey("      " + ln))
    if len(lines) > CONSOLE_MAX_LINES:
        print(dim(f"      … (+{len(lines) - CONSOLE_MAX_LINES} more lines shown to the model)"))


# --------------------------------------------------------------------------- #
#  Auto-compaction  (summarize old turns near the context limit, like Claude Code)
# --------------------------------------------------------------------------- #
# Compact late (85%), not at 75%. With a small NUM_CTX, 75% fires very early and often;
# every compaction summarizes away specific tool results, which is a leading cause of the
# model REDOING work it already did. The 4096 floor keeps it from compacting on tiny
# contexts. The summary is instructed (below) to preserve "actions already completed".
COMPACT_AT = max(4096, int(NUM_CTX * 0.85))
KEEP_RECENT = 8                     # keep a bit more recent history verbatim, so the
                                    # model can see what it JUST did and not repeat it
CHECKPOINT_FILE = "redcoder.md"     # written in the CWD by /save so a session can resume


def est_tokens(messages):
    # No tokenizer in the stdlib; ~4 chars/token is close enough to decide.
    return sum(len(m.get("content", "")) for m in messages) // 4


def write_checkpoint(model, summary, recent):
    """Persist a resume point to ./redcoder.md. This is the ONE place redcoder writes
    conversation content to disk — on purpose, so a session that can't continue can be
    picked up later with /resume. Delete the file to remove the record."""
    path = os.path.join(os.getcwd(), CHECKPOINT_FILE)
    lines = [
        "# Redcoder — session checkpoint",
        "",
        "_Saved with `/save` so you can resume where you left off. In a new session in this "
        "folder, run `/resume` to load it back into context._",
        "",
        f"- Updated: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- Model: {model}",
        f"- Working dir: {os.getcwd()}",
        "",
        "## Where it left off",
        "",
        (summary or "").strip(),
        "",
        "## Most recent turns",
        "",
    ]
    for m in recent:
        content = m.get("content", "")
        if len(content) > 2000:
            content = content[:2000] + " …"
        lines.append(f"**{m.get('role', '?')}:** {content}")
        lines.append("")
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        return path
    except OSError:
        return None


_SUMMARIZE_SYS = (
    "Summarize the conversation below into concise but complete notes that let the "
    "assistant continue seamlessly. You MUST include a section titled 'ACTIONS ALREADY "
    "COMPLETED' listing every command run, file read, and file written, WITH their key "
    "results — so the assistant does not repeat work it has already done. Also capture: "
    "goals, decisions, key facts and values, and any open threads. Preserve specifics "
    "(paths, names, numbers). Output only the notes.")


def summarize_messages(model, msgs, label):
    """Summarize a list of messages into notes. Returns the summary or None."""
    convo = "\n\n".join(f"{m['role'].upper()}: {m.get('content', '')}" for m in msgs)
    spin = Spinner(label, color=orange).start()
    try:
        summary, _ = ollama_chat(model, [
            {"role": "system", "content": _SUMMARIZE_SYS},
            {"role": "user", "content": convo[:60000]},
        ])
    except Exception:
        spin.stop()
        return None
    spin.stop()
    return summary


def maybe_compact(model, messages):
    """If the conversation is nearing the context window, replace the older messages
    with a concise summary so the session can continue indefinitely. Mutates `messages`
    in place. Does NOT touch disk — use /save for that. Returns True if it compacted."""
    if est_tokens(messages) < COMPACT_AT or len(messages) <= KEEP_RECENT + 2:
        return False
    system = messages[0]
    head = messages[1:-KEEP_RECENT]
    recent = messages[-KEEP_RECENT:]
    if not head:
        return False
    summary = summarize_messages(model, head, "compacting context")
    if not summary:
        return False
    messages[:] = [system,
                   {"role": "system",
                    "content": "Summary of earlier conversation (auto-compacted to save context):\n" + summary},
                   *recent]
    print(dim(f"  · auto-compacted earlier turns to free context (~{est_tokens(messages)} tok now)"))
    return True


def save_checkpoint(model, messages):
    """Manual /save: summarize the whole conversation and write ./redcoder.md."""
    convo = [m for m in messages if m.get("role") != "system"]
    if not convo:
        return None
    summary = summarize_messages(model, convo, "saving checkpoint") or ""
    return write_checkpoint(model, summary, messages[-KEEP_RECENT:])


# --------------------------------------------------------------------------- #
#  Agent turn
# --------------------------------------------------------------------------- #
def _rejected_tool_name(content):
    """If `content` is really a JSON tool-call whose name is NOT a real tool, return that
    name; else None. Catches small models that 'call' a fake tool like stop/done/finish
    instead of replying in plain text — _coerce_action rejects those, so they arrive here."""
    for blob in _iter_json_objects(content):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str) \
                and obj["name"] not in TOOL_NAMES:
            return obj["name"]
    return None


def _fingerprint(action):
    """Stable hash of a tool call (name + arguments) for loop detection."""
    try:
        return json.dumps({"n": action.get("name"), "a": action.get("arguments", {})},
                          sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(action)


def agent_turn(model, messages, approve):
    """Run tool-use iterations until the model gives a final text answer.

    Ctrl-C at any point stops cleanly and returns to the prompt with the full
    conversation kept, so you can interject new information and continue."""
    call_counts = {}     # tool-call fingerprint -> how many times seen this turn
    forced_final = 0     # how many times we've told it to stop and answer
    bad_tool_tries = 0   # how many times it named a nonexistent tool this turn
    for _ in range(MAX_STEPS):
        maybe_compact(model, messages)
        printer = StreamPrinter()
        try:
            content, native = ollama_chat(model, messages, on_token=printer.feed)
        except KeyboardInterrupt:
            printer.finish()
            print(yellow("  ⎋ interrupted — context kept; type your next message."))
            return
        printer.finish()

        messages.append({"role": "assistant", "content": content})
        action = extract_action(content, native)

        if not action:
            # The model may have "called" a fake tool (e.g. {"name":"stop"}) instead of
            # replying in plain text. _coerce_action rejected it, so it's here as content.
            fake = _rejected_tool_name(content)
            if fake is not None:
                if fake.lower().strip() in STOP_TOOL_WORDS:
                    # It meant "I'm done" — end cleanly, don't print the raw json.
                    print(dim("  (done)"))
                    return
                # An unknown, non-stop tool name. Correct it a couple of times, then stop.
                bad_tool_tries += 1
                if bad_tool_tries <= 2:
                    print(yellow(f"  ⚠ no tool named '{fake}' — asking for a real answer."))
                    messages.append({"role": "user", "content":
                        f"There is no tool named '{fake}'. The only tools are: "
                        f"{', '.join(sorted(TOOL_NAMES))}. If you are finished, reply in "
                        "plain text with NO json. Otherwise call one of the real tools."})
                    continue
                print(dim("  (done)"))
                return
            # A genuine plain-text answer.
            if (printer.mode == "tool" and not printer.displayed) or not printer.printed:
                print(render_markdown(content.strip()))
            return

        if action.pop("_truncated", False):
            # A tool call was recognized but its JSON was cut off mid-value. Don't run a
            # half-written file; tell the model so it resends the COMPLETE call and the
            # loop continues — instead of silently dropping the write.
            if printer.displayed or printer.printed:
                print()
            print(yellow("  ⚠ the tool call was cut off before it finished — "
                         "asking the model to resend it."))
            messages.append({"role": "user", "content":
                "OBSERVATION: your previous tool call was cut off before it finished (the "
                "JSON was incomplete), so nothing was executed. Resend the COMPLETE tool "
                "call as a single json block. Do not add prose around it."})
            continue

        if (printer.mode == "prose" and printer.printed) or printer.displayed:
            print()  # separate streamed reasoning / live write from the result

        # --- Loop control: has the model already made this exact call this turn? ------
        fp = _fingerprint(action)
        seen = call_counts[fp] = call_counts.get(fp, 0) + 1
        if seen >= REPEAT_STOP_AT:
            forced_final += 1
            print(red(f"  ! repeated the same action {seen}× — stopping and forcing an answer."))
            if forced_final > 2:
                # It won't stop even when told. Bail out cleanly rather than loop forever.
                print(red("  ! model kept repeating; ending the turn."))
                return
            messages.append({"role": "user", "content":
                "STOP. You have called the same tool with the same arguments "
                f"{seen} times. The result is already in the conversation above. Do NOT "
                "call any tool again. Reply now with your final answer in plain text, "
                "using the information you already have."})
            continue
        if seen == REPEAT_NUDGE_AT:
            # Don't re-run it — feed back a nudge so it uses the prior result or answers.
            print(yellow("  ⚠ already ran this exact call — nudging instead of repeating."))
            messages.append({"role": "user", "content":
                f"OBSERVATION: you already ran this exact call ({action['name']} with the "
                "same arguments) and its result is above. Do not repeat it. Either take a "
                "DIFFERENT action that makes progress, or give your final answer."})
            continue
        # -----------------------------------------------------------------------------

        # Show the model's step-by-step reasoning before the action.
        thought = action.pop("thought", None)
        if thought:
            print(cyan("  » ") + dim(thought))

        # If the streamer already showed the tool live (write_file), don't repeat it.
        if not printer.displayed:
            show_tool_call(action)
        try:
            result = run_tool(action, approve)
        except KeyboardInterrupt:
            print(yellow("  ⎋ interrupted — context kept; type your next message."))
            return
        except ToolError as e:
            result = f"ERROR: {e}"
        except KeyError as e:
            result = f"ERROR: missing argument {e}"
        except Exception as e:
            result = f"ERROR: {type(e).__name__}: {e}"

        show_tool_result(result)
        if len(result) > MAX_TOOL_OUTPUT:
            result = result[:MAX_TOOL_OUTPUT] + "\n…(truncated)"
        messages.append({"role": "user", "content": f"OBSERVATION ({action['name']}):\n{result}"})

    print(red("  ! Reached the step limit for this turn."))


# --------------------------------------------------------------------------- #
#  REPL
# --------------------------------------------------------------------------- #
BANNER = r"""
   ____   _____  ____    ____   ___   ____   _____  ____
  |  _ \ | ____||  _ \  / ___| / _ \ |  _ \ | ____||  _ \
  | |_) ||  _|  | | | || |    | | | || | | ||  _|  | |_) |
  |  _ < | |___ | |_| || |___ | |_| || |_| || |___ |  _ <
  |_| \_\|_____||____/  \____| \___/ |____/ |_____||_| \_\
"""

HELP = """\
Commands (type inside the session):
  /help              show this help
  /clear, /reset     clear the conversation (nothing is stored anywhere)
  /save              write a ./redcoder.md checkpoint of this session (manual)
  /resume            load a ./redcoder.md checkpoint back into context
  /auto              toggle auto-approve for writes/edits/shell
  /net [MODE]        network mode: /net (status), /net sealed (airgap),
                     /net lab (offline lab net), /net online (allow internet)
  /model [NAME]      pick a model from a menu (or /model NAME to switch directly)
  /cwd [PATH]        show or change the working directory
  /exit, /quit       leave (Ctrl-C also works)

Just type a request to have Redcoder work in the current directory.
It reads/writes/edits files and runs commands here — nothing is logged to disk.
Launch flags: run `redcoder --help` (includes --dangerously-skip-permissions, -p, ...).
"""


def preflight(model):
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=4) as r:
            names = [m.get("name", "") for m in json.load(r).get("models", [])]
        if any(n.split(":")[0] == model or n == model for n in names):
            return True, f"model '{model}' ready"
        return False, (f"Ollama is up but model '{model}' is not installed. Build it with:\n"
                       f"      ollama pull huihui_ai/qwen2.5-coder-abliterate:14b\n"
                       f"      ollama create {model} -f config/Modelfile.redcoder-drago\n"
                       f"    (or run ./install-kali.sh, which does both)")
    except Exception:
        return False, ("Ollama not reachable on 127.0.0.1:11434. Start it with `ollama serve`\n"
                       "    (or `systemctl start ollama`), then try again.")


def list_models():
    """Return [(name, size_gb), ...] of installed Ollama models, largest first."""
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=4) as r:
            models = json.load(r).get("models", [])
    except Exception:
        return []
    out = [(m.get("name", ""), (m.get("size", 0) or 0) / 1e9) for m in models if m.get("name")]
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def pick_model(current):
    """Interactive model picker (like Claude Code's /model). Returns a name or None."""
    models = list_models()
    if not models:
        print(yellow("  no models found (is Ollama running?)"))
        return None
    print(bold("  Available models:"))
    for i, (name, gb) in enumerate(models, 1):
        mark = green(" ← current") if (name == current or name.split(":")[0] == current) else ""
        print(f"    {cyan(str(i))}. {name}  {grey(f'({gb:.1f} GB)')}{mark}")
    try:
        ans = input(bold("  switch to # (or name, Enter to cancel): ")).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not ans:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(models):
        return models[int(ans) - 1][0]
    # allow typing a name / prefix directly
    for name, _ in models:
        if name == ans or name.split(":")[0] == ans:
            return name
    return ans  # accept as-is (preflight will validate)


# --------------------------------------------------------------------------- #
#  Voice input — hold Space to talk (offline: ffmpeg mic capture + local Whisper)
# --------------------------------------------------------------------------- #
VK_SPACE = 0x20
WHISPER_MODEL = "base.en"
_MIC = None
_WHISPER = None
_VOICE = False   # resolved once at startup


def _key_down(vk):
    if os.name != "nt":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _mic_device():
    global _MIC
    if _MIC is not None:
        return _MIC
    try:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true",
                              "-f", "dshow", "-i", "dummy"],
                             capture_output=True, text=True, timeout=10).stderr
        hits = re.findall(r'"([^"]+)"\s*\(audio\)', err)
        _MIC = hits[0] if hits else ""
    except Exception:
        _MIC = ""
    return _MIC


def voice_available():
    return (os.name == "nt" and msvcrt is not None and _C
            and importlib.util.find_spec("whisper") is not None
            and importlib.util.find_spec("torch") is not None
            and shutil.which("ffmpeg") is not None
            and bool(_mic_device()))


def _record_ptt(is_held, wav):
    """Record the mic to `wav` while is_held() is true. Returns True if audio captured."""
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "dshow",
         "-i", f"audio={_mic_device()}", "-ac", "1", "-ar", "16000", "-y", wav],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while is_held() and time.time() - t0 < 60:   # 60s safety cap
        time.sleep(0.03)
    try:
        proc.communicate(input=b"q", timeout=5)   # 'q' tells ffmpeg to finalize the file
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    return os.path.exists(wav) and os.path.getsize(wav) > 1200


def _transcribe(wav):
    global _WHISPER
    import whisper
    import torch
    if _WHISPER is None:
        sp = Spinner("loading speech model", color=pink).start()
        _WHISPER = whisper.load_model(WHISPER_MODEL)
        sp.stop()
    res = _WHISPER.transcribe(wav, fp16=torch.cuda.is_available())
    return (res.get("text") or "").strip()


def _do_voice():
    """Record while Space is held, transcribe, return the text (audio deleted after)."""
    print(pink("  🎤 recording… ") + dim("(release Space to send)"))
    wav = os.path.join(tempfile.gettempdir(), "redcoder_voice.wav")
    ok = _record_ptt(lambda: _key_down(VK_SPACE), wav)
    if not ok:
        print(dim("  (no audio captured)"))
        return ""
    try:
        text = _transcribe(wav)
    except Exception as e:
        print(red(f"  transcription failed: {e}"))
        text = ""
    finally:
        try:
            os.remove(wav)   # no-logging: never leave the recording on disk
        except OSError:
            pass
    return text


def read_input():
    """Raw-mode line reader with hold-Space-to-talk. The prompt is already printed.
    Space held at an EMPTY line starts voice; otherwise Space types normally."""
    buf = ""
    while True:
        if not buf and _key_down(VK_SPACE):        # possible push-to-talk
            time.sleep(0.12)                        # confirm it's a hold, not a tap
            while msvcrt.kbhit():                    # drain the queued space char(s)
                if msvcrt.getwch() not in (" ",):
                    pass
            if _key_down(VK_SPACE):
                text = _do_voice()
                if text.strip():
                    print(green("  ▸ heard: ") + text)
                    return text
                sys.stdout.write(grey("│ ") + green("› ") + buf); sys.stdout.flush()
            continue
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                sys.stdout.write("\n"); return buf
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x00", "\xe0"):              # arrow/function key → consume 2nd byte
                msvcrt.getwch(); continue
            if ch == "\x08":                          # backspace
                if buf:
                    buf = buf[:-1]; sys.stdout.write("\b \b"); sys.stdout.flush()
                continue
            if ch == " " and not buf:                 # leading space is meaningless; reserve for voice
                continue
            if ch >= " ":
                buf += ch; sys.stdout.write(ch); sys.stdout.flush()
            continue
        time.sleep(0.008)


def _term_width(default=80):
    try:
        return max(20, min(os.get_terminal_size().columns, 100))
    except Exception:
        return default


def input_bar(model, prefill=None):
    """Draw a Claude-Code-style framed input bar that adapts to the terminal width
    (drops the model tag, then shortens, as the window gets narrower)."""
    if not _C:
        if prefill is not None:
            return prefill
        return input("redcoder> ")
    w = _term_width() - 1   # leave the last column empty so the ╮ never wraps a line
    tag = f"[{model}]"
    if len(f"╭─ redcoder · {tag} ") + 1 <= w:
        header = grey("╭─ ") + bold(red("redcoder")) + grey(" · ") + blue(tag) + " "
        hlen = len(f"╭─ redcoder · {tag} ")
    elif len("╭─ redcoder ") + 1 <= w:
        header = grey("╭─ ") + bold(red("redcoder")) + " "
        hlen = len("╭─ redcoder ")
    else:
        header = grey("╭ ")
        hlen = 2
    print(header + grey("─" * max(0, w - hlen - 1) + "╮"))
    if prefill is not None:
        print(grey("│ ") + green("› ") + prefill)
        return prefill
    if _VOICE:
        sys.stdout.write(grey("│ ") + green("› ")); sys.stdout.flush()
        return read_input()
    return input(grey("│ ") + green("› "))


def main(argv):
    global _C, _VOICE, _NET_MODE
    auto = False
    print_mode = False
    no_voice = False
    net_mode = NET_MODE_DEFAULT
    model = DEFAULT_MODEL
    start_cwd = None
    start_parts = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-y", "--auto", "--dangerously-skip-permissions", "--yolo"):
            auto = True
        elif a == "--no-voice":
            no_voice = True
        elif a in ("--sealed", "--offline", "--airgap"):
            net_mode = "sealed"
        elif a in ("--lab", "--offline-lab"):
            net_mode = "lab"
        elif a == "--online":
            net_mode = "online"
        elif a in ("-p", "--print"):
            print_mode = True
        elif a in ("-m", "--model") and i + 1 < len(argv):
            model = argv[i + 1]; i += 1
        elif a in ("-C", "--cwd") and i + 1 < len(argv):
            start_cwd = argv[i + 1]; i += 1
        elif a == "--no-color":
            _C = False
        elif a in ("-v", "--version"):
            print(f"redcoder {VERSION}")
            return 0
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif a.startswith("-") and a != "-":
            print(f"redcoder: unknown option '{a}'  (try --help)")
            return 2
        else:
            start_parts.append(a)
        i += 1

    _NET_MODE = net_mode

    if start_cwd:
        try:
            os.chdir(os.path.expanduser(start_cwd))
        except OSError as e:
            print(red(f"redcoder: --cwd: {e}"))
            return 2

    prompt = " ".join(start_parts).strip()

    # -------- non-interactive (-p/--print): run one turn and exit -------------
    if print_mode:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read().strip()
        if prompt and stdin_text:
            prompt = f"{prompt}\n\n{stdin_text}"   # instruction + piped content
        elif stdin_text:
            prompt = stdin_text
        if not prompt:
            print("redcoder: --print needs a prompt (as an argument or on stdin).")
            return 2
        if _NET_MODE == "lab":
            lok, lmsg = activate_lab()
            if not lok:
                print(red(f"lab mode unavailable: {lmsg}\nfalling back to SEALED (airgap)."))
                _NET_MODE = "sealed"
        ok, status = preflight(model)
        if not ok:
            print(status)
            return 1
        messages = [{"role": "system", "content": build_system()},
                    {"role": "user", "content": prompt}]
        try:
            agent_turn(model, messages, Approver(auto))
        except RuntimeError as e:
            print(red(f"  ! {e}"))
            return 1
        return 0

    # -------- interactive REPL ----------------------------------------------
    w = _term_width()
    if w >= 58:
        print(red(BANNER))
    else:
        print("\n" + bold(red("  R E D C O D E R")) + "\n")
    ok, status = preflight(model)
    print(("  " + (green(status) if ok else yellow(status))) + "\n")
    _VOICE = (not no_voice) and voice_available()
    print(grey("  cwd: ") + blue(os.getcwd()))
    if _NET_MODE == "lab":
        ok, msg = activate_lab()
        if not ok:
            print(red("  lab mode unavailable: " + msg))
            print(red("  falling back to Airgapped."))
            _NET_MODE = "sealed"
    if _NET_MODE == "sealed":
        print(green("  Airgapped"))
        if IS_WINDOWS:
            print(yellow("  ⚠ isolation is Linux-only; not enforced on Windows"))
        else:
            _, _, err = _net_prefix("sealed")
            if err:
                print(red("  ⚠ no firejail/unshare found — shell commands will REFUSE "
                          "to run until one is installed (sudo apt install -y firejail)"))
    elif _NET_MODE == "lab":
        print(green("  Lab"))
    else:
        print(yellow("  Online"))
    if _VOICE:
        print(pink("  🎤 hold ") + bold(pink("Space")) + pink(" at an empty prompt to talk")
              + dim("  (offline Whisper)"))
    if os.path.exists(os.path.join(os.getcwd(), CHECKPOINT_FILE)):
        print(orange(f"  ⤶ {CHECKPOINT_FILE} — /resume" if w < 74
                     else f"  ⤶ found {CHECKPOINT_FILE} here — type /resume to pick up where a prior session left off"))
    print(grey("  /help · Ctrl-C = interrupt" if w < 74
               else "  /help for commands · Ctrl-C interrupts a running task to interject (quits at an empty prompt)"))
    if auto:
        print(red("  ⚠ auto-approve ON — no prompts" if w < 62
                  else "  ⚠ auto-approve ON — writes, edits, and shell run WITHOUT asking"))
    print()

    approver = Approver(auto)
    messages = [{"role": "system", "content": build_system()}]
    pending = prompt

    while True:
        try:
            if pending:
                user = input_bar(model, prefill=pending).strip()
                pending = ""
            else:
                user = input_bar(model).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + dim("  bye."))
            return 0

        if not user:
            continue

        if user.startswith("/"):
            cmd, _, rest = user[1:].partition(" ")
            cmd = cmd.lower()
            if cmd in ("exit", "quit", "q"):
                print(dim("  bye.")); return 0
            if cmd == "help":
                print(HELP); continue
            if cmd in ("reset", "clear"):
                messages = [{"role": "system", "content": build_system()}]
                print(dim("  conversation cleared.")); continue
            if cmd == "save":
                if len(messages) <= 1:
                    print(yellow("  nothing to save yet.")); continue
                path = save_checkpoint(model, messages)
                if path:
                    print(green(f"  saved checkpoint → {path}"))
                else:
                    print(red("  could not write the checkpoint."))
                continue
            if cmd == "resume":
                path = os.path.join(os.getcwd(), CHECKPOINT_FILE)
                if not os.path.exists(path):
                    print(yellow(f"  no {CHECKPOINT_FILE} checkpoint in this directory.")); continue
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError as e:
                    print(red(f"  {e}")); continue
                messages.append({"role": "system",
                                 "content": "Resuming a prior session from its saved checkpoint. "
                                            "Use this as context for what was already done:\n\n" + text})
                print(green(f"  resumed from {path}  ({len(text)} chars loaded into context).")); continue
            if cmd == "auto":
                approver.auto = not approver.auto
                print(dim(f"  auto-approve {'ON' if approver.auto else 'OFF'}.")); continue
            if cmd == "net":
                arg = rest.strip().lower()
                if arg in ("", "status"):
                    if _NET_MODE == "sealed":
                        print(green("  Airgapped"))
                    elif _NET_MODE == "lab":
                        print(green("  Lab"))
                    else:
                        print(yellow("  Online"))
                    print(grey("  /net sealed · /net lab · /net online"))
                elif arg in ("sealed", "offline", "off", "airgap"):
                    if _NET_MODE == "sealed":
                        print(dim("  already Airgapped.")); continue
                    _NET_MODE = "sealed"
                    messages.append({"role": "system", "content":
                        "NETWORK MODE CHANGED to SEALED (airgap): shell commands now have NO "
                        "network at all. Do local work only."})
                    print(green("  Airgapped"))
                elif arg in ("lab", "offline-lab"):
                    if _NET_MODE == "lab":
                        print(dim("  already Lab.")); continue
                    ok, msg = activate_lab()
                    if not ok:
                        print(red("  " + msg)); continue
                    _NET_MODE = "lab"
                    messages.append({"role": "system", "content":
                        f"NETWORK MODE CHANGED to LAB: shell commands reach the isolated "
                        f"offline lab network ({LAB_SUBNET}) only — no internet. Aim tools at "
                        f"{LAB_SUBNET}, not public hosts."})
                    print(green("  Lab"))
                elif arg in ("online", "on"):
                    if _NET_MODE == "online":
                        print(dim("  already Online.")); continue
                    _NET_MODE = "online"
                    messages.append({"role": "system", "content":
                        "NETWORK MODE CHANGED to ONLINE: shell commands can now reach the "
                        "network. Still prefer local work; go online only when required."})
                    print(yellow("  Online"))
                else:
                    print(yellow("  usage: /net [status|sealed|lab|online]"))
                continue
            if cmd in ("model", "models"):
                choice = rest.strip() or pick_model(model)
                if choice:
                    model = choice
                    ok, status = preflight(model)
                    print("  " + (green(status) if ok else yellow(status)))
                else:
                    print(grey(f"  keeping current model: {model}"))
                continue
            if cmd == "cwd":
                if rest.strip():
                    try:
                        os.chdir(os.path.expanduser(rest.strip()))
                    except OSError as e:
                        print(red(f"  {e}")); continue
                print(grey(f"  cwd: {os.getcwd()}")); continue
            print(yellow(f"  unknown command: /{cmd}  (try /help)")); continue

        messages.append({"role": "user", "content": user})
        try:
            agent_turn(model, messages, approver)
        except RuntimeError as e:
            print(red(f"  ! {e}"))
        except KeyboardInterrupt:
            print(yellow("\n  (interrupted)"))
        print()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
