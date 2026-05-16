"""Tests for ``wiki_http_schemas``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentium.api.http.wiki_http_schemas import WikiSessionUploadRequest


def test_wiki_session_upload_request_ok() -> None:
    req = WikiSessionUploadRequest.model_validate(
        {
            "session_id": "sess-1",
            "filename": "a.md",
            "content_base64": "YWJjZA==",
        }
    )
    assert req.session_id == "sess-1"
    assert req.filename == "a.md"


def test_wiki_session_upload_request_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        WikiSessionUploadRequest.model_validate(
            {
                "session_id": "s",
                "filename": "a.md",
                "content_base64": "YQ==",
                "evil": True,
            }
        )
