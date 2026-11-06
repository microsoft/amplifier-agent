"""The model, reasoning effort, and approvals mode as session config options (acp-face.v1 section 9)."""

import asyncio

from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption
from amplifier_agent import AgentError, ModelRecord, ProviderRecord, SessionRecord, list_models, list_providers

from amplifier_agent_acp._approvals import Mode

# Every model of every provider that is installed and has credentials, in listing order.
Models = list[tuple[ProviderRecord, ModelRecord]]

_APPROVALS = [
    SessionConfigSelectOption(value="ask", name="Ask every time", description="Ask before every tool call."),
    SessionConfigSelectOption(
        value="allow-reads",
        name="Allow reads",
        description="Read and search files in the project without asking. Ask before everything else.",
    ),
    SessionConfigSelectOption(
        value="allow-all", name="Allow everything", description="Run every tool call without asking."
    ),
]


async def discover() -> Models:
    providers = [
        record
        for record in await list_providers()
        if record.installed and record.credentials in ("found", "not_required")
    ]
    listings = await asyncio.gather(*(list_models(record.provider) for record in providers), return_exceptions=True)
    models: Models = []
    for provider, listed in zip(providers, listings, strict=True):
        if isinstance(listed, AgentError):
            # A provider that cannot list its models offers none to select.
            continue
        if isinstance(listed, BaseException):
            raise listed
        models.extend((provider, model) for model in listed)
    return models


def _choice(
    provider: ProviderRecord | None, provider_id: str, model: ModelRecord | None, model_id: str
) -> SessionConfigSelectOption:
    shown = model.display_name if model is not None else model_id
    owner = provider.display_name if provider is not None else provider_id
    return SessionConfigSelectOption(value=f"{provider_id}/{model_id}", name=f"{shown} ({owner})")


def config_options(
    info: SessionRecord, models: Models | None, approvals: Mode | None
) -> list[SessionConfigOptionSelect]:
    """Every config option at the session's current values.

    `models` None offers the current model alone, and `approvals` None, under a host policy, offers no approvals mode.
    """
    current = f"{info.provider}/{info.model}"
    listed = models or []
    record = next(
        (model for provider, model in listed if provider.provider == info.provider and model.id == info.model), None
    )
    owner = next((provider for provider, _ in listed if provider.provider == info.provider), None)
    choices = [_choice(provider, provider.provider, model, model.id) for provider, model in listed]
    if record is None:
        choices.insert(0, _choice(owner, info.provider, None, info.model))
    options = [
        SessionConfigOptionSelect(
            type="select", id="model", name="Model", category="model", current_value=current, options=choices
        )
    ]
    if record is not None:
        efforts = list(record.reasoning_efforts or [])
    else:
        efforts = [] if info.reasoning_effort is None else [info.reasoning_effort]
    if efforts:
        if info.reasoning_effort is not None and info.reasoning_effort not in efforts:
            efforts.append(info.reasoning_effort)
        options.append(
            SessionConfigOptionSelect(
                type="select",
                id="reasoning_effort",
                name="Reasoning effort",
                category="thought_level",
                current_value=info.reasoning_effort or efforts[0],
                options=[SessionConfigSelectOption(value=effort, name=effort) for effort in efforts],
            )
        )
    if approvals is not None:
        options.append(
            SessionConfigOptionSelect(
                type="select",
                id="approvals",
                name="Approvals",
                description="Which tool calls run without asking.",
                category="mode",
                current_value=approvals,
                options=_APPROVALS,
            )
        )
    return options
