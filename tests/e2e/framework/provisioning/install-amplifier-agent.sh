#!/usr/bin/env bash
# Install/configure amplifier-agent inside the DTU.
#
# MODULARITY CONTRACT: everything about HOW amplifier-agent is installed and
# configured lives in THIS file (and host-config.json). Change the install story
# — package name, extra modules, bundle wiring, editable vs wheel — by editing
# here; the profile skeleton and dtu.py do not change.
#
# The `uv tool install git+...amplifier-agent` below goes through the DTU's
# url_rewrites proxy, which redirects github.com/microsoft/amplifier-agent to the
# in-DTU Gitea mirror of the local working tree, so the install resolves to that
# local code instead of upstream GitHub.
set -euo pipefail

# OS prerequisites for the git+ install path. The stock base image is bare, so
# git (used by `uv tool install --from git+...`) and TLS certs must be present.
# A pre-baked base image would carry these already; until then we install per launch.
if ! command -v git >/dev/null 2>&1; then
  echo "[install] git not found; installing OS prerequisites"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git ca-certificates curl
fi

# uv drives the install; bootstrap it if the base image lacks it.
if ! command -v uv >/dev/null 2>&1; then
  echo "[install] uv not found; bootstrapping"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[install] installing amplifier-agent from git (via Gitea proxy)"
uv tool install --reinstall --force \
  --from git+https://github.com/microsoft/amplifier-agent amplifier-agent

# Post-install hook (module wiring, anchor bundle prep). Non-fatal if absent.
# A clean install here yields a working provider module for both `run` and the HTTP
# `serve` model enumeration. (Note: an in-place `uv tool install --reinstall` on an
# already-provisioned container wipes the provider module and breaks `serve`, which is
# why the harness provisions a fresh container per run rather than updating in place.)
amplifier-agent-post-install || true

echo "[install] installed:"
amplifier-agent --version
