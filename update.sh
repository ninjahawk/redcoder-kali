#!/usr/bin/env bash
# redcoder — one-command update. Pulls the latest from GitHub (ninjahawk/redcoder-kali).
# Works whether this folder is a git clone or just a copied-over redcoder.py. Your own data
# (redcoder.md checkpoints, evals/runs) is untracked/gitignored and is left completely alone.
#
#   Run it from the redcoder folder:   ./update.sh      (or:  bash update.sh)
#
# Needs internet for the update itself — that's separate from redcoder's own /net mode.
set -e
cd "$(dirname "$0")"
RAW="https://raw.githubusercontent.com/ninjahawk/redcoder-kali/main/redcoder.py"

if [ -d .git ]; then
    echo "→ updating via git…"
    git fetch --quiet origin main
    git reset --hard --quiet origin/main        # match the published version exactly (data untouched)
    echo "✓ updated to $(git rev-parse --short HEAD)  —  $(git log -1 --format=%s)"
else
    echo "→ not a git clone; fetching the latest redcoder.py directly…"
    curl -fL -o redcoder.py "$RAW"
    echo "✓ redcoder.py updated."
fi
echo "Done. Run redcoder as usual."
