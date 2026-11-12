"""Regression tests for the two standalone coursework kits and their builder."""

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
KITS = ("streamlit_web", "local_pc")


def load_builder():
    path = ROOT / "tools" / "build_variants.py"
    spec = importlib.util.spec_from_file_location("build_variants", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return load_builder()


@pytest.fixture(scope="module")
def payloads(builder):
    return builder.payloads()


def test_both_kits_exist_and_cover_their_entrypoints(payloads):
    assert set(payloads) == set(KITS)
    web, local = payloads["streamlit_web"], payloads["local_pc"]
    assert {"streamlit_app.py", "main.py", "notifier.py", "config.yaml", "requirements.txt"} <= set(web)
    assert {"mcp_server.py", "mcp_service.py", "mcp_client.py", "tools/mcp_config.py"} <= set(local)
    assert "streamlit_app.py" not in local and "mcp_server.py" not in web
    # Only the web kit ships the rendered architecture diagram.
    assert "diagr/agents.html" in web and not any(name.startswith("diagr/") for name in local)


def test_kits_are_runnable_without_the_monorepo(payloads):
    web, local = payloads["streamlit_web"], payloads["local_pc"]
    assert "streamlit==1.67.0" in web["requirements.txt"].decode()
    assert "numpy==2.4.6" in web["requirements.txt"].decode()
    # The MCP kit must not drag in Streamlit/pandas/APScheduler.
    local_requirements = local["requirements.txt"].decode()
    assert "mcp==2.3.0" in local_requirements and "numpy==2.4.6" in local_requirements
    for excluded in ("streamlit==", "pandas==", "apscheduler==", "pillow=="):
        assert excluded not in local_requirements
    for name, files in payloads.items():
        assert files["config.yaml"].decode().strip()
        assert files["setup.sh"].startswith(b"#!/usr/bin/env sh")
        assert files[".gitignore"]
        assert json.loads(files["BUILD_INFO.json"])["profile"] == name


def test_no_runtime_state_secrets_or_absolute_developer_paths_in_payloads(payloads):
    forbidden_names = {".venv", "history.json", "main.log", "atomic-chat.json", "secrets.toml"}
    for files in payloads.values():
        for name, content in files.items():
            assert Path(name).name not in forbidden_names, name
            # A developer's machine path must never be baked into a kit.
            assert b"/home/user" not in content, name
        # Only the env-var NAME may appear; never a literal key value.
        for files in payloads.values():
            config = files["config.yaml"].decode()
            assert 'api_key_env: "OPENAI_API_KEY"' in config and "sk-" not in config
    web_config = payloads["streamlit_web"]["config.yaml"].decode()
    assert "enable_llm: false" in web_config
    assert 'api_key: ""' in web_config


def test_tracked_kits_are_in_sync_with_their_sources(builder):
    assert builder.stale_files(ROOT) == []


def test_archives_match_allowlist_and_are_reproducible(builder, tmp_path):
    archives = builder.create_archives(tmp_path)
    assert [path.name for path in archives] == ["streamlit_web.zip", "local_pc.zip"]
    expected = builder.payloads()
    for path in archives:
        profile = path.stem
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            assert sorted(archive.namelist()) == sorted(f"{profile}/{name}" for name in expected[profile])
            assert archive.read(f"{profile}/setup.sh").startswith(b"#!/usr/bin/env sh")
            # A local virtual environment or runtime log cannot enter a release.
            assert not any(
                ".venv/" in name or name.endswith((".log", "history.json")) for name in archive.namelist()
            )
    again = builder.create_archives(tmp_path)
    assert [a.read_bytes() for a in archives] == [b.read_bytes() for b in again]


def test_atomic_generator_emits_single_server_object_without_cwd():
    path = ROOT / "local_pc" / "tools" / "mcp_config.py"
    spec = importlib.util.spec_from_file_location("kit_mcp_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    atomic = module.atomic_config()
    assert set(atomic) == {"command", "args", "env"} and "cwd" not in atomic
    assert atomic["args"] == [str(ROOT / "local_pc" / "mcp_server.py")]
    assert atomic["env"]["PYTHONUTF8"] == "1"
    assert set(module.build_config()["mcpServers"]["publication-tracker"]) == {
        "command",
        "args",
        "cwd",
        "env",
    }


def test_kit_smoke_check_imports_its_own_copy_from_any_working_directory(tmp_path):
    """Each kit must import itself (not the monorepo) regardless of the caller's cwd."""
    venv = PROJECT / ".venv"
    if not (venv / "bin" / "python").is_file():
        pytest.skip("Local virtual environment is unavailable")
    results = {}
    for kit in KITS:
        code = "import sys; sys.path.insert(0, r'%s'); import utils; print(utils.BASE_DIR)" % (ROOT / kit)
        output = subprocess.run(
            [str(venv / "bin" / "python"), "-c", code],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        results[kit] = Path(output.stdout.strip())
    assert results["streamlit_web"] == ROOT / "streamlit_web"
    assert results["local_pc"] == ROOT / "local_pc"
    assert sys.version_info >= (3, 11)
