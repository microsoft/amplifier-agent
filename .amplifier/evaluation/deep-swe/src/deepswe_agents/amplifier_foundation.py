"""Full Amplifier foundation stack (`amplifier run`) with the anchors bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pier.environments.base import BaseEnvironment
from pier.models.agent.install import InstallStep
from pier.models.agent.network import NetworkAllowlist

from deepswe_agents.base import UV_PRELUDE, WORKDIR, AmplifierBaseAgent
from deepswe_agents.providers import (
    ANTHROPIC,
    API_KEY_VAR,
    FOUNDATION_PROVIDER_MODULE,
    FOUNDATION_PROVIDER_SOURCE,
    OPENAI,
    REASONING_EFFORT,
)

SETTINGS_PATH = "$HOME/.amplifier/settings.yaml"

# Env vars used to smuggle provider config into the heredoc without it appearing
# in argv. The key must never be logged; the base URL rides along for symmetry.
# Deliberately provider-neutral names: the SOURCE var differs by family
# (ANTHROPIC_API_KEY vs OPENAI_API_KEY) but the smuggling channel does not, so
# the settings template has one shape regardless of which model is under test.
API_KEY_ENV = "AMPLIFIER_BENCH_API_KEY"
BASE_URL_ENV = "AMPLIFIER_BENCH_BASE_URL"

# The bundle under benchmark. `anchors` is also the CLI's built-in default when
# no `bundle.active` is configured, but we pass it EXPLICITLY on the run command:
# `--bundle` has the highest precedence, so a stray `/app/.amplifier/settings.yaml`
# in a task repo cannot silently swap the stack out from under the benchmark.
ANCHORS_BUNDLE = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors/bundle.md"
)

#: The CLI package. Overridable so a run can pin an exact commit.
DEFAULT_AMPLIFIER_REF = "git+https://github.com/microsoft/amplifier"


class AmplifierFoundationAgent(AmplifierBaseAgent):
    LOCAL_SOURCE_PACKAGE = "amplifier"
    VERSION_BINARY = "amplifier"

    # Trajectory + metrics source. `amplifier run` writes one session tree per
    # project slug, and the slug is the cwd with separators replaced, so a
    # container running in /app yields `/root/.amplifier/projects/-app/`.
    #
    # We collect the whole `projects/` dir rather than just `-app`: the install
    # performs no LLM call, so `-app` is the only project that can exist, and
    # pulling the parent survives a slug surprise instead of silently yielding
    # zero metrics. `_log_session_provenance` reports what actually landed.
    #
    # Under `anchors` a single session writes events.jsonl TWICE (hooks-logging
    # at the session root, hook-context-intelligence under
    # `context-intelligence/`). Both are collected on purpose; `parse_events`
    # de-duplicates by the provider's response id, so cost is not doubled.
    SESSION_DIRS = ("/root/.amplifier/projects",)

    def __init__(
        self,
        *args: Any,
        amplifier_ref: str = DEFAULT_AMPLIFIER_REF,
        anchors_ref: str = ANCHORS_BUNDLE,
        **kwargs: Any,
    ):
        """Both refs are injectable so a run can pin exact commits.

        Reachable as `--ak amplifier_ref=...` / `--ak anchors_ref=...`. Left at
        the defaults this arm tracks the moving branch, which is fine for a
        one-off but silently invalidates a long multi-task run: upstream can
        move between the first task and the last, so different tasks would be
        graded against different agent code. `run.py --pin` resolves both to
        commit SHAs once per run and records them.
        """
        super().__init__(*args, **kwargs)
        self._amplifier_ref = amplifier_ref
        self._anchors_ref = anchors_ref

    @staticmethod
    def name() -> str:
        return "amplifier-foundation"

    def agent_install_steps(self) -> list[InstallStep]:
        return [
            *UV_PRELUDE,
            InstallStep(
                user="root",
                run=(
                    'export PATH="$HOME/.local/bin:$PATH"\n'
                    f"uv tool install {self._amplifier_ref}\n"
                    "amplifier --version"
                ),
            ),
            InstallStep(
                user="root",
                # Pre-resolve the anchors composition at BUILD time so the timed
                # run does not include ~30 module git clones. `bundle show`
                # downloads every module it resolves and needs no API key, which
                # is why it (not a warm-up `amplifier run`) is used here: a
                # warm-up would need the key baked into a layer AND would write a
                # second session that pollutes the token/cost numbers.
                #
                # Best effort: a resolution failure here must not fail the build,
                # since the run can still resolve behind the egress proxy.
                run=(
                    'export PATH="$HOME/.local/bin:$PATH"\n'
                    f"amplifier bundle show '{self._anchors_ref}' >/dev/null 2>&1 || "
                    'echo "anchors pre-warm failed; modules will resolve at run time" >&2'
                ),
            ),
        ]

    def network_allowlist(self) -> NetworkAllowlist:
        # The provider module is declared by source in settings.yaml, which is
        # only written at runtime, so amplifier resolves it from git on first
        # run -- behind the egress proxy. Without these, the run cannot start.
        base = super().network_allowlist()
        return NetworkAllowlist(
            domains=[*base.domains, "github.com", "pypi.org", "files.pythonhosted.org"]
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        await self._write_settings(environment)

    def _settings_yaml(self) -> str:
        """Render the settings.yaml body for the family under test.

        Pure and host-side so the exact bytes written into the container can be
        inspected without launching one.

        The provider module is chosen by family; the base_url key is present in
        BOTH branches and must stay that way. provider-anthropic reads base_url
        from CONFIG ONLY, and provider-openai likewise reads it only from
        config (its AsyncOpenAI env fallback is an SDK accident, not a
        contract). Omitting this key was silently sending this arm to the
        vendor's public endpoint while the other arms honored the proxy, i.e.
        benchmarking a different endpoint.

        The Anthropic branch keeps `enable_1m_context` / `enable_prompt_caching`;
        the OpenAI branch drops them. provider-openai does not consume either --
        they would draw an unknown-key warning and then sit inert, implying a
        caching posture the run does not actually have.

        The OpenAI branch adds `reasoning_effort` (see `providers.REASONING_EFFORT`).
        It is OpenAI-only: the Anthropic side reasons on a token budget, not an
        effort level, and pinning that is a separate change.
        """
        family = self.provider_family
        settings = (
            "config:\n"
            "  providers:\n"
            f"    - module: {FOUNDATION_PROVIDER_MODULE[family]}\n"
            f"      source: {FOUNDATION_PROVIDER_SOURCE[family]}\n"
            "      config:\n"
            f"        api_key: ${{{API_KEY_ENV}}}\n"
            f"        base_url: ${{{BASE_URL_ENV}}}\n"
            f"        default_model: {self.model}\n"
        )
        if family == ANTHROPIC:
            settings += "        enable_1m_context: 'true'\n"
            settings += "        enable_prompt_caching: 'true'\n"
        elif family == OPENAI:
            # Unquoted on purpose: YAML reads a bare `high` as the string
            # "high", which is exactly what provider-openai validates against
            # at mount time. Pinned so this arm runs at the same effort as the
            # other two rather than at whatever the model defaults to.
            settings += f"        reasoning_effort: {REASONING_EFFORT}\n"
        settings += "        priority: 1\n"
        # NOTE: no routing.matrix key -- it re-introduces role-based model fan-out,
        # which would destroy the single-model-under-test premise of the benchmark.
        # NOTE: no `bundle:` key -- the run command pins the bundle explicitly.
        return settings

    async def _write_settings(self, environment: BaseEnvironment) -> None:
        """Write settings.yaml at RUNTIME, never at install time.

        Install steps become Docker layers: baking the API key there would put
        the secret in the image and make the install fingerprint key-dependent
        (defeating layer caching across runs).
        """
        family = self.provider_family
        env = self.agent_env()
        env[API_KEY_ENV] = self._get_env(API_KEY_VAR[family]) or ""
        base_url = self.base_url()
        env[BASE_URL_ENV] = base_url

        # Unquoted heredoc so ${API_KEY_ENV} expands in the container -- the key
        # never appears in argv or in the logged command.
        settings = self._settings_yaml()
        command = (
            'mkdir -p "$HOME/.amplifier" && '
            f'cat > "{SETTINGS_PATH}" <<PIER_SETTINGS_EOF\n{settings}PIER_SETTINGS_EOF'
        )
        await self.exec_as_root(environment, self._wrap(command), env=env)
        self.logger.info(
            f"Wrote amplifier settings.yaml at runtime "
            f"({FOUNDATION_PROVIDER_MODULE[family]}, base_url={base_url})."
        )

    def run_command(self, instruction_path: str) -> str:
        """Single-shot, non-interactive, on a pinned bundle.

        `--mode single` is already the CLI default, and is stated anyway so a
        future default flip cannot turn the benchmark into an interactive session
        that hangs until the timeout. `--output-format json` puts a machine-
        readable `{"status": ...}` envelope at the end of agent.log.

        No `--model` flag: it requires `--provider` alongside it, and the model is
        already pinned by `default_model` in settings.yaml.

        The `--` before the prompt is load-bearing: without it, any instruction
        whose text begins with `-` is parsed as a flag and the CLI exits with a
        usage error before the agent ever runs.
        """
        return (
            f"amplifier run --bundle '{self._anchors_ref}' "
            f"--mode single --output-format json "
            f'-- "$(cat {instruction_path})"'
        )

    def populate_context_post_run(self, context) -> None:  # type: ignore[no-untyped-def]
        super().populate_context_post_run(context)
        self._log_session_provenance()

    def _log_session_provenance(self) -> None:
        """Report which project/bundle actually produced the collected sessions.

        This is the cheap, direct proof that the trial exercised foundation with
        the anchors bundle in the task workspace -- rather than, say, a stray
        project slug whose events would be counted as if they were the task's.
        Never raises: provenance logging must not fail a good trial.
        """
        try:
            projects = self.logs_dir / "sessions" / "projects"
            if not projects.is_dir():
                self.logger.warning(
                    "No session projects dir collected; token/cost metrics will be not_available."
                )
                return
            slugs = sorted(p.name for p in projects.iterdir() if p.is_dir())
            expected = "-" + WORKDIR.strip("/").replace("/", "-")
            unexpected = [s for s in slugs if s != expected]
            self.logger.info(f"session projects collected: {slugs} (expected {expected!r})")
            if unexpected:
                self.logger.warning(
                    f"Session projects OTHER than {expected!r} were collected: "
                    f"{unexpected}. Their events are included in the token/cost "
                    f"totals and may not belong to this task."
                )
            bundles = {
                str(meta.get("bundle"))
                for path in projects.rglob("metadata.json")
                if isinstance(meta := self._read_json(path), dict) and meta.get("bundle")
            }
            if bundles:
                self.logger.info(f"bundle(s) recorded in session metadata: {sorted(bundles)}")
                if not any("anchors" in b for b in bundles):
                    self.logger.warning(
                        f"No collected session recorded the anchors bundle: {sorted(bundles)}"
                    )
        except Exception as exc:  # noqa: BLE001 - provenance must never fail a trial
            self.logger.warning(f"Could not log session provenance: {exc!r}")

    @staticmethod
    def _read_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
