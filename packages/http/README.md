# Amplifier Agent HTTP service

An authenticated chat-completions projection of the Amplifier Agent Python binding.
Run the `amplifier-agent-face` service with host configuration supplied by the environment.

Follow the [installation guide](https://github.com/microsoft/amplifier-agent/blob/main/docs/install.md#http-face).
Set `ANTHROPIC_API_KEY` for the provider and `FACE_TOKEN` to a separate secret for
HTTP clients, then start the service:

```bash
AMPLIFIER_AGENT_PROVIDER=anthropic \
AMPLIFIER_AGENT_MODEL=claude-sonnet-5 \
AMPLIFIER_AGENT_APPROVALS=deny \
AMPLIFIER_AGENT_FACE_TOKEN="$FACE_TOKEN" \
uv run amplifier-agent-face
```

It binds to `127.0.0.1:9099` and serves `/v1/models`, `/v1/chat/completions`,
`/v1/providers`, and `/v1/providers/{provider}/models`.
Requests require `Authorization: Bearer <FACE_TOKEN>`. Every completion uses a new
ephemeral session; send the full conversation with each request.

`AMPLIFIER_AGENT_APPROVALS` is the static policy for every tool the service runs:
`deny` refuses them, `allow` runs them. With tools and no policy, the service refuses
to start. See the
[HTTP quickstart](https://github.com/microsoft/amplifier-agent/blob/main/docs/http/quickstart.md#configure-server-side-tools)
and [HTTP limits](https://github.com/microsoft/amplifier-agent/blob/main/docs/http/limits.md)
before exposing the service to other callers.
