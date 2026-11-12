#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf '%s\n' "Python 3.11+ не найден ($PYTHON). Установите Python и повторите; при необходимости задайте PYTHON=python3.11." >&2
  exit 1
fi
"$PYTHON" -c 'import sys; sys.exit("Требуется Python 3.11+ (найден %s)." % ".".join(map(str, sys.version_info[:3]))) if sys.version_info < (3, 11) else None'
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit("Существующий .venv требует Python 3.11+.") if sys.version_info < (3, 11) else None'
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
exec .venv/bin/python check_setup.py
