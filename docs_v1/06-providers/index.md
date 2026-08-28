# Providers

Nine providers ship with the agent. Naming one in `ProviderConfig` and having a
credential it can resolve is the whole setup.

```python
ProviderConfig(name="anthropic", model="claude-sonnet-5")
```

- [Anthropic](anthropic.md)
- [OpenAI](openai.md)
- [Azure OpenAI](azure-openai.md)
- [Gemini](gemini.md)
- [GitHub Copilot](github-copilot.md)
- [OpenAI ChatGPT](openai-chatgpt.md)
- [Ollama](ollama.md)
- [vLLM](vllm.md)
- [Chat Completions](chat-completions.md)

Each page covers that provider's credentials, its environment variables, its
default model, and anything specific to it.

The mechanism behind all of them, including the descriptor types, the resolution
order, and discovery, is in [Providers](../05-interface/providers.md). This
section is the per-provider detail.

## Which one

**Hosted, and you have a key.** Anthropic, OpenAI, Gemini. One environment
variable each and you are running.

**Hosted, through your organization.** Azure OpenAI for an Azure deployment,
GitHub Copilot for a Copilot subscription, OpenAI ChatGPT for a ChatGPT account
rather than an API key.

**Local or self-hosted.** Ollama for models on your own machine, vLLM for a
server you run. Neither sends anything outside your network, which is usually the
reason to pick them.

**Anything else that speaks the OpenAI API.** Chat Completions points at an
arbitrary base URL. Use it for a gateway, a proxy, or a provider not listed here.

## Credentials

Environment first, then the stored credential file. This is the `gh` and `aws`
convention: a one-off export points a single run somewhere else without
disturbing what you have saved.

```
1. ProviderConfig.credentials
2. the provider's environment variable
3. the credential store
```

Each provider page names its own variables. To check what resolves right now:

```bash
amplifier-agent doctor
```

That reports every provider, whether its credentials resolve, and where each one
came from. It is the fastest answer to why an agent says it cannot find a model.

The same information is available in code, without constructing an agent:

```python
from amplifier_agent import list_providers

for status in await list_providers():
    print(status.descriptor.name, status.available, status.credential_source)
```

## Models

The agent holds no table of model names. A provider reports its own models at
runtime, so the list cannot drift from what the provider actually offers.

```bash
amplifier-agent models list --provider anthropic
```

```python
from amplifier_agent import list_models

for model in await list_models("anthropic"):
    print(model.id, model.context_window)
```

Omit `model` in `ProviderConfig` to take the provider's default.
