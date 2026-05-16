"""Unit tests for chat session title normalization helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agentium.coordination.chat_session_title_job as title_job_mod
from agentium.coordination.chat_session_title_job import (
    clip_excerpt,
    normalize_generated_title,
    run_chat_session_title_job,
)
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from tests.unit.app.test_identity_factory import _minimal_app_settings


def test_clip_excerpt_trims_and_caps() -> None:
    long = "x" * 2000
    out = clip_excerpt(long, cap=10)
    assert len(out) == 11
    assert out.endswith("…")


def test_clip_excerpt_normalizes_newlines() -> None:
    assert clip_excerpt("  a\r\nb\r c  ", cap=100) == "a\nb\n c"


def test_normalize_generated_title_collapses_whitespace() -> None:
    assert normalize_generated_title("  hello   world  ") == "hello world"


def test_normalize_generated_title_strips_wrappers() -> None:
    assert normalize_generated_title('"My chat"') == "My chat"


def test_normalize_generated_title_empty_returns_none() -> None:
    assert normalize_generated_title("") is None
    assert normalize_generated_title("   ") is None


def test_normalize_generated_title_max_len() -> None:
    raw = "x" * 200
    out = normalize_generated_title(raw, max_len=10)
    assert out is not None
    assert len(out) == 10
    assert out.endswith("…")


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({}, False),
        ({"session_title_source": "user"}, True),
        ({"session_title_auto_status": "skipped"}, True),
        ({"session_title_auto_status": "complete"}, True),
        ({"session_title_auto_status": "scheduled"}, False),
    ],
)
def test_should_abort_title_after_metadata_refresh(meta: dict[str, object], expected: bool) -> None:
    assert title_job_mod._should_abort_title_after_metadata_refresh(meta) == expected


def test_run_chat_session_title_job_respects_user_rename_after_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale post-LLM update must not overwrite a title the user set during the LLM call."""

    settings = _minimal_app_settings(tmp_path)
    db_path = settings.sqlite_db_path
    store = SqliteChatSessionStore(db_path)
    store.create_session(
        tenant_id="tenant1",
        session_id="sess1",
        title="seed",
        skill="workspace_agent",
        intro_text=None,
        metadata={"session_title_auto_status": "scheduled"},
    )
    store.close()

    mock_client = MagicMock()

    def _complete_chat_side_effect(*_a: object, **_kw: object) -> MagicMock:
        mid = SqliteChatSessionStore(db_path)
        rec = mid.get_session(tenant_id="tenant1", session_id="sess1")
        md = dict(rec.metadata or {})
        md["session_title_source"] = "user"
        md["session_title_auto_status"] = "skipped"
        mid.update_session(
            tenant_id="tenant1",
            session_id="sess1",
            title="User locked title",
            skill=rec.skill,
            metadata=md,
        )
        mid.close()
        return MagicMock(text="LLM would win")

    mock_client.complete_chat = MagicMock(side_effect=_complete_chat_side_effect)
    monkeypatch.setattr(title_job_mod, "_deepseek_client", lambda _settings: mock_client)

    run_chat_session_title_job(
        settings=settings,
        tenant_id="tenant1",
        session_id="sess1",
        user_excerpt="hi",
        assistant_excerpt="there",
    )

    final_store = SqliteChatSessionStore(db_path)
    try:
        final = final_store.get_session(tenant_id="tenant1", session_id="sess1")
    finally:
        final_store.close()
    assert final.title == "User locked title"
