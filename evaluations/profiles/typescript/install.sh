#!/bin/bash
# Installs the TypeScript binding with exactly the commands docs/install.md gives a user: clone the release tag next to the
# application, build the runtime and library, then install the built directory into the application. Records what got
# installed, then idles.
set -u
APP="$HOME/app"
CHECKOUT="$HOME/amplifier-agent"
LOG="$APP/setup.log"
mkdir -p "$APP"
{
  echo "install started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  START=$(date +%s)
  cd "$HOME" \
    && git clone --depth 1 --single-branch --branch v0.20.0 https://github.com/microsoft/amplifier-agent.git \
    && cd amplifier-agent \
    && uv run --frozen --package amplifier-agent-engine --extra github-copilot --group build python scripts/build_runtime.py --output packages/typescript/runtime/linux-x64 \
    && cd packages/typescript \
    && pnpm install --frozen-lockfile \
    && pnpm build \
    && cd "$APP" \
    && npm init -y > /dev/null \
    && npm install --install-links ../amplifier-agent/packages/typescript
  STATUS=$?
  END=$(date +%s)
  echo "install exit $STATUS after $((END - START))s"
  if [ $STATUS -eq 0 ]; then
    COMMIT=$(git -C "$CHECKOUT" rev-parse HEAD) node --input-type=module - <<'JS' > "$APP/installed.json"
import { readFileSync, lstatSync } from "node:fs";
const root = "node_modules/amplifier-agent-ts";
const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const pkg = read(`${root}/package.json`);
const runtime = read(`${root}/runtime/linux-x64/manifest.json`);
const out = {
  surface: "typescript",
  node: process.versions.node,
  packages: {
    "amplifier-agent-ts": {
      version: pkg.version,
      commit: process.env.COMMIT,
      source: "https://github.com/microsoft/amplifier-agent",
      copied: !lstatSync(root).isSymbolicLink(),
      runtime_variant: runtime.variant,
    },
  },
};
process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);
JS
  fi
  echo "$STATUS" > "$APP/.setup-status"
} >> "$LOG" 2>&1
exec sleep infinity
