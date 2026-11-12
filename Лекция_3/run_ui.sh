#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Create the environment first: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt' >&2
  exit 1
fi
exec "$PYTHON" -m streamlit run streamlit_app.py "$@"
