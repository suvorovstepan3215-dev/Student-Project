#!/bin/zsh
# Запуск MCP-сервера ВСЕГДА из виртуального окружения проекта (.venv).
# Сервер говорит по stdio (JSON-RPC), поэтому запускать вручную в терминале
# бессмысленно: его поднимает MCP-host (Cursor, Claude Desktop) или mcp_client.py.
cd "$(dirname "$0")"
exec .venv/bin/python mcp_server.py "$@"
