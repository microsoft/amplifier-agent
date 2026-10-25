"""Provider discovery: `list_providers` reports whether a provider is installed and has credentials, before any turn."""

from amplifier_agent import AgentError, list_providers


async def check_credentials(provider: str) -> str | None:
    """A message naming what to set when the provider cannot run, or None when it can."""
    try:
        records = await list_providers()
    except AgentError as error:
        return f"{error.message} {error.remedy}"
    record = next((item for item in records if item.provider == provider), None)
    if record is None:
        return f"Unknown provider {provider}. Choose one of: {', '.join(item.provider for item in records)}."
    if not record.installed:
        return f"The {provider} provider is not installed. Add its amplifier-agent extra to pyproject.toml."
    if record.credentials == "missing":
        return f"Set {' or '.join(record.credential_variables)} for the {provider} provider."
    return None
