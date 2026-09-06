# Amplifier Agent HTTP service

An authenticated chat-completions projection of the Amplifier Agent Python binding.
Run the `amplifier-agent-face` service with host configuration supplied by the environment.

Follow the [installation guide](https://github.com/microsoft/amplifier-agent/blob/v1/docs/install.md#http-face).
Set `ANTHROPIC_API_KEY` for the provider and `FACE_TOKEN` to a separate secret for
HTTP clients, then start the service:

```bash
AMPLIFIER_AGENT_PROVIDER=anthropic \
AMPLIFIER_AGENT_MODEL=claude-sonnet-5 \
AMPLIFIER_AGENT_FACE_TOKEN="$FACE_TOKEN" \
uv run amplifier-agent-face
```

It binds to `127.0.0.1:9099` and serves `/v1/models` and `/v1/chat/completions`.
Requests require `Authorization: Bearer <FACE_TOKEN>`. Every completion uses a new
ephemeral session; send the full conversation with each request.

The launcher has no tool approval policy. To authorize tools, use the Python host
example in the [HTTP quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs/http/quickstart.md).
See [HTTP limits](https://github.com/microsoft/amplifier-agent/blob/v1/docs/http/limits.md)
before exposing the service to other callers.
