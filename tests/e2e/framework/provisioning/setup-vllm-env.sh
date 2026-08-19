#!/usr/bin/env bash
#
# Export the vllm suite's endpoint into the container environment.
#
# Why this is a provisioning script rather than a `passthrough` entry: passthrough
# copies the host value verbatim (engine.py `_write_env`), but the vLLM server the
# suite targets runs on the HOST. Inside the container `localhost` is the container
# itself, so a verbatim `http://localhost:8007/v1` would resolve to the container's
# own loopback and never reach the server. `--var` values, by contrast, are rewritten
# at launch (engine.py `_rewrite_localhost`) so `localhost` / `127.0.0.1` become the
# bridge gateway IP -- exactly the trick GITEA_URL already relies on. So the endpoint
# travels as a var, and this script lands it in the environment.
#
# Args (all positional, all may be empty):
#   $1  VLLM_BASE_URL   already localhost-rewritten by DTU
#   $2  VLLM_MODEL      model id to pin; empty means "let the suite discover it"
#   $3  VLLM_API_KEY    optional; a local vLLM server usually needs none
#
# An empty value is OMITTED rather than exported as an empty string. That distinction
# is load-bearing, not stylistic. The provider module resolves its key as:
#
#     api_key = config.get("api_key") or os.environ.get("VLLM_API_KEY", "EMPTY")
#
# and that "EMPTY" default applies only when the variable is ABSENT. Exporting
# VLLM_API_KEY="" makes it present-but-empty, which defeats the default and hands the
# OpenAI SDK an empty key -- surfacing as "Missing credentials ... set OPENAI_API_KEY",
# a message that points nowhere near the real cause. Omitting the export instead is
# what lets an unauthenticated local vLLM server work at all.
#
# `[ -n "$VAR" ]` cannot tell absent from empty, so the suite's guard reads the same
# either way and nothing is lost by omitting.

set -euo pipefail

BASE_URL="${1:-}"
MODEL="${2:-}"
API_KEY="${3:-}"

DEST="/etc/profile.d/zz-vllm.sh"

# `zz-` prefix is load-bearing. /etc/profile.d is sourced in alphabetical order and
# DTU writes no_proxy in dtu-env.sh; sorting after it is what lets the no_proxy
# amendment below survive rather than be overwritten.
#
# Written with `if` blocks rather than `[ -n "$X" ] && printf ...` on purpose: under
# `set -e` a trailing short-circuit that evaluates false is the script's exit status,
# so an absent API key would make provisioning fail.
printf '#!/bin/bash\n' >"$DEST"
if [ -n "$BASE_URL" ]; then
    printf 'export VLLM_BASE_URL=%q\n' "$BASE_URL" >>"$DEST"
fi
if [ -n "$MODEL" ]; then
    printf 'export VLLM_MODEL=%q\n' "$MODEL" >>"$DEST"
fi
if [ -n "$API_KEY" ]; then
    printf 'export VLLM_API_KEY=%q\n' "$API_KEY" >>"$DEST"
fi

# Exempt the vLLM host from the mitmproxy interception proxy.
#
# DTU sets no_proxy="localhost,127.0.0.1,::1" when url_rewrites is active. The vLLM
# endpoint is the bridge gateway IP, which is NOT in that list, so without this the
# traffic would route through mitmproxy -- and per DTU's own comment in _write_env,
# mitmproxy "buffers whole response bodies and destroys SSE / token streaming". The
# agent streams, so this is not a theoretical concern.
#
# Only the host is added, never the port: no_proxy matches on host.
if [ -n "$BASE_URL" ]; then
    VLLM_HOST="$(printf '%s' "$BASE_URL" | sed -E 's#^[a-zA-Z]+://##; s#[:/].*$##')"
    if [ -n "$VLLM_HOST" ]; then
        {
            printf 'export no_proxy="${no_proxy:+${no_proxy},}%s"\n' "$VLLM_HOST"
            printf 'export NO_PROXY="${NO_PROXY:+${NO_PROXY},}%s"\n' "$VLLM_HOST"
        } >>"$DEST"
    fi
fi

chmod +x "$DEST"

# Report without echoing the API key.
echo "[setup-vllm-env] VLLM_BASE_URL=${BASE_URL:-<unset>} VLLM_MODEL=${MODEL:-<unset>} api_key=$([ -n "$API_KEY" ] && echo present || echo absent)"
