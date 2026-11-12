#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Create .venv and install requirements.txt first.' >&2
  exit 1
fi
exec "$PYTHON" main.py "$@"
