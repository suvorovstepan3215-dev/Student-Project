#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Сначала выполните: sh setup.sh' >&2
  exit 1
fi
ADDRESS="${STREAMLIT_ADDRESS:-127.0.0.1}"
printf '%s\n' "Streamlit: http://${ADDRESS}:8501  (остановить: Ctrl+C)" >&2
exec .venv/bin/python -m streamlit run streamlit_app.py --server.address="$ADDRESS" "$@"
