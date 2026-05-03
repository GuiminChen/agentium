import pytest

from agentium.shared.errors import ConfigurationError
from agentium.tools.builtin import ai_reviewer


class _FakeAsyncOpenAI:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key


def test_ai_reviewer_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_reviewer, "AsyncOpenAI", _FakeAsyncOpenAI)
    with pytest.raises(ConfigurationError):
        ai_reviewer.AIReviewer(api_key="")


def test_ai_reviewer_init_success(monkeypatch) -> None:
    monkeypatch.setattr(ai_reviewer, "AsyncOpenAI", _FakeAsyncOpenAI)
    reviewer = ai_reviewer.AIReviewer(api_key="test-key")
    assert reviewer.client.api_key == "test-key"
