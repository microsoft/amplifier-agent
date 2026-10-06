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
github-copilot     Copilot token or GitHub CLI login; needs the github-copilot extra
openai-chatgpt     ChatGPT OAuth login
chat-completions   optional CHAT_COMPLETIONS_API_KEY
gemini             GOOGLE_API_KEY or GEMINI_API_KEY
vllm               optional VLLM_API_KEY
```

Set credentials in the environment running your application or HTTP server, or per
agent in [`environment`](concepts/agents.md#environment), and select the provider and
model as described in [configuration](configuration.md#resolution).
The quickstarts use Anthropic with `claude-sonnet-5`; `claude-opus-5` is another
Anthropic selection. Other providers use their own model or deployment IDs.

## Credential status

[`list_providers`](concepts/models.md#discovering-providers-and-models) reports `found`
when a provider has what this list names, `not_required` where noted, and `missing`
otherwise.

```text
anthropic          ANTHROPIC_API_KEY
openai             OPENAI_API_KEY
azure-openai       AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT
ollama             not_required when the OLLAMA_HOST host is localhost (the default),
                   127.0.0.1, or ::1; otherwise OLLAMA_API_KEY
github-copilot     a token variable, or a token printed by `gh auth token`
openai-chatgpt     tokens in ~/.amplifier/openai-chatgpt-oauth.json
chat-completions   CHAT_COMPLETIONS_API_KEY, otherwise not_required
gemini             GOOGLE_API_KEY or GEMINI_API_KEY
vllm               VLLM_API_KEY, otherwise not_required
```

The `github-copilot` token variables are under
[subscription authentication](#subscription-authentication). `gh auth token` runs with
the same environment, so `GH_CONFIG_DIR` and `GH_HOST` apply. Its output is discarded,
and after 5 seconds the status is `missing`.

Azure identity credentials and a Copilot sign-in made outside the GitHub CLI are not
detected, so `azure-openai` and `github-copilot` can report `missing` and still work.
`list_models` therefore always asks `github-copilot`, and for `azure-openai` needs only
`AZURE_OPENAI_ENDPOINT`. For any other `missing` provider it fails `provider_failed`
without a request.

## Endpoints and accounts

- **Azure OpenAI:** set `AZURE_OPENAI_ENDPOINT` and use the deployment's model ID.
  An API key, managed identity, or `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and
  `AZURE_CLIENT_SECRET` authenticate the account. An API key takes precedence; without
  it, all three identity variables select client-secret authentication, otherwise
  managed identity is used. Azure CLI and workload-identity login are not consulted.
  Supply the resource endpoint without an `/openai/v1` suffix.
- **Ollama:** `OLLAMA_HOST` selects the server; the local default is
  `http://localhost:11434`. Authentication depends on that server.
  Its model listing reports `max_output_tokens` equal to `context_window`, and 8192
  for both when the server states no context length.
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
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, or a GitHub CLI login
(`gh auth login`).
It is one provider even when its model catalog spans multiple model families.
It needs the `github-copilot` extra ([install](install.md#python)); without it,
agent construction fails `engine_unavailable`.

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

## Reasoning effort

Which [reasoning effort](concepts/models.md#reasoning-effort) values a model takes comes
from the provider's own model knowledge:

```text
anthropic                             the model's capabilities; a model without thinking takes none
openai  azure-openai  openai-chatgpt  the OpenAI model table; a non-reasoning model takes none;
                                      gpt-5.5-pro and GPT-6 models take restricted sets
gemini                                minimal, low, medium, high where the model has that thinking
                                      level; Gemini 2.x takes none
github-copilot                        the live model list
chat-completions  ollama  vllm        unknowable
```

On `github-copilot`, a model missing from the live list, or listed without its values,
is unknowable. Where support is unknowable, only a named value is sent, and a provider
refusal fails the turn in `terminal`. `chat-completions` sends it as the top-level
`reasoning_effort` request field.

With the default `"medium"`, Anthropic models that take it run with adaptive thinking,
and Gemini 3.x models run at thinking level `medium`.

## Images

Image parts reach the model as native image input. Which messages may hold them:

```text
anthropic  openai  azure-openai  gemini      input content and every supplied user message
ollama  vllm  chat-completions
openai-chatgpt

github-copilot                                the newest user message of the turn only
```

Images in tool results reach the model only on `anthropic`, `openai`, and
`azure-openai`, with a model that accepts images. Every other provider fails such a
turn `image_unsupported` in `terminal`.

Before a turn holding an image starts, the selected model is checked against what the
provider reports about it: Anthropic, OpenAI, and Azure OpenAI models from a capability
table, others from the provider's model listing. A model reported without image input
fails `image_unsupported` at the method. When the provider reports nothing about the
model, a provider refusal saying the model does not accept images fails
`image_unsupported` in `terminal`. On `github-copilot`, an image in supplied history, an
earlier turn, or a tool result also fails `image_unsupported`.

Images are sent as given: the agent never resizes or re-encodes them, and sets no size
limit below what a provider accepts. Each provider's own limits apply, such as
Anthropic's 8000 pixels per side and 10 MB of base64 per image, or Gemini's 20 MB inline
request. The TypeScript binding carries one turn's input or one tool result in a message
of at most 512 MiB; a larger one fails `invalid_input`, or `tool_result_invalid` for a
tool result. An image a provider
refuses for any reason other than image support, such as its size, fails
`provider_failed` with the provider's message in `details.provider_message`.
