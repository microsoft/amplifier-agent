"""Hold the credentials an unset provider defaults from fixed, so tests never read the host's."""

# Every variable the engine's default provider choice reads, in its preference order.
DEFAULT_CREDENTIALS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "COPILOT_AGENT_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def anthropic_only(monkeypatch):
    """An Anthropic key alone, so an unset provider resolves to anthropic as before the default changed."""
    for key in DEFAULT_CREDENTIALS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-api-key")
