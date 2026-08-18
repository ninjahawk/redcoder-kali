#!/usr/bin/env bash
#
# Redcoder (Kali edition) — one-time setup.
#
# Target: Kali Linux live USB with persistence, talking to a 12 GB NVIDIA GPU.
# Safe to re-run; every step is idempotent.
#
# It does NOT touch any internal drive. Everything lands on the persistence
# partition (the overlay covering /), so it survives reboots.

set -euo pipefail

BASE_MODEL="huihui_ai/qwen2.5-coder-abliterate:14b"
MODEL_NAME="redcoder-drago"
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

say()  { printf '\n\033[1;31m==>\033[0m \033[1m%s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$1"; }

fetch() {  # fetch <url> -> stdout ; works with curl or wget
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"
  elif command -v wget >/dev/null 2>&1; then wget -qO- "$1"
  else echo "neither curl nor wget is installed" >&2; return 1
  fi
}

# --------------------------------------------------------------------------- #
say "Checking disk space"
AVAIL_KB=$(df -Pk / | awk 'NR==2 {print $4}')
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
echo "    ${AVAIL_GB} GB free on /"
if [ "$AVAIL_GB" -lt 11 ]; then
  warn "Need roughly 11 GB free (8.4 GB model + Ollama + headroom)."
  warn "Free some space or point OLLAMA_MODELS at external storage, then re-run."
  exit 1
fi
ok "enough space"

# --------------------------------------------------------------------------- #
say "Checking Python"
command -v python3 >/dev/null 2>&1 || {
  echo "    python3 not found. Install it:  sudo apt install -y python3" >&2; exit 1; }
ok "$(python3 --version)"

# --------------------------------------------------------------------------- #
# Network isolation. Airgapped mode uses `unshare -rn` (util-linux — always on
# Kali); Lab mode uses `ip netns exec` (iproute2 — always on Kali). firejail is a
# nicer optional backend but is NOT required and is often absent from the current
# Kali repo, so we only note whether it happens to be present.
say "Checking network-isolation tools"
command -v unshare >/dev/null 2>&1 && ok "unshare present — Airgapped mode ready" \
  || warn "unshare (util-linux) missing — Airgapped mode will refuse to run shell"
command -v ip >/dev/null 2>&1 && ok "ip present — Lab mode ready (with ./lab-net.sh up)" \
  || warn "ip (iproute2) missing — Lab mode unavailable"
command -v firejail >/dev/null 2>&1 && ok "firejail present (optional nicer backend)" \
  || ok "firejail not installed — not needed"

# --------------------------------------------------------------------------- #
say "Checking Ollama"
if command -v ollama >/dev/null 2>&1; then
  ok "$(ollama --version 2>/dev/null | head -1)"
else
  warn "not installed — fetching the official installer from ollama.com"
  fetch https://ollama.com/install.sh | sh
fi

# --------------------------------------------------------------------------- #
# These are read by the Ollama SERVER, not the client, so exporting them in your
# own shell does nothing. They have to be set on the service.
# q8_0 KV cache halves the cache footprint, which is what keeps the 14B fully
# resident on a 12 GB card at longer contexts.
say "Configuring the Ollama server (flash attention + q8_0 KV cache)"
if command -v systemctl >/dev/null 2>&1 \
   && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
EOF
  sudo systemctl daemon-reload
  sudo systemctl restart ollama || warn "could not restart ollama.service"
  ok "systemd drop-in written and service restarted"
else
  warn "no ollama systemd service found (common in a live session)."
  warn "Start the server yourself with these set:"
  echo "        export OLLAMA_FLASH_ATTENTION=1"
  echo "        export OLLAMA_KV_CACHE_TYPE=q8_0"
  echo "        ollama serve &"
fi

# --------------------------------------------------------------------------- #
say "Waiting for the Ollama API"
UP=0
for _ in $(seq 1 40); do
  if fetch http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then UP=1; break; fi
  sleep 1
done
if [ "$UP" -ne 1 ]; then
  warn "Ollama is not answering on 127.0.0.1:11434."
  warn "Start it in another terminal ('ollama serve'), then re-run this script."
  exit 1
fi
ok "API is up"

# --------------------------------------------------------------------------- #
say "Pulling the base model — ${BASE_MODEL} (~8.4 GB, this takes a while)"
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$BASE_MODEL"; then
  ok "already present, skipping download"
else
  ollama pull "$BASE_MODEL"
fi

# --------------------------------------------------------------------------- #
say "Building the '${MODEL_NAME}' model from config/Modelfile.redcoder-drago"
ollama create "$MODEL_NAME" -f "$HERE/config/Modelfile.redcoder-drago"
ok "created '${MODEL_NAME}'"

# --------------------------------------------------------------------------- #
say "Installing the launcher"
chmod +x "$HERE/redcoder"
[ -f "$HERE/lab-net.sh" ] && chmod +x "$HERE/lab-net.sh" || true
if sudo ln -sf "$HERE/redcoder" /usr/local/bin/redcoder 2>/dev/null; then
  ok "/usr/local/bin/redcoder -> $HERE/redcoder"
else
  warn "could not symlink into /usr/local/bin; run it as $HERE/redcoder"
fi

# --------------------------------------------------------------------------- #
say "Done"
cat <<EOF

    Start it:        redcoder            (Airgapped by default — shell has NO network)
    Offline lab:     redcoder --lab      (builds the lab itself; sudo password once per boot)
    Allow internet:  redcoder --online   (or /net online inside a session)
    One-shot:        redcoder -p "explain scan.py"

    VERIFY GPU USE — this is the one check that matters:

        redcoder            (ask it anything, so the model loads)
        ollama ps           (in another terminal)

    PROCESSOR should read 100% GPU. Anything mentioning CPU means the model
    spilled out of VRAM and you will be several times slower. If that happens,
    lower NUM_CTX in redcoder.py and rebuild with:
        ollama create $MODEL_NAME -f config/Modelfile.redcoder-drago

EOF
