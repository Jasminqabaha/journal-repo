#!/usr/bin/env bash

REPO=$1
FILE=$2

if [[ ! -d "$REPO/.git" ]]; then
  echo "[push.sh] $REPO is not a git repo" >&2
  exit 3
fi

cd "$REPO"
git add -A
git commit -m "journal update: $(date -Iseconds)" || true
git push
echo "[push.sh] pushed successfully."
