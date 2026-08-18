# Redcoder — Kali edition

An offline, Claude-Code-style terminal coding agent driven by a local abliterated model
through Ollama. This build is tuned for one specific setup: **a Kali Linux live USB with
persistence, running against a 12 GB NVIDIA GPU.**

Nothing leaves the machine. It talks only to `127.0.0.1:11434`, writes no transcript, no
history file, and no telemetry — only the files you ask it to change.

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
builds the `redcoder` model, and installs the launcher.

---

## What differs from the Windows build

| | Windows build | This build |
|---|---|---|
| Default model | `redcoder-max` (30B, 19 GB) | `redcoder` (14B, 8.4 GB) |
| `NUM_CTX` | 32768 | 8192 |
| `run_shell` | PowerShell | bash |
| System prompt | "Windows PC" | "Kali Linux machine" + Kali briefing (below) |
| Voice (hold-Space) | works | disabled — Windows-only API |
| Launcher | `redcoder.cmd` | `redcoder` + `install-kali.sh` |

The Python file still detects the platform at runtime, so it also runs correctly on
Windows — it just defaults to settings that suit the USB.

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
**overrides whatever `config/Modelfile.redcoder` says.** Changing the Modelfile alone
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
redcoder                                     interactive
redcoder --dangerously-skip-permissions      never ask before writes/edits/shell
redcoder -p "explain scan.py"                one-shot, print and exit
git diff | redcoder -p "review this diff"    pipe via stdin
redcoder -m some-other-model                 different Ollama model
```

In-session commands: `/help`, `/model`, `/clear`, `/save`, `/resume`, `/auto`, `/cwd`.
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
config/Modelfile.redcoder       14B build for a 12 GB card
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
