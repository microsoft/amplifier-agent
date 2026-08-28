# Providers

A provider declares its own credential fields and default model, and that
declaration is what the agent uses. There is no table inside the agent to fall
out of date, and discovery tells you what is actually available rather than what
was true when the agent shipped.

This page covers the mechanism. For the credentials and models of a specific
provider, see [Providers](../06-providers/index.md).

## Selecting one

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str | None = None
    model_roles: Mapping[str, str] | None = None
    credentials: Mapping[str, str] | None = None
    options: Mapping[str, object] | None = None
    max_retries: int = 3
```

`name` refers to a registered provider, and `create_agent` raises
`config/unknown_provider` when nothing is registered under it. `model` takes the
provider's `default_model` when omitted.

`options` is an opaque pass-through. The agent does not interpret, validate, or
rewrite it, which is what lets a provider gain a setting without any change to
the agent or to this interface.

## Descriptors

```python
@dataclass(frozen=True)
class CredentialField:
    name: str
    display_name: str
    env_var: str | None = None
    secret: bool = True
    required: bool = True
    default: str | None = None


@dataclass(frozen=True)
class ProviderDescriptor:
    name: str
    display_name: str
    credentials: list[CredentialField]
    default_model: str | None = None


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    display_name: str
    context_window: int
    max_output_tokens: int
```

Because the provider supplies these at runtime, they cannot drift from what the
provider actually does. There is no table inside the agent to fall out of date.

## Credential resolution

Resolution runs per `CredentialField`, first match wins:

```
1. ProviderConfig.credentials[field.name]
2. the environment variable named by field.env_var
3. the credential store
4. field.default
```

Environment before store follows the `gh` and `aws` convention, so a one-off
export can point a single run at a different key without disturbing what you have
saved.

A required field that resolves to nothing fails `create_agent` with
`config/missing_credentials`. The error names each unresolved field and the
environment variable that would satisfy it, so the message tells you what to
export.

Three guarantees worth relying on:

- **Credentials resolve once per `create_agent` call** and are not cached across
  calls. A rotated key takes effect on the next call, with no process restart.
- **A resolved credential never appears** in an event, an error message,
  `AgentError.details`, or a descriptor.
- **`options` cannot override a credential.** Resolved credentials are reapplied
  after the options overlay, so a stray key in `options` cannot redirect
  authentication.

## Model roles

`model_roles` maps a role name to a model. The agent uses roles for its own
internal work, and pointing one at a cheaper model moves that work without
touching the model doing your main task.

```python
ProviderConfig(
    name="anthropic",
    model="claude-opus-5",
    model_roles={"fast": "claude-sonnet-5"},
)
```

Unmapped roles fall back to `model`.

## Retries

The agent retries transient provider failures itself, with exponential backoff
and jitter, up to `max_retries` attempts per request. Timeouts, connection
failures, `429`, and `5xx` are transient. A rate limit carrying a `Retry-After`
waits that long rather than guessing.

This matters for reading the error codes, because it changes what they mean:

```
provider/unavailable    the provider stayed unreachable across every attempt
provider/rate_limited   the provider stayed rate-limited across every attempt
provider/error          the provider rejected the request; not retried
```

`provider/unavailable` and `provider/rate_limited` are still marked `retryable`,
because waiting longer than the agent is willing to wait can genuinely clear
them. But they no longer mean "try once more" the way they would from a raw HTTP
client. The agent already did. Back off substantially further than you would
otherwise, and treat a second occurrence as a real outage rather than noise.

Retries happen inside a turn and are invisible in the event stream. The turn
takes longer; nothing else changes. They do not consume turn iterations, and a
retried request is billed by the provider only for the attempts it served.

`max_retries=0` disables the behavior and surfaces the first failure, which is
what you want when your own caller is already retrying and you would otherwise
multiply the two.

## Discovery

```python
async def list_providers() -> list[ProviderStatus]: ...
async def list_models(provider: str) -> list[ModelDescriptor]: ...


@dataclass(frozen=True)
class ProviderStatus:
    descriptor: ProviderDescriptor
    available: bool
    credential_source: Literal["config", "environment", "store", "default", "unresolved"]
    unresolved: list[str]
```

Both are module-level functions and neither needs an `Agent`, because you call
them to decide what to put in an `AgentConfig` in the first place.

```python
for status in await list_providers():
    if status.available:
        print(status.descriptor.name, "via", status.credential_source)
    else:
        print(status.descriptor.name, "missing", status.unresolved)
```

`available` is `True` when every required credential field resolves.
`list_models` may contact the provider, so it can fail with
`provider/unavailable` or `config/missing_credentials`.

## Errors

- **`config/unknown_provider`** nothing is registered under that name.
- **`config/unknown_model`** the model is not available for that provider.
- **`config/missing_credentials`** a required credential did not resolve.
- **`provider/error`** the provider rejected the request.
- **`provider/unavailable`** the provider could not be reached. Retryable.
- **`provider/rate_limited`** the provider rate-limited the request. Retryable.

See [Errors](errors.md) for the full registry.
