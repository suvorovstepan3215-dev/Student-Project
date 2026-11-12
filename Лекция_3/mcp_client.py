# mcp_client.py
"""Короткоживущий MCP-клиент по stdio. Каждая операция поднимает сервер и закрывает его.

Клиент нужен для демо и для Streamlit: он не импортирует агентов напрямую,
а говорит с ``mcp_server.py`` по протоколу MCP — как любой внешний host
(Cursor, Claude Desktop). Сервер запускается из папки проекта, поэтому
импорты агентов работают без установки пакета.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROJECT_DIR = Path(__file__).resolve().parent


def _server_parameters() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_DIR / "mcp_server.py")],
        cwd=str(PROJECT_DIR),
        env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )


def _text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def payload_from_result(result: Any) -> Any:
    if getattr(result, "is_error", False):
        message = _text(result) or "MCP tool вернул ошибку без текста."
        raise RuntimeError(message)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    text = _text(result)
    if not text:
        raise RuntimeError("MCP tool вернул пустой ответ.")
    return json.loads(text)


def _unwrap_group(exc: BaseException) -> BaseException:
    current = exc
    while isinstance(current, BaseExceptionGroup) and len(current.exceptions) == 1:
        current = current.exceptions[0]
    return current


async def _with_session(callback):
    try:
        async with stdio_client(_server_parameters()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await callback(session)
    except BaseExceptionGroup as exc:
        unwrapped = _unwrap_group(exc)
        if unwrapped is exc:
            raise
        raise unwrapped from exc


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    async def _run(session: ClientSession):
        result = await session.call_tool(name, arguments or {})
        return payload_from_result(result)

    return await _with_session(_run)


async def fetch_demo_state() -> dict[str, Any]:
    async def _run(session: ClientSession):
        listed = await session.list_tools()
        catalog = payload_from_result(await session.call_tool("list_sources", {}))
        return {
            "tool_names": [tool.name for tool in listed.tools],
            "sources": catalog["sources"],
        }

    return await _with_session(_run)


def run_async(coro):
    return asyncio.run(coro)


def call_tool_sync(name: str, arguments: dict[str, Any] | None = None) -> Any:
    return run_async(call_tool(name, arguments))


def fetch_demo_state_sync() -> dict[str, Any]:
    return run_async(fetch_demo_state())


if __name__ == "__main__":
    # Демо без сети: каталог источников и список tools.
    state = fetch_demo_state_sync()
    print("Tools:", ", ".join(state["tool_names"]))
    for source in state["sources"]:
        print(f"  {source['id']}: {source['name']}")
