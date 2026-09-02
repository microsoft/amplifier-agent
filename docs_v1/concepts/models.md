# Models and the ceiling

One provider per agent. A second is refused.

`model` names the most expensive thing that will run on your behalf. It is a ceiling, not
a selection.

## Refining downward

A model may be named at three levels, each one a refinement within the agent's provider.

```
agent  <  session  <  turn
```

A refinement only lowers. Naming a session or turn model more expensive than the one above
it fails `selector_rejected`. No decision anywhere goes above the agent's ceiling.

Configure an agent for a cheap model and you never get a bill for an expensive one.

## Honored or refused

A model you name is used for primary work, or the turn fails `selector_rejected`. It is
never quietly swapped for something else.

Below the ceiling, choosing what actually runs is the agent's job. That work is
downward-only and invisible: there is no routing table to configure and no roles to map.

Every selection actually used, primary or otherwise, shows up in [usage](usage.md), and
the primary one is named in `turn_started.primary_actual`.

## Naming a model

Model ids are the provider's own. See [providers](../providers.md) for the id of each
provider and where its model names come from.
