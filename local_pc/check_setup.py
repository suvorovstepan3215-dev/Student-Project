"""Offline smoke check for this standalone MCP kit (no external API calls)."""

from pathlib import Path

import utils
from mcp_client import call_tool_sync, fetch_demo_state_sync

EXPECTED_TOOLS = {
    "list_sources",
    "search_publications",
    "search_europepmc",
    "search_minzdrav",
    "search_archive",
    "search_arxiv",
    "search_openalex",
    "search_habr",
    "summarize_text",
}


def main() -> int:
    directory = Path(__file__).resolve().parent
    if utils.BASE_DIR != directory:
        raise RuntimeError(f"Imported another copy: {utils.BASE_DIR}")
    if not (directory / "tools" / "mcp_config.py").is_file():
        raise RuntimeError("tools/mcp_config.py is missing.")
    state = fetch_demo_state_sync()
    if set(state["tool_names"]) != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected MCP tools: {sorted(state['tool_names'])}")
    if len(state["sources"]) != 6:
        raise RuntimeError(f"Expected 6 sources, got {len(state['sources'])}")
    result = call_tool_sync(
        "summarize_text",
        {
            "text": ("Агенты собирают публикации. Другой агент проверяет релевантность. "
                     "Результат показан пользователю."),
            "max_sentences": 2,
        },
    )
    if not result.get("summary"):
        raise RuntimeError("summarize_text returned no summary.")
    if not str(result.get("method", "")).startswith(("sumy_lsa", "sumy_lsa_regex")):
        raise RuntimeError(f"Actual LSA unavailable: {result.get('method')}")
    print("OK: real MCP stdio handshake, 9 tools, 6 sources, actual Russian LSA.")
    print("No source API requests were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
