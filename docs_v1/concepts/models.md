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

Configure an agent for a cheap model and you never get a bill for an expensive one.

## Honored or refused

A model you name is used for primary work, or the turn fails `selector_rejected`. It is
never quietly swapped for something else.

If a provider reports a different model ID, it must be a verified alias of the
selection or the turn fails `selector_rejected`. Use an exact available model ID when
an account's rolling alias is not recognized.

Below the ceiling, choosing what actually runs is the agent's job. That work is
downward-only and invisible: there is no routing table to configure and no roles to map.

Every selection actually used, primary or otherwise, shows up in [usage](usage.md), and
the primary one is named in `turn_started.primary_actual`.

## Naming a model

Model ids are the provider's own. See [providers](../providers.md) for selection and
credentials.
