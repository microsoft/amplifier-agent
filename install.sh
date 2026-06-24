#!/usr/bin/env bash
# amplifier-agent installer
#
# Recommended usage:
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash
#
# Pin a specific version:
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --tag v0.9.0
#
# Review first (recommended for production):
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh -o install.sh
#   less install.sh
#   bash install.sh
#
# Flags: --tag <ref> | --no-prime | --yes | --help

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_SLUG="microsoft/amplifier-agent"
RELEASES_URL="https://api.github.com/repos/${REPO_SLUG}/releases/latest"
GIT_URL="https://github.com/${REPO_SLUG}"
PACKAGE_NAME="amplifier-agent"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    cat <<'USAGE'
amplifier-agent installer

Usage:
  curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash

Flags:
  --tag <ref>    Install a specific tag, branch, or commit (default: latest release)
  --no-prime     Skip the bundle cache priming step
  --yes          Skip the confirmation prompt (for CI/automation)
  --help         Print this help and exit

Examples:
  # Pin to a specific version
  curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --tag v0.9.0

  # Skip bundle priming (faster install, but first run will be 30-60s slower)
  curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --no-prime

  # Non-interactive (CI/automation)
  curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --yes
USAGE
}

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------

TAG=""
NO_PRIME=0
YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            if [[ "$#" -lt 2 ]] || [[ -z "$2" ]]; then
                printf 'error: --tag requires a non-empty argument\n' >&2
                exit 1
            fi
            TAG="$2"
            shift 2
            ;;
        --tag=*)
            TAG="${1#--tag=}"
            if [[ -z "${TAG}" ]]; then
                printf 'error: --tag= requires a non-empty value\n' >&2
                exit 1
            fi
            shift
            ;;
        --no-prime)
            NO_PRIME=1
            shift
            ;;
        --yes)
            YES=1
            shift
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            printf 'error: unknown flag: %s\n' "$1" >&2
            printf 'Run with --help for usage.\n' >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

# Require bash 3.2+ (macOS ships with 3.2; this script uses bash-specific syntax).
BASH_MAJOR="${BASH_VERSION%%.*}"
BASH_MINOR="${BASH_VERSION#*.}"
BASH_MINOR="${BASH_MINOR%%.*}"
if [[ "${BASH_MAJOR}" -lt 3 ]]; then
    printf 'error: bash 3.2 or later is required (found bash %s)\n' "${BASH_VERSION}" >&2
    exit 1
fi
if [[ "${BASH_MAJOR}" -eq 3 ]] && [[ "${BASH_MINOR}" -lt 2 ]]; then
    printf 'error: bash 3.2 or later is required (found bash %s)\n' "${BASH_VERSION}" >&2
    exit 1
fi

# Require curl.
if ! command -v curl > /dev/null 2>&1; then
    printf 'error: curl is required but not found on PATH.\n' >&2
    printf 'Install curl with your package manager and re-run this script.\n' >&2
    exit 1
fi

# Require uv — never silently bootstrap it; direct the user instead.
if ! command -v uv > /dev/null 2>&1; then
    printf 'error: uv is required but not found on PATH.\n\n' >&2
    printf 'Install uv first:\n' >&2
    printf '  curl -LsSf https://astral.sh/uv/install.sh | sh\n\n' >&2
    printf 'Then open a new shell (or run: source ~/.local/bin/env) and re-run this script.\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve the target tag
# ---------------------------------------------------------------------------

if [[ -z "${TAG}" ]]; then
    printf 'Resolving latest amplifier-agent release...\n'
    TAG="$(
        curl -fsSL "${RELEASES_URL}" \
            | grep -m1 '"tag_name":' \
            | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/'
    )"
    if [[ -z "${TAG}" ]]; then
        printf 'error: could not determine the latest release tag.\n' >&2
        printf 'Pass --tag <ref> explicitly, or browse releases at:\n' >&2
        printf '  https://github.com/%s/releases\n' "${REPO_SLUG}" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Confirmation gate
# ---------------------------------------------------------------------------

printf '\n'
if [[ "${NO_PRIME}" -eq 1 ]]; then
    printf 'Will install %s@%s via uv tool install (bundle priming skipped).\n' \
        "${PACKAGE_NAME}" "${TAG}"
else
    printf 'Will install %s@%s via uv tool install, then prime bundle cache.\n' \
        "${PACKAGE_NAME}" "${TAG}"
fi
printf '\n'

# Skip the prompt when stdin is not a TTY (piped) or --yes was passed.
if [[ "${YES}" -eq 0 ]] && [[ -t 0 ]]; then
    printf 'Press Enter to continue, Ctrl+C to abort... '
    read -r _confirm
    printf '\n'
fi

# ---------------------------------------------------------------------------
# Install via uv tool
# ---------------------------------------------------------------------------

printf 'Installing %s@%s...\n' "${PACKAGE_NAME}" "${TAG}"
if ! uv tool install --reinstall --force \
        --from "git+${GIT_URL}@${TAG}" \
        "${PACKAGE_NAME}"; then
    printf '\nerror: uv tool install failed.\n' >&2
    printf 'You can retry manually:\n' >&2
    printf '  uv tool install --reinstall --force "git+%s@%s"\n' "${GIT_URL}" "${TAG}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Prime bundle cache (optional but strongly recommended)
# ---------------------------------------------------------------------------

if [[ "${NO_PRIME}" -eq 0 ]]; then
    printf '\nPriming bundle cache (this prevents a 30-60s delay on first run)...\n'
    if ! amplifier-agent-post-install; then
        printf 'warning: bundle priming failed — continuing anyway.\n' >&2
        printf 'You can retry priming with: amplifier-agent prepare\n' >&2
    fi
fi

# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

TOOL_DIR="$(uv tool dir)"

printf '\n'
printf '=======================================================================\n'
printf '  amplifier-agent %s installed successfully!\n' "${TAG}"
printf '=======================================================================\n'
printf '\n'
printf '  Install location:  %s/%s\n' "${TOOL_DIR}" "${PACKAGE_NAME}"
printf '  Run:               amplifier-agent --help\n'
printf '  Update:            amplifier-agent update\n'
printf '  Uninstall:         uv tool uninstall %s\n' "${PACKAGE_NAME}"
printf '                     rm -rf ~/.amplifier-agent  # optional: removes cached data\n'
printf '\n'
