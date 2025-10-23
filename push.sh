#!/usr/bin/env bash
set -euo pipefail
REPO="${1:-}"
FILE="${2:-}"

if [[ -z "$REPO" ]]; then
  echo "[push.sh] repo path missing" >&2
  exit 2
fi
if [[ ! -d "$REPO/.git" ]]; then
  echo "[push.sh] $REPO is not a git repo" >&2
  exit 3
fi

cd "$REPO"
git add -A
git commit -m "journal update: $(date -Iseconds)" || echo "[push.sh] nothing to commit."
git push
echo "[push.sh] pushed successfully."
