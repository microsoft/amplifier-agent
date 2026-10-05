#!/bin/bash
# Installs the TypeScript binding with the commands docs/install.md gives a user, then records what got installed and
# idles. The one argument picks the method:
#   npm    install the published package from npm into the application.
#   build  clone the release tag next to the application, build the runtime and library, then install the built
#          directory into the application.
set -u
MODE="${1:-}"
APP="$HOME/app"
CHECKOUT="$HOME/amplifier-agent"
LOG="$APP/setup.log"
mkdir -p "$APP"
{
  echo "install ($MODE) started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  START=$(date +%s)
  case "$MODE" in
    npm)
      cd "$APP" \
        && npm init -y > /dev/null \
        && npm install amplifier-agent-ts@0.21.0
      ;;
    build)
      cd "$HOME" \
        && git clone --depth 1 --single-branch --branch v0.21.0 https://github.com/microsoft/amplifier-agent.git \
        && cd amplifier-agent \
        && uv run --frozen --package amplifier-agent-engine --extra github-copilot --group build python scripts/build_runtime.py --output packages/typescript/runtime/linux-x64 \
        && cd packages/typescript \
        && pnpm install --frozen-lockfile \
        && pnpm build \
        && cd "$APP" \
        && npm init -y > /dev/null \
        && npm install --install-links ../amplifier-agent/packages/typescript
      ;;
    *)
      echo "usage: install.sh npm|build, got '$MODE'"
      false
      ;;
  esac
  STATUS=$?
  END=$(date +%s)
  echo "install exit $STATUS after $((END - START))s"
  if [ $STATUS -eq 0 ]; then
    if [ "$MODE" = build ]; then
      export COMMIT
      COMMIT=$(git -C "$CHECKOUT" rev-parse HEAD)
    fi
    MODE="$MODE" node --input-type=module - <<'JS' > "$APP/installed.json"
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
const name = "amplifier-agent-ts";
const root = `node_modules/${name}`;
const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const pkg = read(`${root}/package.json`);
const runtime = read(`${root}/runtime/linux-x64/manifest.json`);
let record;
if (process.env.MODE === "build") {
  record = {
    version: pkg.version,
    commit: process.env.COMMIT,
    source: "https://github.com/microsoft/amplifier-agent",
    copied: !lstatSync(root).isSymbolicLink(),
    runtime_variant: runtime.variant,
  };
} else {
  // npm keeps each tarball it fetched in its content-addressed cache, filed under the hex of its sha512 integrity.
  const { resolved, integrity } = read("package-lock.json").packages[root];
  const hex = Buffer.from(integrity.replace(/^sha512-/, ""), "base64").toString("hex");
  const tarball = join(homedir(), ".npm/_cacache/content-v2/sha512", hex.slice(0, 2), hex.slice(2, 4), hex.slice(4));
  record = {
    version: pkg.version,
    runtime_variant: runtime.variant,
    resolved,
    integrity,
    tarball_sha256: existsSync(tarball) ? createHash("sha256").update(readFileSync(tarball)).digest("hex") : null,
  };
}
const out = { surface: "typescript", node: process.versions.node, packages: { [name]: record } };
process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);
JS
  fi
  echo "$STATUS" > "$APP/.setup-status"
} >> "$LOG" 2>&1
exec sleep infinity
