# Models and the ceiling

One provider per agent. A second is refused.

`model` selects primary work and sets the most expensive model that may run on your behalf.

## Refining downward

A model may be named at three levels, each one a refinement within the agent's provider.

```
agent  <  session  <  turn
```

A refinement only lowers. Naming a session or turn model more expensive than the one above
it fails `selector_rejected`. No decision anywhere goes above the agent's ceiling.

An identical model needs no price comparison. Different models require a verified
price ordering within the provider; an unknown or incomparable price is refused.
Custom deployments and subscription models may therefore allow only the same model.

## Honored or refused

A model you name is used for primary work, or the turn fails `selector_rejected`. It is
never quietly swapped for something else.

If a provider reports a different model ID, it must be a verified alias of the
selection or the turn fails `selector_rejected`. Use an exact available model ID when
an account's rolling alias is not recognized.

Below the ceiling, choosing what actually runs is the agent's job. That work is
downward-only and invisible: there is no routing table to configure and no roles to map.
A turn whose conversation holds an image never drops to a model that cannot accept
images; when the selected model cannot, the turn fails `image_unsupported`.

Every selection actually used, primary or otherwise, shows up in [usage](usage.md), and
the primary one is named in `turn_started.primary_actual`.

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
never a fallback, never filtered by a ceiling. Each record has an `id`, a `display_name`,
and `context_window` and `max_output_tokens` when the provider reports them. Azure OpenAI
returns an empty list, because its models are your deployment names.

```
unknown provider, invalid options      invalid_input
package extra missing                  engine_unavailable
missing credentials or endpoint        provider_failed
rejected credentials, unreachable      provider_failed
no answer within 15 seconds            provider_failed, retryable
```

A `provider_failed` remedy names what to fix, such as a variable or an endpoint.
