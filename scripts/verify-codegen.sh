#!/usr/bin/env bash
# verify-codegen.sh -- release-path gate: the checked-in protocol artifacts must
# match what the generator emits.
#
# CONVENTION
#   This follows the Kubernetes `hack/verify-codegen.sh` / Go
#   `go generate ./... && git diff --exit-code` pattern: regenerate into a
#   throwaway tree, byte-compare against the artifacts that are checked in, and
#   fail with the exact command needed to fix it. It is deliberately NOT a
#   pytest test -- `tests/` means "e2e contract tests" and this is a build gate.
#
# WHAT IT CHECKS
#   1. src/amplifier_agent_lib/protocol/spec.md byte-matches generator output
#   2. every src/amplifier_agent_lib/protocol/schemas/*.schema.json byte-matches
#   3. no orphan schema files are checked in (checked-in set == generated set)
#
# WHY IT MATTERS
#   Per design section 8 D1, the Python TypedDicts in
#   src/amplifier_agent_lib/protocol/ are the authoritative wire-spec source;
#   spec.md and schemas/ are GENERATED artifacts.
#
#   Those schemas are not documentation -- they are BUILD INPUT for the
#   published npm package. wrappers/typescript/scripts/gen-types.ts reads
#   protocol/schemas/*.schema.json and generates the TypeScript types, and it
#   runs automatically via the `prebuild` script in
#   wrappers/typescript/package.json. So `npm run build` bakes whatever is
#   checked in into dist/index.d.ts.
#
#   If the checked-in schemas drift from the TypedDicts, we ship an npm package
#   whose published types do not match the engine's actual wire format.
#   Consumers typecheck clean against a lie and fail at runtime. Nothing
#   downstream can catch this -- the drift is upstream of every TS type check.
#
# USAGE
#   ./scripts/verify-codegen.sh      # from repo root, no arguments
#
# EXIT CODES
#   0  all artifacts up to date
#   1  drift or orphans detected (actionable message printed)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROTOCOL_DIR="src/amplifier_agent_lib/protocol"
REGEN_CMD="uv run python -m amplifier_agent_lib.protocol._gen --output-dir ${PROTOCOL_DIR}"

GEN_DIR="$(mktemp -d)"
trap 'rm -rf "${GEN_DIR}"' EXIT

echo "verify-codegen: regenerating protocol artifacts into a temp tree"
echo "  repo root:  ${REPO_ROOT}"
echo "  source:     ${PROTOCOL_DIR}/ (TypedDicts)"
echo "  temp tree:  ${GEN_DIR}"
echo

if ! uv run python -m amplifier_agent_lib.protocol._gen --output-dir "${GEN_DIR}" >/dev/null; then
    echo "FAIL: the generator itself failed to run." >&2
    echo "      Reproduce with: ${REGEN_CMD}" >&2
    exit 1
fi

failed=0

# --- 1. spec.md ------------------------------------------------------------
if diff -q "${PROTOCOL_DIR}/spec.md" "${GEN_DIR}/spec.md" >/dev/null 2>&1; then
    echo "  OK    spec.md matches generator output"
else
    echo "  STALE spec.md differs from generator output:" >&2
    diff -u "${PROTOCOL_DIR}/spec.md" "${GEN_DIR}/spec.md" | head -40 >&2 || true
    failed=1
fi

# --- 2. schemas/*.schema.json ----------------------------------------------
checked_in="$(cd "${PROTOCOL_DIR}/schemas" && ls -1 ./*.schema.json 2>/dev/null | sed 's|^\./||' | sort)"
generated="$(cd "${GEN_DIR}/schemas" && ls -1 ./*.schema.json 2>/dev/null | sed 's|^\./||' | sort)"

schema_count=0
stale_schemas=0
while IFS= read -r name; do
    [ -n "${name}" ] || continue
    schema_count=$((schema_count + 1))
    if [ ! -f "${GEN_DIR}/schemas/${name}" ]; then
        # Orphan; reported by check 3 below.
        continue
    fi
    if ! diff -q "${PROTOCOL_DIR}/schemas/${name}" "${GEN_DIR}/schemas/${name}" >/dev/null 2>&1; then
        echo "  STALE schemas/${name} differs from generator output:" >&2
        diff -u "${PROTOCOL_DIR}/schemas/${name}" "${GEN_DIR}/schemas/${name}" | head -20 >&2 || true
        stale_schemas=$((stale_schemas + 1))
        failed=1
    fi
done <<<"${checked_in}"

if [ "${stale_schemas}" -eq 0 ]; then
    echo "  OK    ${schema_count} schemas/*.schema.json match generator output"
fi

# --- 3. orphan schemas -----------------------------------------------------
orphans="$(comm -23 <(echo "${checked_in}") <(echo "${generated}"))"
missing="$(comm -13 <(echo "${checked_in}") <(echo "${generated}"))"

if [ -n "${orphans}" ]; then
    echo "  ORPHAN extra schema files are checked in but no longer generated:" >&2
    echo "${orphans}" | sed 's|^|          schemas/|' >&2
    failed=1
else
    echo "  OK    no orphan schema files checked in"
fi

if [ -n "${missing}" ]; then
    echo "  MISSING generator emits schemas that are not checked in:" >&2
    echo "${missing}" | sed 's|^|          schemas/|' >&2
    failed=1
fi

echo
if [ "${failed}" -ne 0 ]; then
    cat >&2 <<EOF
FAIL: generated protocol artifacts are out of date.

  Run this to regenerate, then commit the result:

      ${REGEN_CMD}

  Do NOT hand-edit spec.md or schemas/*.schema.json -- they are generated from
  the TypedDicts in ${PROTOCOL_DIR}/. Drift here ships an npm package whose
  published TypeScript types do not match the engine's wire format (the schemas
  are the build input for wrappers/typescript/scripts/gen-types.ts).
EOF
    exit 1
fi

echo "verify-codegen: PASS -- spec.md + ${schema_count} schemas are up to date."
exit 0
