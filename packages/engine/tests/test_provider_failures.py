from amplifier_agent_engine._engine.adapters import transient
from amplifier_core.llm_errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    LLMTimeoutError,
    NetworkError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamError,
)
import pytest


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderUnavailableError, True),
        (NetworkError, True),
        (RateLimitError, True),
        (LLMTimeoutError, True),
        (StreamError, True),
        (QuotaExceededError, False),
        (AuthenticationError, False),
        (InvalidRequestError, False),
        (ContentFilterError, False),
    ],
)
def test_transient_follows_the_error_type_not_its_retryable_flag(error, expected):
    assert transient(error("failed", retryable=False)) is expected
