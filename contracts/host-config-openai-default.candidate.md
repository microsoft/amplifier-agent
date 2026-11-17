# CANDIDATE: OpenAI default model amendment

Proposed 2026-11-17. Governing contract: [host-config/1](host-config.v1.md),
sections 1 and 2. This proposal does not amend the frozen contract or authorize a
release. The contract owner decides ratification and version disposition.

## Reason

The OpenAI provider documents the exact `gpt-6.2-sol` successor and its
[model migration](https://developers.openai.com/api/docs/guides/latest-model.md).
Agent's implicit OpenAI model remains `gpt-6.1-sol`, so discovering or explicitly
selecting the successor does not update the model a fresh unconfigured caller
receives. Changing that choice is a breaking default change, not a model alias or
a repair to the caller's saved selection.

## Proposed section 2 addition

When `openai` is selected and no layer sets `model`, the engine default is
`gpt-6.2-sol`. This applies both to an explicitly configured `openai` provider and
to provider selection from available credentials. Other providers' defaults and
the credential preference order are unchanged.

Explicit `provider` and `model` values keep their existing precedence and are not
rewritten. Resuming a durable session retains its saved provider, model and
reasoning effort, rather than adopting the constructing agent's new defaults.
The reasoning default remains `medium`; no request effort is silently substituted.

## Breaking migration

To retain `gpt-6.1-sol` for new sessions, set one of:

```text
AgentOptions:          model="gpt-6.1-sol"
environment:           AMPLIFIER_AGENT_MODEL=gpt-6.1-sol
host configuration:    { "model": "gpt-6.1-sol" }
```

An explicitly selected `gpt-6-astra`, `gpt-6.1-sol` or other model is not migrated.
Existing durable sessions require no rewrite. The application must have access to
`gpt-6.2-sol` to use the proposed default; rejection remains a named failure, not a
fallback to another model.

## Proposed contract changelog entry

2026-11-17: Breaking CANDIDATE: the implicit `openai` model changes from
`gpt-6.1-sol` to `gpt-6.2-sol` for new sessions with no model configured (section 2).
Explicit choices and saved session selections are unchanged. Pin the previous
model explicitly to retain it for new sessions.

The existing prohibition on silently changing defaults remains in force. An
accepted amendment must update the governing body and its dated changelog entry
together, and retain the migration notice under Breaking in the root changelog.

## Acceptance evidence

Verification must cover both an absent provider with fixture OpenAI credentials
and an explicitly selected OpenAI provider at each configuration layer. The
public binding's fresh session, turn events and native request must name
`gpt-6.2-sol` with `medium` effort when no selection is supplied. Resumed legacy
sessions must retain their exact model, effort, conversation and encrypted
reasoning while a new session uses the proposed default.

The provider must independently establish support for the exact model. A local
candidate-provider override does not update the repository lock or prove normal
locked CI, accepted upstream integration, account entitlement or live inference.
