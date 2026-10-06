# Models and selection

A session's selection is one provider, a model within it, and a reasoning effort. The
agent's `provider`, `model`, and `reasoning_effort` are the defaults for new sessions.

## Naming a selection

A model may be named at three levels within the session's provider, the most specific
winning:

```
agent  <  session  <  turn
```

A turn's model applies to that turn only. A model you name is honored even when it costs
more than the one it replaces. To change provider, use
[`session.set_model`](sessions.md#switching-models).

## Honored or refused

A model you name is used for primary work, or the turn fails `selector_rejected`. It is
never quietly swapped for something else.

If a provider reports a different model ID, it must be a verified alias of the
selection or the turn fails `selector_rejected`. Use an exact available model ID when
an account's rolling alias is not recognized.

Below the named selection, choosing what actually runs is the agent's job. That work is
downward-only and invisible: there is no routing table to configure and no roles to map.
Internal routing and delegated work never go above the model you named: another model
runs only when its price is verified lower within the provider. A turn whose
conversation holds an image never drops to a model that cannot accept images; when the
selected model cannot, the turn fails `image_unsupported`.

Every selection actually used, primary or otherwise, shows up in [usage](usage.md), and
the primary one is named in `turn_started.primary_actual`.

## Reasoning effort

`reasoning_effort` sets how much the model reasons before it answers, from least to
most:

```
none   minimal   low   medium   high   xhigh   max
```

Named nowhere, it is `"medium"`. It is named at the same levels as `model`, and a named
value is honored even when higher. Any value outside the set fails `invalid_input`.

```
model takes no reasoning effort      nothing is sent, and nothing fails
model does not take the named value  selector_rejected, never lowered or substituted
support cannot be known              only a named value is sent, never the default
```

Delegated work runs at the highest value its model takes, at or below the parent's.
`turn_started.reasoning_effort` names the value primary work was sent at, and is absent
when none was sent. See [providers](../providers.md#reasoning-effort) for which models
take which values.

## Naming a model

Model ids are the provider's own. See [providers](../providers.md) for selection and
credentials.

## Discovering providers and models

```
list_providers(options?)          -> [ProviderRecord]
list_models(provider, options?)   -> [ModelRecord]
```

Neither needs an agent or changes state. Both read what an agent's provider connection
reads: the process environment with `DiscoveryOptions.environment` on top, as for
[`environment`](agents.md#environment). `AMPLIFIER_AGENT_*` settings and the config file
play no part.

`list_providers` returns one record per accepted `provider` id, in a fixed order, with a
`display_name`, whether the provider's package extra is `installed`, a `credentials`
status of `found`, `missing`, or `not_required`, and the `credential_variables` it reads,
never their values. `found` means present, not valid. See
[credential status](../providers.md#credential-status) for each provider's rule.

`list_models` asks the provider on every call and returns its whole list: never cached,
never a fallback, never filtered by a selection. Each record has an `id`, a `display_name`,
and `context_window` and `max_output_tokens` when the provider reports them.
`reasoning_efforts` lists the [`reasoning_effort`](#reasoning-effort) values an agent
selecting that model accepts, in order: empty when the model takes none, absent when the
engine cannot know, as for Chat Completions, Ollama, and vLLM. Azure OpenAI returns an
empty list, because its models are your deployment names.

```
unknown provider, invalid options      invalid_input
package extra missing                  engine_unavailable
missing credentials or endpoint        provider_failed
rejected credentials, unreachable      provider_failed
no answer within 15 seconds            provider_failed, retryable
```

A `provider_failed` remedy names what to fix, such as a variable or an endpoint.
