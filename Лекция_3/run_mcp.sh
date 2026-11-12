#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Create .venv and install requirements.txt first.' >&2
  exit 1
fi
# stdout is reserved for MCP JSON-RPC. Diagnostics above go to stderr.
exec "$PYTHON" mcp_server.py "$@"
