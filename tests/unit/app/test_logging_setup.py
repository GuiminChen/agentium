"""Tests for :mod:`agentium.app.logging_setup`."""

from __future__ import annotations

import logging

import pytest
import structlog

from agentium.app.logging_setup import reset_logging_for_tests, setup_logging
from agentium.app.settings import load_settings


def test_setup_logging_writes_structlog_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_LOG_FILE", str(tmp_path / "run.log"))
    monkeypatch.setenv("AGENTIUM_LOG_CONSOLE", "0")
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")

    reset_logging_for_tests()
    settings = load_settings()
    setup_logging(settings)

    structlog.get_logger("logging_setup_integration").info("rotating_file_probe")
    logging.shutdown()

    assert "rotating_file_probe" in (tmp_path / "run.log").read_text(encoding="utf-8")
