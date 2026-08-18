# Redcoder

An offline, local AI coder for your own machine and your own isolated lab. It runs a
refusal-removed (abliterated) model through Ollama — nothing leaves this PC, and
**nothing about your chats is written to disk**. Two front ends share the same local
model:

| Front end | What it is | Launch |
|---|---|---|
| **CLI harness** | A Claude-Code-style terminal agent that reads/writes/edits files and runs commands on this PC. | type `redcoder` in any terminal |
| **Web chat** | A clean browser chat UI (no tools, just conversation). | run `Start Redcoder Web.cmd` |

---

## 1. The CLI harness (`redcoder`)

A self-contained agent (`redcoder.py`, Python standard library only — no pip installs)
that drives the local `redcoder` model in an agentic loop, the way Claude Code does:
it takes a request, uses tools to act on your files, feeds the results back to itself,
and iterates until the task is done.

```powershell
redcoder                                  # start in the current directory
redcoder "refactor utils.py"              # start with a task
redcoder --dangerously-skip-permissions   # never ask before writes/edits/shell
redcoder -p "explain scan.py"             # one-shot: print the answer and exit
git diff | redcoder -p "review this diff" # pipe content in via stdin
redcoder -m qwen3.5:9b                     # use a different Ollama model
```

**Flags (Claude-Code style):**

| Flag | Meaning |
|---|---|
| `-p`, `--print` | Non-interactive: run the prompt (from args or stdin), print, exit. |
| `-y`, `--auto`, `--dangerously-skip-permissions`, `--yolo` | Auto-approve **all** writes/edits/shell — no prompts. |
| `-m`, `--model NAME` | Use a different Ollama model (default `redcoder`). |
| `-C`, `--cwd DIR` | Start in `DIR` instead of the current directory. |
| `--no-color` | Plain output, no ANSI colors. |
| `-v`, `--version` · `-h`, `--help` | Version / help. |

**Tools it can use:** `read_file`, `write_file`, `edit_file`, `list_dir`, `glob`,
`grep`, `run_shell` (PowerShell). It operates in whatever directory you launched it
from.

**Permissions:** read-only tools run automatically. Writes, edits, and shell commands
ask for approval first (`Y` = yes, `n` = no, `a` = always for this session). Skip the
prompts with `--dangerously-skip-permissions` (or `-y`), or type `/auto` mid-session.

**In-session commands:** `/help  /clear (/reset)  /auto  /model [NAME]  /cwd [PATH]  /exit`.

**Interface (Claude-Code style):** a framed input bar (shows the active model) where you
submit prompts; live line-by-line streaming of the model's text; an animated spinner while
it's thinking; every action shown in full (writes/edits stream live as a green +/red −
diff, plus the exact command and its full output); and color throughout. **Ctrl-C**
interrupts a running task and returns you to the bar with the conversation kept, so you
can interject. The bar and banner adapt to the terminal width.

**Voice (hold Space to talk):** if a mic, ffmpeg, and Whisper are present (they are on this
box — base.en is cached and runs on the RTX 5070), hold **Space at an empty prompt** to
talk; release to transcribe **offline on the GPU** and submit. The recording is deleted
right after transcription — nothing is kept. Disable with `--no-voice`.

**Context & limits:** the harness sends a **32,768-token** context (the model's native max
is 262,144 — raise `NUM_CTX` in `redcoder.py` / `num_ctx` in the Modelfile if you have RAM
headroom). There's a 200-step runaway guard per turn (an infinite-loop safety net, not a
real cap — Ctrl-C also stops it).

### Models & the switcher
Two local models are installed; `redcoder` starts on the best-quality one by default:

| Model | Base | Size | When to use |
|---|---|---|---|
| **redcoder-max** (default) | Qwen3-Coder-**30B-A3B** abliterated (MoE, ~3B active) | 18.6 GB | Best quality — newest-gen, agent-tuned. Splits across GPU+RAM (~53% GPU / 47% RAM on this box); still fast because only ~3B params are active per token. |
| **redcoder** | Qwen2.5-Coder-**14B** abliterated | 9.0 GB | Lighter/faster, fully on-GPU. Good for quick, scoped tasks. |

Type **`/model`** (no argument) for an interactive picker — it lists every installed
Ollama model, marks the current one, and you switch by number (just like Claude Code).
`/model NAME` switches directly; `redcoder -m NAME` launches straight into one. Rebuild
either from its Modelfile: `ollama create redcoder-max -f config/Modelfile.redcoder-max`.

### How `redcoder` is on your PATH
A directory on your user PATH holds `redcoder.cmd`, which points at `redcoder.py`.
That's why typing `redcoder` works from any folder. The command runs in your current
directory, so the agent acts on the project you're standing in.

(On Linux the equivalent is the `redcoder` shell launcher, symlinked into
`/usr/local/bin` by `install-kali.sh`.)

---

## 2. The web chat (`Start Redcoder Web.cmd`)

`server.py` serves the chat UI at **http://127.0.0.1:7331**, bound to loopback only
(not exposed on your network), and streams from the local model. It's conversation
only — it does **not** touch your files. History lives in the browser tab's memory and
is gone the moment you close it; nothing is stored.

---

## 3. Nothing is logged — by design

This is the point of the setup, so it's enforced in several places:

- **CLI harness:** writes *nothing* to disk except the files you ask it to change **and one
  opt-in exception: `redcoder.md`** (see below). No transcript, no history file, no
  telemetry. The whole conversation is in RAM and gone on exit. `/clear` clears it
  mid-session. Prompts you type at the `>` prompt go through Python's `input()` with no
  readline/history module installed, so they are never persisted anywhere.
- **The one on-disk record — `redcoder.md`:** only when you run **`/save`**, redcoder writes
  a `redcoder.md` checkpoint in the working directory (a summary of the session so far + the
  latest turns) so a session that can't continue can be resumed. It's the single place
  conversation content touches disk, and only on your explicit command. In a new session in
  that folder you'll see a hint and can `/resume` to load it; delete the file to remove it.
  (Auto-compaction summarizes in memory only — it never writes to disk.)
- **One caveat — your shell's history:** anything you type *on the command line*
  (`redcoder "some prompt"`) can be recorded by PowerShell's own PSReadLine history —
  that's your shell, not Redcoder. To keep a prompt off disk entirely, type it at the
  `>` prompt instead of passing it as an argument. (Existing `redcoder …` lines were
  scrubbed from PSReadLine during setup.)
- **Web server:** HTTP request logging is suppressed; the UI keeps history in memory
  only (no `localStorage`, no database).
- **Ollama:** the harness talks to it over the HTTP API, which does not persist prompts.
  The interactive REPL history file (`~/.ollama/history`) is not used by the harness and
  has been cleared.
- **Offline:** everything targets `127.0.0.1:11434`. Turn WiFi off and nothing changes.

> Note: if Ollama on your machine is a shared instance other tools depend on, it can be
> left running and bound as-is. The harness only ever connects to `127.0.0.1`, which
> works regardless of that binding.

---

## 4. The model

- **Name:** `redcoder` (in Ollama) — built from the abliterated base
  `huihui_ai/qwen2.5-coder-abliterate:14b` plus a tuned system prompt and VRAM-tuned
  context. See `config/Modelfile.redcoder`.
- **Why 14B:** RTX 5070 = 12 GB VRAM, which makes a 14B model the ceiling. At
  `num_ctx 8192` it runs 100% on the GPU (verify with `ollama ps`). A bigger model would
  need a 24 GB card.
- Answers offensive-security and coding questions directly (no refusals) because the
  refusal direction was abliterated and the system prompt frames it as an authorized,
  isolated-lab assistant. It's a copilot, not an oracle — verify its commands.

Rebuild / retune the model:
```powershell
ollama create redcoder -f config/Modelfile.redcoder
```
For a capability jump on a 24 GB card, change the `FROM` line to a Mistral-Small-24B or
GLM abliterated build and rebuild.

---

## 5. The isolated VirtualBox lab (optional)

For live offensive-security work against your own targets. You supply the guest ISOs:

```powershell
.\scripts\start-model.ps1     # bring the model up, loopback-only, VRAM-tuned
.\scripts\lab-setup.ps1 -AttackerIso "C:\isos\kali.iso" -TargetIso "C:\isos\metasploitable.iso"
.\scripts\verify-airgap.ps1   # must say RESULT: PASS before you attack anything
```

This creates two VM shells (`rc-attacker`, `rc-target`), each with a single NIC on an
isolated internal network (`redcoderlab`) and all other NICs off. An internal network
has no NAT and no bridged adapter, so it's cut off from the host and the internet even
if WiFi is on. `verify-airgap.ps1` fails loudly if it ever finds a NAT/bridged NIC.

**Air-gap checklist (before every live session):** `verify-airgap.ps1` = PASS ·
Tailscale disconnected · Ethernet unplugged / WiFi off · target VM has an
internal-network NIC only.

---

## 6. Files

```
Redcoder/
  redcoder.py                 # the CLI agent harness
  redcoder.cmd                # local CLI launcher (copy it to a directory on your PATH)
  server.py                   # web chat server (loopback :7331)
  Start Redcoder Web.cmd      # launches the web chat
  webui/index.html            # web chat UI
  config/Modelfile.redcoder   # tuned model definition (8192 ctx, system prompt)
  scripts/ start-model.ps1    # start Ollama loopback-only, build+warm redcoder
           ask.ps1            # one-shot query helper
           lab-setup.ps1      # create isolated attacker/target VMs
           verify-airgap.ps1  # audit: no internet path
  docs/README.md              # this file
```
