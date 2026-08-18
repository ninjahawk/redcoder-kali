# Redcoder — Kali edition

An offline, Claude-Code-style terminal coding agent driven by a local abliterated model
through Ollama. This build is tuned for one specific setup: **a Kali Linux live USB with
persistence, running against a 12 GB NVIDIA GPU.**

Nothing leaves the machine. It talks only to `127.0.0.1:11434`, writes no transcript, no
history file, and no telemetry — only the files you ask it to change.

---

## Three network modes — sealed, lab, online

The agent process is offline by construction: it only ever talks to the local Ollama
server on `127.0.0.1`. But the *shell commands the model runs* (`curl`, `nmap`, `apt`, ...)
can reach the network. So there are three deliberate modes, chosen explicitly and shown in
the header every session:

- **🔒 SEALED (default) — airgap.** Every `run_shell` command runs inside an *empty*
  network namespace: no interface, no route — no internet **and no LAN**. It's not "asked
  not to"; there is no usable network stack in the command. Enforced by `unshare -rn`
  (util-linux, always on Kali), or `firejail --net=none` if firejail happens to be
  installed. Best for QA of the agent itself. **Fails closed:** if neither is present,
  `run_shell` *refuses to run*.
- **🧪 LAB — offline lab network.** Commands run inside a pre-built isolated namespace
  (`rclab`) wired to a bridge with **no physical uplink**. Tools like `nmap` reach the
  fake targets on that bridge (`10.66.0.0/24`), but packets have no wire to the internet.
  **`redcoder --lab` builds the lab itself** if it isn't up (asking for your sudo password
  once per boot, since making namespaces needs root), including a built-in target at
  `10.66.0.20` (HTTP on port 80) so there's something to scan out of the box; it also installs
  a scoped NOPASSWD sudo rule so redcoder can *enter* the namespace via `ip netns exec`
  (firejail is used instead if present, but is not required). **redcoder actively verifies
  the internet is unreachable from the lab and refuses lab mode if it isn't** — the airgap
  is proven every time, not trusted — then runs an nmap sweep of the subnet and shows what's
  reachable as a positive control. You can still run `sudo ./lab-net.sh up` by hand.
- **🌐 ONLINE.** Commands run normally and can reach the internet. You opt in on purpose.

```bash
redcoder                 # sealed (default) — no network for shell commands
redcoder --lab           # offline lab — reaches fake targets, never the internet
redcoder --online        # online — shell commands can reach the internet
```

Inside a session, `/net` shows the current mode and `/net sealed` / `/net lab` /
`/net online` switch it. The model is told which mode it's in, so while sealed/lab it won't
waste steps attempting public downloads or remote scans.

### The offline lab (`--lab`)

`lab-net.sh` builds the isolated network. The safety invariant is simple: **its bridge is
never enslaved to a physical NIC**, so nothing on it can reach the internet, regardless of
routes. The script's `verify` (and redcoder's own pre-flight check) actively opens a TCP
connection to public resolvers from inside the namespace and requires it to *fail*.

```bash
sudo ./lab-net.sh up        # build + verify the lab network (10.66.0.0/24)
sudo ./lab-net.sh verify    # re-prove the internet is unreachable
sudo ./lab-net.sh status    # namespace / bridge / attached targets
sudo ./lab-net.sh down      # tear it down
```

It's runtime kernel state — re-run `up` after each boot (live USB doesn't persist it).
`up` includes a built-in target at `10.66.0.20`, so `nmap 10.66.0.0/24` finds something
immediately. Add more targets by giving any container/VM a `10.66.0.x` address on the
bridge; `lab-net.sh` (no args) prints a copy-paste one-liner for a second throwaway target.

### What the seal does and doesn't cover

The seal covers the *model's shell commands* — the one path a hallucination can take to the
network. It does not sandbox a bug in `redcoder.py` itself (which only ever dials
`127.0.0.1`); for that harder guarantee, run the whole thing in a container. Isolation is a
Linux feature — on the Windows build the flags exist but are not enforced (no firejail/
namespaces), and the header says so plainly.

---

## Quick start

```bash
git clone https://github.com/ninjahawk/redcoder-kali
cd redcoder-kali
./install-kali.sh
redcoder
```

`install-kali.sh` is idempotent — re-run it any time. It checks disk space, installs
Ollama if missing, configures the server for a q8_0 KV cache, pulls the base model,
builds the `redcoder-drago` model, and installs the launcher.

---

## What differs from the Windows build

| | Windows build | This build |
|---|---|---|
| Default model | `redcoder-max` (30B, 19 GB) | `redcoder-drago` (14B, 8.4 GB) |
| `NUM_CTX` | 32768 | 8192 |
| `MAX_STEPS` | 200 | 25 |
| `run_shell` | PowerShell | bash |
| System prompt | "Windows PC" | small-model rewrite + Kali briefing (below) |
| Loop detection | none | fingerprint + nudge + hard-stop |
| Dangerous-command backstop | none | confirm-on-dangerous, even in auto mode |
| Voice (hold-Space) | works | disabled — Windows-only API |
| Launcher | `redcoder.cmd` | `redcoder` + `install-kali.sh` |

The Python file still detects the platform at runtime, so it also runs correctly on
Windows — it just defaults to settings that suit the USB.

## Making a small model behave

A 14B is fast but cannot be reasoned with like a frontier model. Two failure modes show
up, and both are handled here — in code, not just by asking nicely in the prompt.

**Repetition — calling the same tool with the same arguments over and over.** Every tool
call is fingerprinted (`name` + `arguments`). On the 2nd identical call the harness skips
execution and injects a nudge ("you already ran this, use the result or answer"); on the
3rd it stops the turn and forces a final answer. This is the documented fix for small-model
loops — detect the repeat in the harness rather than trusting the model to notice.

A related cause was compaction: with a small context, compacting at 75% fired constantly,
and every compaction summarized away the specific tool results the model needed — so it
redid the work. Compaction now waits until 85%, keeps more recent history verbatim
(`KEEP_RECENT = 8`), and the summary is required to list "ACTIONS ALREADY COMPLETED".

**Aimless wandering — running tools with no goal, just because they exist.** The system
prompt was rewritten short and imperative for a small model. Its first rule is now *"Do
the LEAST work that answers the request, then STOP,"* every tool call must have a stated
purpose, and it must reuse earlier observations instead of re-fetching. `MAX_STEPS` is
cut from 200 to 25 so a wandering model can't do much damage before the turn ends.

**Destructive commands.** Because a small model will eventually emit something dangerous,
`run_shell` matches each command against a pattern list (`rm -rf`, `mkfs`, `dd of=`,
`mount`, writes to `/dev/nvme*` or `/dev/sd*`, `shutdown`, fork bombs, `apt purge`, ...).
A match forces an explicit confirmation **even in auto-approve mode**, defaulting to No.
It is a guardrail against mistakes, not a security boundary. Normal Kali tools (nmap,
grep, cat, ...) are unaffected. Edit `_DANGEROUS_PATTERNS` in `redcoder.py` to tune it.

These are tuning knobs, not guarantees — a small model still needs supervision. The
`--dangerously-skip-permissions` flag still bypasses ordinary write/edit prompts, but the
dangerous-command confirmation above ignores it by design.

### The Kali briefing

On Linux only, `KALI_NOTES` is appended to the system prompt. Without it the model
writes Python to do things Kali already has a dedicated tool for, and has no idea it's
on a live USB. It tells the model to:

- **Prefer installed tools.** Check `which` / `apt-cache policy` before reimplementing
  something — nmap, ffuf, sqlmap, hashcat, tcpdump, binwalk, radare2 and the rest are
  already there.
- **Watch disk space.** Everything installed lands on the small persistence partition;
  check `df -h /` before pulling anything large.
- **Never touch internal drives.** No mounting, writing, formatting or repartitioning
  `/dev/nvme*` or any non-USB disk — the host's own OS lives there. Stop and ask instead.
- **Not claim anonymity.** Kali routes traffic normally: no Tor, no MAC randomisation.
  This is not Tails and the model should never imply otherwise.
- **Use sudo sparingly** and say why when it does.

It costs about 900 tokens of the 8192-token window. Edit `KALI_NOTES` in `redcoder.py`
to change it; it is skipped entirely on Windows.

---

## Why these numbers

Token generation is **memory-bandwidth bound**: the GPU reads every weight once per
token, so `tokens/sec ≈ bandwidth ÷ model size`. The single thing that matters is
whether the model fits entirely in VRAM. If it spills to system RAM you drop from a
~670 GB/s bus to ~90 GB/s and lose most of your speed.

Budget for a 12 GB card with the 14B at Q4_K_M (~8.4 GB of weights). The KV cache costs
roughly **192 KB per token** (48 layers x 8 KV heads x 128 dim, fp16):

| Context | KV cache | Total | Verdict |
|---|---|---|---|
| 8192 | ~1.5 GB | ~9.9 GB | fits comfortably (default) |
| 16384 | ~3.1 GB | ~11.5 GB | fits, tight — use the q8_0 KV cache |
| 32768 | ~6.1 GB | ~14.5 GB | **does not fit** — spills, several times slower |

Expect roughly **50–60 tok/s** when it's fully GPU-resident.

**A trap worth knowing:** `redcoder.py` sends `num_ctx` in its API options, and that
**overrides whatever `config/Modelfile.redcoder-drago` says.** Changing the Modelfile alone
will not do what you expect — change `NUM_CTX` in `redcoder.py` too.

### The q8_0 KV cache

`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` roughly halve the cache,
which is what buys you the longer contexts above. These are read by the **Ollama server**,
not the client — exporting them in your own shell does nothing. `install-kali.sh` writes
them into a systemd drop-in. If there's no systemd service, start the server yourself:

```bash
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
ollama serve &
```

---

## The check that actually matters

```bash
ollama ps
```

`PROCESSOR` must read **100% GPU**. Anything mentioning CPU means the model spilled out
of VRAM, and you'll feel it immediately — agentic work makes several model calls per
action, so a slow model compounds badly.

Inside the TUI, `/set verbose` is not available (that's the `ollama run` REPL), but
`ollama ps` and `nvidia-smi` from a second terminal tell you everything. You want to see
roughly 9–10 GB of VRAM in use.

---

## GPU prerequisites

The RTX 50-series (Blackwell) needs **driver 570 or newer**, and Blackwell is supported
**only by NVIDIA's open kernel modules**. Debian and Kali package the 550.x series, which
does not support these cards — `apt install nvidia-driver` will install cleanly, build a
module, and never bind to the GPU.

Also note Kali's documentation does not support NVIDIA driver installation on a live
boot, and the kernel lives in the read-only squashfs, so `apt` installing a newer kernel
will not change what you actually boot. DKMS has to build against the running kernel:

```bash
uname -r
apt-cache search linux-headers | grep "$(uname -r)"
```

If no headers match your running kernel, stop — the driver cannot be built, and a full
install to an external SSD is the better path.

Everything in this repo works without a GPU; it just runs on CPU at low single-digit
tokens/sec, which is fine for verifying the setup and unusable for real work.

---

## Usage

```
redcoder                                     interactive (SEALED — no network)
redcoder --lab                               offline lab net (fake targets, no internet)
redcoder --online                            allow shell commands to reach the internet
redcoder --dangerously-skip-permissions      never ask before writes/edits/shell
redcoder -p "explain scan.py"                one-shot, print and exit
git diff | redcoder -p "review this diff"    pipe via stdin
redcoder -m some-other-model                 different Ollama model
```

In-session commands: `/help`, `/model`, `/net`, `/clear`, `/save`, `/resume`, `/auto`, `/cwd`.
`Ctrl-C` interrupts the current turn without losing context.

`/save` writes `./redcoder.md` — the one place Redcoder deliberately puts conversation
content on disk, so a session that can't continue can be resumed.

### Web UI

```bash
python3 server.py
```

Serves a chat UI on `http://127.0.0.1:7331`, loopback only.

---

## Files

```
redcoder.py                     the agent (stdlib only, no pip installs)
redcoder                        launcher; symlinked to /usr/local/bin by the installer
install-kali.sh                 one-time setup, idempotent
config/Modelfile.redcoder-drago  14B build for a 12 GB card (model name: redcoder-drago)
config/Modelfile.redcoder-max   30B build — will NOT fit a 12 GB card, kept for reference
server.py + webui/              optional local web chat
docs/README.md                  original project documentation
```

---

## Notes on the USB

Storage speed does **not** affect generation speed. Weights are read off the stick once
at load and live in VRAM after that, so a slow USB costs you about a minute at startup
and nothing thereafter — provided the model fits in VRAM.

The persistence partition is unencrypted by design in this setup. Anything you save,
including anything the agent writes, is readable by anyone who plugs the stick into
another machine.

## License

MIT
