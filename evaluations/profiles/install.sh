#!/bin/bash
# Installs amplifier-agent the way the README tells a user to, records what got installed, then idles.
set -u
APP="$HOME/app"
LOG="$APP/setup.log"
mkdir -p "$APP"
{
  echo "install started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  START=$(date +%s)
  mkdir -p "$APP" && cd "$APP" \
    && uv init --bare --python 3.13 . \
    && uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --branch v1
  STATUS=$?
  END=$(date +%s)
  echo "install exit $STATUS after $((END - START))s"
  if [ $STATUS -eq 0 ]; then
    uv run - <<'PY' > "$APP/installed.json"
import json, sys, platform, importlib.metadata as md
out = {"python": platform.python_version(), "packages": {}}
for name in ("amplifier-agent", "amplifier-agent-engine"):
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        out["packages"][name] = None
        continue
    direct = dist.read_text("direct_url.json")
    out["packages"][name] = {"version": dist.version, "direct_url": json.loads(direct) if direct else None}
json.dump(out, sys.stdout, indent=2)
PY
  fi
  echo "$STATUS" > "$APP/.setup-status"
} >> "$LOG" 2>&1
exec sleep infinity
