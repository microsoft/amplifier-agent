# Providers

One provider per agent. Its id is what you set as `provider`, in code or in
[configuration](configuration.md).

```
anthropic          ANTHROPIC_API_KEY
openai             OPENAI_API_KEY
azure-openai       AZURE_OPENAI_API_KEY
gemini             GOOGLE_API_KEY
github-copilot     GITHUB_TOKEN
openai-chatgpt     device-code sign-in, no key
ollama             none by default
vllm               none by default
chat-completions   set in extra_request_params
```

Model ids are the provider's own. Name one your account can actually reach, because a
named model is honored or the turn fails `selector_rejected`. See
[models](concepts/models.md).

## Endpoints and accounts

`azure-openai` needs the endpoint and deployment your resource exposes, alongside the
key.

`ollama` talks to `http://localhost:11434` unless told otherwise, and needs no
credential for a local install.

`vllm` talks to whatever server you point it at, and needs a credential only if you put
one in front of it.

`chat-completions` is the generic OpenAI-compatible endpoint, for anything that speaks
that shape and is not listed above. Its base URL and credential go in
`extra_request_params`.

## Aggregators

`github-copilot` serves models from more than one family. It is still one provider, and
one value. Its model ids are namespaced, so a Copilot-served model reads
`github-copilot/<id>` and never collides with the same id served natively.

## Sign-in rather than a key

`openai-chatgpt` authenticates with a device-code flow and manages its own tokens
afterward. There is no key to set.

## Statelessness

Every provider is asked to keep nothing, and none of them holds anything a session
depends on. This is not per-provider behavior you have to check for; it is the posture,
and turning it off for one provider is an explicit act. See
[configuration](configuration.md).
