"""Impartial graders for the redcoder eval loop.

Anthropic's guidance (Demystifying evals for AI agents / Create strong empirical evaluations):
- Prefer CODE-BASED graders — fast, objective, reproducible — for anything with a clear-cut
  answer. That's what lives here: deterministic checks over the agent's final answer, the
  resulting workspace files, and which tools it used.
- Grade the OUTCOME, not the exact path. We never assert a specific tool-call sequence (too
  brittle); we check what the agent PRODUCED, with partial credit for multi-part tasks.
- The LLM-as-judge, when needed for subjective dimensions, MUST be a different model family
  than the one under test (leviathan is Qwen), so it is NOT a local Qwen model — it is the
  cross-family reviewer (Opus) reading the saved transcript. See README.
"""
import re

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(s):
    return _ANSI.sub("", s or "")


# --- accessors over a trial result dict -------------------------------------------------
# result = {"text": <ansi-stripped stdout>, "files": {relpath: content},
#           "tools_used": [names], "exit": int, "seconds": float}

def _t(r):
    return (r.get("text") or "").lower()


def has(r, *subs):
    """True if ALL substrings appear in the transcript/answer (case-insensitive)."""
    t = _t(r)
    return all(s.lower() in t for s in subs)


def has_any(r, *subs):
    t = _t(r)
    return any(s.lower() in t for s in subs)


def matches(r, pattern):
    return re.search(pattern, r.get("text") or "", re.IGNORECASE | re.DOTALL) is not None


def file_get(r, name):
    return (r.get("files") or {}).get(name)


def file_exists(r, name):
    return name in (r.get("files") or {})


def file_has(r, name, sub):
    c = file_get(r, name)
    return c is not None and sub.lower() in c.lower()


def file_eq(r, name, value):
    c = file_get(r, name)
    return c is not None and c.strip() == value


def used_tool(r, *names):
    tu = r.get("tools_used") or []
    return any(n in tu for n in names)


def used_any_tool(r):
    return len(r.get("tools_used") or []) > 0
