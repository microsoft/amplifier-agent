# Providers

One provider per agent. Its id is what you set as `provider`, in code or in
[configuration](configuration.md).

Model ids are the provider's own. Name one your account can actually reach, because a
named model is honored or the turn fails `selector_rejected`. See
[models](concepts/models.md).

## Providers and credentials

```text
anthropic          ANTHROPIC_API_KEY
openai             OPENAI_API_KEY
azure-openai       AZURE_OPENAI_API_KEY or Azure identity credentials
ollama             optional OLLAMA_API_KEY
github-copilot     Copilot token or cached SDK login
openai-chatgpt     ChatGPT OAuth login
chat-completions   optional CHAT_COMPLETIONS_API_KEY
gemini             GOOGLE_API_KEY or GEMINI_API_KEY
vllm               optional VLLM_API_KEY
```

Set credentials in the environment running your application or HTTP server. Choose
one provider and model in agent options or through `AMPLIFIER_AGENT_PROVIDER` and
`AMPLIFIER_AGENT_MODEL`. Credentials do not select a provider automatically.

The [Python](python/quickstart.md) and [TypeScript](typescript/quickstart.md)
examples use Anthropic with `claude-sonnet-5`; `claude-opus-5` is another Anthropic
selection. Other providers use their own model or deployment IDs. Code takes
precedence over environment settings.

## Endpoints and accounts

- **Azure OpenAI:** set `AZURE_OPENAI_ENDPOINT` and use the deployment's model ID.
  An API key, managed identity, or default Azure credentials authenticate the account.
- **Ollama:** `OLLAMA_HOST` selects the server; the local default is
  `http://localhost:11434`. Authentication depends on that server.
- **Chat Completions:** set `CHAT_COMPLETIONS_BASE_URL` for an OpenAI-compatible
  endpoint, and supply a key if it requires one.
- **vLLM:** `VLLM_BASE_URL` selects the Responses API endpoint; the local default is
  `http://localhost:8000/v1`.

Endpoint and authentication settings configure the connection.
`extra_request_params` contains provider request fields, not connection credentials.

## Subscription authentication

`github-copilot` uses the first nonempty token in `COPILOT_AGENT_TOKEN`,
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, or an existing SDK login.
It is one provider even when its model catalog spans multiple model families.

`openai-chatgpt` uses a ChatGPT subscription through OAuth device-code login, with
cached tokens under `~/.amplifier/openai-chatgpt-oauth.json`. This is separate from
the `openai` API provider and its `OPENAI_API_KEY` credential.

## Statelessness

Every provider is asked to keep nothing, and none of them holds anything a session
depends on. This is not per-provider behavior you have to check for; it is the posture,
and turning it off for one provider is an explicit act. See
[configuration](configuration.md).
