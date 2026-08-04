# amplifier-agent
#
# This repo is developed spec-first. Three tiers, three purposes:
#
#   docs/spec/              the contract, in prose
#   tests/e2e/              proves the contract against the real CLI and HTTP
#                           server inside a DTU container
#   .amplifier/evaluation/  measures probabilistic agent behavior
#
# There is no unit test tier. Release and contract guards are standalone
# scripts (scripts/verify-*, wrappers/conformance/verify-parity.py), not tests,
# so that `tests/` unambiguously means "the e2e contract".
#
# Day-to-day:   make check     (seconds)
# Before a PR:  make verify    (~1 min)
# Contract:     make e2e       (needs a DTU)
# Behavior:     make eval      (needs a DTU + API keys)

.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help check verify e2e eval fmt \
        verify-codegen verify-wheel verify-parity verify-wrapper

help: ## Show available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- fast gate ---------------------------------------------------------------

check: ## Lint + types. Run this constantly while iterating.
	uv run ruff check src/ scripts/ tests/ wrappers/
	uv run ruff format --check src/ scripts/ tests/ wrappers/
	uv run pyright src/

fmt: ## Auto-fix formatting and lint
	uv run ruff format src/ scripts/ tests/ wrappers/
	uv run ruff check --fix src/ scripts/ tests/ wrappers/

# --- pre-PR gate -------------------------------------------------------------

verify: check verify-codegen verify-wheel verify-parity verify-wrapper ## Full gate: lint, types, and every contract/release guard
	@echo ""
	@echo "verify: ALL GATES PASSED"

verify-codegen: ## Checked-in protocol spec.md + schemas match the generator
	./scripts/verify-codegen.sh

verify-wheel: ## Built wheel ships the protocol spec, schemas, fixtures, and all bundle content
	./scripts/verify-wheel.py

verify-parity: ## Python and TypeScript runners agree on every conformance fixture
	@command -v pnpm >/dev/null 2>&1 || { \
		echo "verify-parity: pnpm is required but not installed." >&2; \
		echo "  This gate cross-validates the TypeScript conformance runner against" >&2; \
		echo "  the Python one, so it needs Node + pnpm." >&2; \
		echo "  Install: https://pnpm.io/installation  (e.g. 'npm i -g pnpm', or 'corepack enable pnpm')" >&2; \
		exit 1; \
	}
	@if [ ! -d wrappers/conformance/node_modules ]; then \
		echo "installing conformance deps..."; \
		cd wrappers/conformance && pnpm install --silent; \
	fi
	uv run python wrappers/conformance/verify-parity.py

verify-wrapper: ## Build and test the published TypeScript SDK
	@command -v bun >/dev/null 2>&1 || { \
		echo "verify-wrapper: bun is required but not installed." >&2; \
		echo "  This gate builds and tests the published TypeScript SDK (wrappers/typescript)." >&2; \
		echo "  Install: curl -fsSL https://bun.sh/install | bash" >&2; \
		exit 1; \
	}
	cd wrappers/typescript && bun install --no-save && bun run build && bun run test

# --- contract and behavior ---------------------------------------------------

e2e: ## Run the e2e DTU suites. Pass SUITE=<name> to scope: make e2e SUITE=skills
	@if [ -n "$(SUITE)" ]; then \
		uv run python tests/e2e/framework/cli.py run $(SUITE); \
	else \
		echo "Running ALL suites. This is slow -- scope with: make e2e SUITE=<name>"; \
		uv run python tests/e2e/framework/cli.py run; \
	fi

eval: ## Run the evaluation harness. Pass TASK=<id> to scope.
	@echo "See .amplifier/evaluation/README.md for options."
	@if [ -z "$(TASK)" ]; then \
		echo "Running EVERY agent x EVERY task. This is slow -- scope with: make eval TASK=<id>"; \
	fi
	cd .amplifier/evaluation && uv run python run.py run $(if $(TASK),--task $(TASK),)
