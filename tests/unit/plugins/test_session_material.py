"""Unit tests for wiki session upload helpers."""

from __future__ import annotations

import pytest

from agentium.plugins.llm_wiki.session_material import (
    SessionMaterialUploadError,
    assert_safe_chat_session_id,
    sanitize_upload_filename,
    validate_decoded_size,
)


def test_sanitize_upload_filename_keeps_suffix() -> None:
    assert sanitize_upload_filename("notes/hello.md").endswith(".md")
    assert sanitize_upload_filename("/tmp/foo/bar.TXT").endswith(".TXT")


def test_sanitize_upload_filename_rejects_bad_suffix() -> None:
    with pytest.raises(SessionMaterialUploadError) as ei:
        sanitize_upload_filename("x.exe")
    assert ei.value.code == "invalid_file_suffix"


def test_assert_safe_chat_session_id_rejects_traversal_like() -> None:
    with pytest.raises(SessionMaterialUploadError) as ei:
        assert_safe_chat_session_id("../evil")
    assert ei.value.code == "invalid_session_id"


def test_validate_decoded_size_empty() -> None:
    with pytest.raises(SessionMaterialUploadError) as ei:
        validate_decoded_size(raw_len=0, max_decoded_bytes=100)
    assert ei.value.code == "empty_payload"


def test_validate_decoded_size_too_large() -> None:
    with pytest.raises(SessionMaterialUploadError) as ei:
        validate_decoded_size(raw_len=10, max_decoded_bytes=5)
    assert ei.value.code == "payload_too_large"
