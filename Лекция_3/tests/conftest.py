import sys
from pathlib import Path

import pytest
import requests
import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import utils  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    """Never touch developer history/config or accidentally use the Internet."""
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(utils, "CONFIG_PATH", path)
    monkeypatch.setattr(utils, "HISTORY_PATH", tmp_path / "history.json")

    def no_http(*args, **kwargs):
        raise AssertionError("Unexpected network request in offline test")

    monkeypatch.setattr(requests.Session, "request", no_http)
    return config
