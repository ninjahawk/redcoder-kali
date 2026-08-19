@echo off
REM Native (Windows) launcher for redcoder — runs the EXACT same redcoder.py as Kali.
REM Kali-only features auto-disable on Windows: the Kali tool guidance (nmap/sqlmap/...)
REM is omitted, and network isolation (airgapped/lab) is Linux-only so it is not enforced
REM here. Everything else — the tool loop, model manager, dragon roster, prompts — is 1:1.
REM
REM Usage:  redcoder.cmd                 (interactive)
REM         redcoder.cmd -m leviathan    (pick a model)
REM         redcoder.cmd --online -p "..."  (one-shot)
python "%~dp0redcoder.py" %*
