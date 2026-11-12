import asyncio
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import main
import mcp_client
import mcp_server
import utils
from results import SearchResults, diagnostic
from tests.helpers import item

PROJECT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "list_sources",
    "search_publications",
    "search_europepmc",
    "search_openalex",
    "search_arxiv",
    "search_archive",
    "search_habr",
    "search_minzdrav",
    "summarize_text",
}


def test_mcp_sdk_registration_is_required_not_a_passing_skip():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    schemas = {tool.name: tool.input_schema for tool in tools}
    assert schemas["summarize_text"]["properties"]["text"]["maxLength"] == 10000


def test_real_stdio_discovery_and_russian_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACKER_DATA_DIR", str(tmp_path))

    async def exercise():
        state = await asyncio.wait_for(mcp_client.fetch_demo_state(), timeout=30)
        summary = await asyncio.wait_for(
            mcp_client.call_tool(
                "summarize_text",
                {
                    "text": "Агенты собирают публикации. Другой агент проверяет их релевантность. Результат показан пользователю.",
                    "max_sentences": 2,
                },
            ),
            timeout=30,
        )
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                mcp_client.call_tool("search_europepmc", {"topics": ["diabetes"], "rows": 0}), timeout=30
            )
        return state, summary

    state, summary = asyncio.run(exercise())
    assert set(state["tool_names"]) == EXPECTED_TOOLS and len(state["sources"]) == 6
    assert summary["summary"] and summary["method"].startswith("sumy_lsa")
    assert not (tmp_path / "history.json").exists()


def test_ui_default_has_no_secret_endpoint_controls_or_shared_file_actions():
    app = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=15).run()
    assert not app.exception
    assert app.multiselect[0].value == ["europepmc", "openalex", "minzdrav"]
    assert len(app.multiselect[0].options) == 6
    assert not app.text_input  # No endpoint, env-variable name or API-key form.
    labels = {button.label for button in app.button}
    assert "Сохранить в config.yaml" not in labels and "🗑 Очистить историю" not in labels
    assert app.radio[0].options == ["Локальный LSA (без LLM)"]


def test_ui_pipeline_roundtrip_and_session_only_history(monkeypatch):
    calls = []

    def process(**kwargs):
        calls.append(kwargs)
        return SearchResults(
            [
                item(
                    summary="A short abstract summary.",
                    summary_method="sumy_lsa_regex",
                    status="Резюме готово",
                ),
                item(
                    "minzdrav:test",
                    source="minzdrav",
                    title="Сахарный диабет",
                    summary="Карточка каталога.",
                    summary_method="metadata_only",
                    published="2026",
                    provenance_notice="Стороннее зеркало",
                    status="Метаданные",
                ),
            ],
            warnings=[diagnostic("minzdrav", "mirror_metadata_only", "Только метаданные зеркала.")],
            source_statuses={"europepmc": "ok", "minzdrav": "partial"},
        )

    monkeypatch.setattr(main, "process_items", process)
    app = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=15).run()
    next(c for c in app.checkbox if c.label == "Запоминать обработанные в этом сеансе").check().run()
    next(b for b in app.button if b.label == "Запустить поиск сейчас").click().run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["2", "1", "1"]
    assert calls[0]["save_history"] is False and calls[0]["send"] is False
    assert calls[0]["session_history"] == [] and not utils.HISTORY_PATH.exists()
    assert app.session_state["seen_ids"] == ["europepmc:1", "minzdrav:test"]
    assert any("Только метаданные" in warning.value for warning in app.warning)


def test_ui_empty_outage_not_reported_as_successful_empty_search(monkeypatch):
    monkeypatch.setattr(
        main,
        "process_items",
        lambda **kwargs: SearchResults(
            errors=[diagnostic("europepmc", "down", "Источник недоступен.")],
            source_statuses={"europepmc": "error"},
        ),
    )
    app = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=15).run()
    next(b for b in app.button if b.label == "Запустить поиск сейчас").click().run()
    assert not app.exception and app.error
    assert any("не доказывает" in warning.value for warning in app.warning)


def test_mcp_config_generator_preserves_venv_interpreter(monkeypatch):
    from tools import mcp_config

    executable = str(PROJECT / ".venv" / "bin" / "python")
    monkeypatch.setattr(mcp_config.sys, "executable", executable)
    config = mcp_config.build_config()["mcpServers"]["publication-tracker"]
    assert config["command"] == executable
    assert Path(config["args"][0]).is_absolute() and Path(config["args"][0]).is_file()


def test_mcp_subprocess_environment_is_whitelisted(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENALEX_API_KEY", "synthetic")
    monkeypatch.setenv("UNRELATED_SERVER_SECRET", "must-not-forward")
    params = mcp_client._server_parameters()
    assert params.env["TRACKER_DATA_DIR"] == str(tmp_path)
    assert params.env["OPENALEX_API_KEY"] == "synthetic"
    assert "UNRELATED_SERVER_SECRET" not in params.env
