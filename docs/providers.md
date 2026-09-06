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
  An API key, managed identity, or `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and
  `AZURE_CLIENT_SECRET` authenticate the account. An API key takes precedence; without
  it, all three identity variables select client-secret authentication, otherwise
  managed identity is used. Azure CLI and workload-identity login are not consulted.
  Supply the resource endpoint without an `/openai/v1` suffix.
- **Ollama:** `OLLAMA_HOST` selects the server; the local default is
  `http://localhost:11434`. Authentication depends on that server.
- **Chat Completions:** set `CHAT_COMPLETIONS_BASE_URL` for an OpenAI-compatible
  endpoint, and supply a key if it requires one.
- **vLLM:** `VLLM_BASE_URL` selects the Responses API endpoint; the local default is
  `http://localhost:8000/v1`.
- **Anthropic / OpenAI:** `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` select compatible
  Messages and Responses endpoints.
- **Gemini:** `GOOGLE_GEMINI_BASE_URL` selects the generateContent endpoint.
  `GOOGLE_API_KEY` takes precedence over `GEMINI_API_KEY` when both are set.

Endpoint and authentication settings configure the connection.
`extra_request_params` contains provider request fields, not connection credentials.
Connection settings and the selected account are captured at agent construction.
Credential refresh may renew access to that same account.

## Subscription authentication

`github-copilot` uses the first nonempty token in `COPILOT_AGENT_TOKEN`,
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, or an existing SDK login.
It is one provider even when its model catalog spans multiple model families.

`openai-chatgpt` uses a ChatGPT subscription through OAuth device-code login, with
cached tokens under `~/.amplifier/openai-chatgpt-oauth.json`. This is separate from
the `openai` API provider and its `OPENAI_API_KEY` credential.

Complete subscription login before creating an agent. Agent construction never opens
a browser or starts device-code login. Missing credentials fail with a remedy.

## Statelessness

Conversation replay comes from the local transcript. OpenAI Responses requests
disable storage and carry no previous-response dependency. Anthropic, Gemini, and
Responses providers bound optional reasoning from completed turns by age and size.
Required reasoning for an active tool round and tool signatures remain complete.
Visible conversation remains intact.
Retention opt-in belongs in host
[configuration](configuration.md).

Hosted account retention controls still apply. A request's storage flag does not
establish the provider's account-level retention or deletion policy.

## Conversation input

OpenAI, Azure OpenAI, vLLM, ChatGPT, and Chat Completions preserve supplied text
parts and system/developer roles in their native message arrays. Configured
instructions remain separate from imported conversation.

Anthropic and Gemini cannot represent system/developer messages at arbitrary
conversation positions with those native roles. The engine sends those messages
as JSON context containing their original role and text parts, in order. Ollama
uses the same representation for supplied system/developer messages and multipart
text. This preserves the input data, but does not reproduce native role semantics.
Local transcripts retain the original messages.

Copilot receives serialized conversation through its SDK prompt. Native role priority
and multipart fidelity are not established for that path. Applications that depend on
imported system/developer instruction priority should use a provider with native role
support above.
