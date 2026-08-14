"""Shared base class for the Amplifier-family deep-swe agents.

Everything here exists to satisfy three hard constraints of the deep-swe/pier
runner:

1. Work is scored as ``git diff <base_commit> HEAD`` in ``/app``. Uncommitted
   work scores ZERO, so we both instruct the agent to commit and run a
   belt-and-braces fallback commit ourselves.
2. ``environment.exec`` runs ``bash -c``, not a login shell. ``/etc/profile.d``
   is never sourced, so PATH must be set explicitly on every exec.
3. Instructions are long markdown blobs with quotes/backticks/newlines. They are
   uploaded as a file and referenced with ``$(cat ...)`` -- never interpolated
   into a shell string.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from abc import abstractmethod
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pier.agents.installed.base import BaseInstalledAgent
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import InstallStep
from pier.models.agent.network import NetworkAllowlist

from deepswe_agents.metrics import (
    find_events_files,
    find_opencode_db_files,
    normalize_metrics,
    normalize_opencode_metrics,
)

# Container path the task repo lives at. deep-swe grades a git diff of this dir.
WORKDIR = "/app"

# Explicit PATH for every exec. `bash -c` does not source /etc/profile.d, so the
# uv tool bin dir and the opencode bin dir must be named here or nothing resolves.
CONTAINER_PATH = (
    "/root/.local/bin:/root/.opencode/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)

# Where the task instruction is staged inside the container.
INSTRUCTION_PATH = "/tmp/pier-instruction.txt"

# amplifier-agent install ref, shared by every arm that installs it. Tracks the
# default branch so we benchmark the agent as it actually ships. Override for a
# reproducible run with:
#     --ak amplifier_agent_ref=git+https://github.com/microsoft/amplifier-agent@<sha>
DEFAULT_AMPLIFIER_AGENT_REF = "git+https://github.com/microsoft/amplifier-agent"

# Staging root for `--ak local_source=...` uploads.
LOCAL_SOURCE_ROOT = "/src"

# The instruction reaches the agent EXACTLY as deep-swe wrote it. Do not append
# scoring hints, commit reminders, or any other scaffolding here.
#
# deep-swe already tells the agent to commit: all 113 instruction.md files end
# with "IMPORTANT: Please work on this in a new branch from main and commit
# everything when you are done." A harness-added restatement on top of that made
# our numbers incomparable to the public leaderboard, whose scores were all
# produced by mini-swe-agent, which passes the instruction through untouched
# (pier/agents/installed/mini_swe_agent.py: `augmented_instruction = instruction`).
# No other pier adapter augments the prompt either.
#
# Uncommitted work is handled mechanically by the fallback commit below, which
# needs no cooperation from the model and does not change what the model sees.

# Marker echoed by the fallback commit so trial logs are greppable.
FALLBACK_MARKER = "PIER_AMPLIFIER_FALLBACK_COMMIT"

# Hard ceiling on the fallback commit itself so a wedged container cannot hang
# teardown forever.
FALLBACK_COMMIT_TIMEOUT_SEC = 120

# Hard ceiling on the teardown-time host collection of the session dirs, for
# the same reason and deliberately the same bound as the fallback
# commit: no single teardown step may add more than two minutes to a trial. The
# session tree is a handful of MB of jsonl, so this is generous by an order of
# magnitude -- it exists to bound a WEDGED container, not a slow one.
TEARDOWN_COLLECT_TIMEOUT_SEC = 120

# Directories/files never uploaded with `--ak local_source`. `.amplifier` is
# ~971MB in the amplifier-agent checkout and would dominate every trial.
LOCAL_SOURCE_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "build",
    "dist",
    ".DS_Store",
    ".amplifier",
    ".amplifier-agent",
    "target",
)

# Reusable install preludes. Subclasses opt in from `agent_install_steps()`.
UV_PRELUDE = [
    InstallStep(user="root", run="curl -LsSf https://astral.sh/uv/install.sh | sh"),
]

OPENCODE_PRELUDE = [
    InstallStep(
        user="root",
        run=(
            "for i in 1 2 3 4 5; do\n"
            "  curl -fsSL https://opencode.ai/install | VERSION=1.17.20 bash && break\n"
            '  echo "opencode install attempt $i failed; retrying in $((i*10))s..." >&2\n'
            "  sleep $((i*10))\n"
            "done\n"
            'export PATH="$HOME/.opencode/bin:$PATH"; opencode --version'
        ),
    ),
]


# Values accepted as "on" for boolean `--ak` kwargs. pier passes every `--ak`
# value as a STRING, so a bare `bool()` would make "false" truthy.
_TRUTHY = frozenset({"true", "1", "yes"})


def _as_bool(value: Any) -> bool:
    """Coerce an `--ak` value to bool. Anything not explicitly truthy is False."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


class AmplifierBaseAgent(BaseInstalledAgent):
    """Base for all Amplifier-family agents run against deep-swe tasks."""

    #: Which installable component `--ak local_source=<path>` replaces.
    #: ``None`` means the agent does not support local-source installs.
    LOCAL_SOURCE_PACKAGE: str | None = None

    #: Default model when the job does not pass one.
    DEFAULT_MODEL = "claude-sonnet-5"

    #: Binary whose ``--version`` identifies the build under benchmark.
    VERSION_BINARY: str = ""

    #: Basename of the captured stdout/stderr log.
    LOG_FILENAME = "agent.log"

    #: In-container absolute directories pulled out to the host after the run.
    #: Each lands at ``<trial>/agent/sessions/<basename>``. Used for the session
    #: state trees (events.jsonl) the token/cost metrics pass reads.
    SESSION_DIRS: tuple[str, ...] = ()

    #: Which usage store this agent writes, and therefore which parser
    #: ``populate_context_post_run`` must use.
    #:
    #: ``"events"``      Amplifier events.jsonl (amplifier-agent, foundation)
    #: ``"opencode_db"`` opencode's SQLite ``session`` table
    #:
    #: This is a per-agent fact, not a harness-wide one. Hardcoding the
    #: events.jsonl route meant every opencode trial searched for a file that
    #: agent never writes, found none, and reported all-``not_available`` --
    #: indistinguishable from a collection failure.
    METRICS_SOURCE: str = "events"

    def __init__(self, *args: Any, local_source: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Self-measured wall clock for the agent COMMAND itself, on a monotonic
        # clock so it cannot be skewed by a clock adjustment mid-trial. Set in
        # run(); read in populate_context_post_run().
        self._run_started_at: float | None = None
        self._run_ended_at: float | None = None
        self._local_source: Path | None = None
        if local_source:
            if not self.LOCAL_SOURCE_PACKAGE:
                raise ValueError(
                    f"Agent '{self.name()}' does not support --ak local_source "
                    "(LOCAL_SOURCE_PACKAGE is None)."
                )
            path = Path(local_source).expanduser().resolve()
            if not path.is_dir():
                # Never silently fall back to the git ref -- a run that quietly
                # tests the wrong code is worse than a failed run.
                raise ValueError(f"local_source path does not exist or is not a directory: {path}")
            self._local_source = path

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def agent_install_steps(self) -> list[InstallStep]:
        """Install steps baked into the Docker image at build time."""

    @abstractmethod
    def run_command(self, instruction_path: str) -> str:
        """Shell command that runs the agent on the instruction at *instruction_path*."""

    # ------------------------------------------------------------------
    # Model / env helpers
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        """Bare model id, accepting both ``anthropic/claude-sonnet-5`` and ``claude-sonnet-5``."""
        return self._parsed_model_name or self.DEFAULT_MODEL

    def agent_env(self) -> dict[str, str]:
        """Env for every exec: explicit PATH plus the Anthropic credentials."""
        base: dict[str, str | None] = {
            "PATH": CONTAINER_PATH,
            "HOME": "/root",
            "ANTHROPIC_API_KEY": self._get_env("ANTHROPIC_API_KEY"),
            "ANTHROPIC_BASE_URL": self._get_env("ANTHROPIC_BASE_URL"),
        }
        return self.build_process_env(base)

    def _wrap(self, command: str) -> str:
        """Re-export PATH inline: belt and braces on top of the ``env=`` dict."""
        return f'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:{CONTAINER_PATH}"; {command}'

    def get_version_command(self) -> str | None:
        """Record which build was benchmarked. ``_wrap`` supplies the PATH."""
        if not self.VERSION_BINARY:
            return None
        return self._wrap(f"{self.VERSION_BINARY} --version")

    def install_spec(self):
        from pier.models.agent.install import AgentInstallSpec

        return AgentInstallSpec(
            agent_name=self.name(),
            version=self._version,
            steps=self.agent_install_steps(),
        )

    def network_allowlist(self) -> NetworkAllowlist:
        domains = []
        base_url = self._get_env("ANTHROPIC_BASE_URL")
        if base_url:
            host = urlparse(base_url).hostname
            if host:
                domains.append(host)
        domains.append("api.anthropic.com")
        if self._local_source:
            # The local-source install runs at RUNTIME (behind the egress proxy)
            # and still resolves dependencies from PyPI/GitHub.
            domains += ["pypi.org", "files.pythonhosted.org", "github.com"]
        return NetworkAllowlist(domains=domains)

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Fill token/cost accounting from the collected session artifacts.

        Host-side and synchronous: nothing here may await or touch the
        container. A field is set only when metrics produced a real number --
        a bogus 0 would silently report a $0 run.
        """
        try:
            run_s = self._agent_run_s()
            if run_s is not None:
                # Merge: never clobber metadata another layer may have set.
                existing = getattr(context, "metadata", None)
                metadata = dict(existing) if isinstance(existing, dict) else {}
                metadata["agent_run_s"] = run_s
                context.metadata = metadata

            if self.METRICS_SOURCE == "opencode_db":
                sources = find_opencode_db_files(self.logs_dir)
                # workspace_dir must be THIS harness's workdir. The parser
                # filters the opencode `session` table by its absolute
                # `directory` column and has NO fallback: a mismatch matches no
                # session, so the run is reported as not_available with a note
                # naming the mismatch, rather than silently summing unrelated
                # sessions.
                record = normalize_opencode_metrics(
                    sources,
                    source=self.name(),
                    workspace_dir=WORKDIR,
                )
                source_label = "opencode.db"
            else:
                sources = find_events_files(self.logs_dir)
                record = normalize_metrics(sources, source=self.name())
                source_label = "events.jsonl"

            def number(key: str) -> float | None:
                value = record.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                return value

            input_tokens = number("input_tokens")
            output_tokens = number("output_tokens")
            cost_usd = number("cost_usd")
            # AgentContext has one cache field; metrics splits read from write.
            cache_read, cache_write = number("cache_read"), number("cache_write")
            cache_tokens = (
                None
                if cache_read is None and cache_write is None
                else (cache_read or 0) + (cache_write or 0)
            )

            if input_tokens is not None:
                context.n_input_tokens = int(input_tokens)
            if output_tokens is not None:
                context.n_output_tokens = int(output_tokens)
            if cache_tokens is not None:
                context.n_cache_tokens = int(cache_tokens)
            if cost_usd is not None:
                context.cost_usd = float(cost_usd)

            # Drop the inferred figure entirely. metrics.py's
            # `agent_wallclock_s` is the earliest-to-latest EVENT timestamp
            # span, measured on the CONTAINER clock, and only ever a floor on
            # LLM-active time -- never the command duration. `agent_run_s`
            # supersedes it, so the record carries exactly ONE duration.
            # Per-event timing is still available by reading events.jsonl.
            record.pop("agent_wallclock_s", None)
            # `agent_run_s` is this adapter's own `time.monotonic()` measurement
            # of the agent command; pier's wall-clock durations are not read.
            record["agent_run_s"] = run_s if run_s is not None else "not_available"

            # The full record carries what AgentContext has no slot for:
            # llm_responses, wallclock, de-duplication notes, source files.
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "metrics.json").write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )
            self.logger.info(
                f"metrics: {len(sources)} {source_label} file(s), "
                f"cost_usd={record.get('cost_usd')}, "
                f"llm_responses={record.get('llm_responses')}, "
                f"agent_run_s={record.get('agent_run_s')}"
            )
        except Exception as exc:  # noqa: BLE001 - metrics must never fail a good trial
            self.logger.warning(f"Could not compute token/cost metrics: {exc}")

    def _agent_run_s(self) -> float | None:
        """Seconds the agent command took, as measured by this adapter.

        None until `run()` has both started and finished (including its timeout
        path, where the `finally` block still stops the clock). Never negative:
        `time.monotonic()` cannot go backwards.
        """
        if self._run_started_at is None or self._run_ended_at is None:
            return None
        return round(self._run_ended_at - self._run_started_at, 3)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def setup(self, environment: BaseEnvironment) -> None:
        # super().setup() runs install_spec steps (when not preinstalled) and
        # version detection. Never skip it.
        await super().setup(environment)
        if self._local_source:
            await self._install_local_source(environment)
        # Always record what actually got installed. Without this the trial log
        # cannot tell you which build was benchmarked -- which matters most for
        # the git-ref path, where the ref is a moving branch.
        self.logger.info("[%s] benchmarking version: %s", self.name(), self._version or "<unknown>")

    async def _install_local_source(self, environment: BaseEnvironment) -> None:
        assert self._local_source is not None
        package = self.LOCAL_SOURCE_PACKAGE
        assert package is not None

        dest = f"{LOCAL_SOURCE_ROOT}/{package}"
        env = self.agent_env()

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / package
            shutil.copytree(self._local_source, staged, ignore=LOCAL_SOURCE_IGNORE)
            n_files = sum(1 for _ in staged.rglob("*") if _.is_file())
            self.logger.info(
                f"LOCAL SOURCE: {self._local_source} -> {dest} ({n_files} files staged)"
            )
            # upload_dir is `docker compose cp`; the destination must already exist.
            await self.exec_as_root(environment, self._wrap(f"mkdir -p {dest}"), env=env)
            await environment.upload_dir(staged, dest)

        await self.exec_as_root(
            environment,
            self._wrap(f"uv tool install --reinstall --force --from {dest} {package}"),
            env=env,
        )

        # Provenance: without this the trial log cannot distinguish a local-source
        # run from a git-ref run.
        result = await environment.exec(
            command=self._wrap(f"{package} --version"),
            env=environment.agent_process_env(env),
            user="root",
        )
        version = (result.stdout or result.stderr or "").strip()
        self.logger.info(f"INSTALLED VERSION: {version!r}")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # FIRST statement: nothing above it may be counted as agent time. The
        # value is parked on `self`, never on `context` -- pier only calls
        # populate_context_post_run() when `context.is_empty()`, so writing the
        # timing here would suppress ALL token/cost population.
        self._run_started_at = time.monotonic()

        env = self.agent_env()
        await self._upload_instruction(instruction, environment)

        log_path = f"{environment.env_paths.agent_dir}/{self.LOG_FILENAME}"
        command = self._wrap(
            f"cd {WORKDIR} && "
            f"{self.run_command(INSTRUCTION_PATH)} "
            f"2>&1 </dev/null | stdbuf -oL tee -a {log_path}"
        )

        try:
            await self.exec_as_agent(environment, command, env=env)
        finally:
            # FIRST statement: stop the clock before any teardown work, so the
            # fallback commit and artifact collection are not billed as agent
            # time. Runs on the timeout path too, for the same reason the
            # teardown steps below do.
            self._run_ended_at = time.monotonic()

            # Runs on the normal path, on exception, AND on CancelledError
            # (pier wraps run() in asyncio.wait_for; agent timeout cancels us).
            #
            # EVERY step below is _guarded, and that is load-bearing, not
            # ceremony. On the timeout path we are already being cancelled when
            # this block runs, so a bare `await` here raises CancelledError and
            # the step never happens -- and `except Exception` cannot save it,
            # because CancelledError is a BaseException. The timeout path is a
            # LIKELY outcome on a full-budget run, and it is exactly the trial
            # whose commit, log and token data we most want. See _await_guarded.
            await self._fallback_commit_guarded(environment, env)
            await self._collect_session_dirs_guarded(environment)

    async def _upload_instruction(self, instruction: str, environment: BaseEnvironment) -> None:
        """Stage the instruction as a file. NEVER interpolate it into a shell string."""
        text = self.render_instruction(instruction)
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "instruction.txt"
            local.write_text(text, encoding="utf-8")
            await environment.upload_file(local, INSTRUCTION_PATH)

    # ------------------------------------------------------------------
    # Fallback commit (cancellation-safe)
    # ------------------------------------------------------------------

    def _fallback_commit_command(self) -> str:
        return (
            f"cd {WORKDIR} && "
            "git config --global --add safe.directory /app; "
            "git config --global user.email 'agent@amplifier.local'; "
            "git config --global user.name 'Amplifier Agent'; "
            'if git add -A && git commit -m "agent work" >/dev/null 2>&1; then '
            f'echo "{FALLBACK_MARKER}: committed"; '
            f'else echo "{FALLBACK_MARKER}: nothing-to-commit"; fi'
        )

    async def _fallback_commit(self, environment: BaseEnvironment, env: dict[str, str]) -> None:
        result = await asyncio.wait_for(
            environment.exec(
                command=self._wrap(self._fallback_commit_command()),
                env=environment.agent_process_env(env),
                user="root",
            ),
            timeout=FALLBACK_COMMIT_TIMEOUT_SEC,
        )
        out = (result.stdout or "") + (result.stderr or "")
        if f"{FALLBACK_MARKER}: committed" in out:
            self.logger.warning(
                "Agent did not commit its own work; fallback commit captured the worktree. "
                f"({FALLBACK_MARKER}: committed)"
            )
        else:
            self.logger.info(f"{FALLBACK_MARKER}: nothing-to-commit")

    async def _await_guarded(self, coro: Coroutine[Any, Any, None], description: str) -> None:
        """Run *coro* to completion even while THIS coroutine is being cancelled.

        This is subtle and load-bearing. Do not simplify.

        pier runs ``asyncio.wait_for(agent.run(...), timeout=agent_timeout_sec)``.
        On timeout our task is cancelled, so a bare ``await`` inside the
        ``finally`` block can itself raise ``CancelledError`` and the teardown
        work would never happen. ``except Exception`` does NOT help here:
        ``CancelledError`` inherits from ``BaseException``.

        Mechanism:
          * The work runs in a SEPARATE task (``ensure_future``), so cancelling
            *us* does not cancel *it*.
          * We await it through ``asyncio.shield`` so an interrupted await leaves
            the work task running.
          * If our await is interrupted, we retry the shielded await (bounded).
            ``wait_for`` only cancels once, so one retry is normally enough; the
            bound just guarantees we cannot spin.
          * Callers' coroutines carry their own hard timeout, so a wedged
            container cannot hang teardown.

        Every teardown step in ``run()``'s ``finally`` block goes through here.
        The mechanism lives in one place so a fix cannot be applied to one copy
        and missed on the others.
        """
        task = asyncio.ensure_future(coro)
        for _ in range(3):
            try:
                await asyncio.shield(task)
                return
            except asyncio.CancelledError:
                if task.done():
                    # Work finished; swallow so the original exception (the
                    # agent timeout) is the one that propagates.
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - cleanup must never mask the real failure
                self.logger.warning(f"{description} failed: {exc}")
                return
        task.cancel()
        self.logger.warning(f"{description} could not be awaited to completion.")

    async def _fallback_commit_guarded(
        self, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        """Run the fallback commit even while this coroutine is being cancelled.

        This is subtle and load-bearing. Do not simplify.

        Without the shielding in ``_await_guarded``, an agent timeout would
        cancel us before the commit was issued -- producing a 0-byte
        ``model.patch`` and a total loss instead of partial credit. The commit
        coroutine carries its own hard timeout (``FALLBACK_COMMIT_TIMEOUT_SEC``)
        so a wedged container cannot hang teardown.
        """
        await self._await_guarded(self._fallback_commit(environment, env), "Fallback commit")

    async def _collect_session_dirs(self, environment: BaseEnvironment) -> None:
        """Pull the agent's in-container session trees onto the host.

        Each entry of ``SESSION_DIRS`` lands at
        ``<trial>/agent/sessions/<basename>``, which is inside the ``/logs/agent``
        bind mount, so ``populate_context_post_run`` can read the events.jsonl
        files host-side afterwards.

        Best effort by construction: every download is guarded and a failure is
        logged, never raised.

        The downloads share ONE overall deadline
        (``TEARDOWN_COLLECT_TIMEOUT_SEC``) rather than a per-directory timeout,
        so adding directories to ``SESSION_DIRS`` can never extend how long
        teardown may stall on a wedged container.
        """
        if not self.SESSION_DIRS:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TEARDOWN_COLLECT_TIMEOUT_SEC
        for source in self.SESSION_DIRS:
            remaining = deadline - loop.time()
            if remaining <= 0:
                self.logger.warning(
                    f"Session collection budget ({TEARDOWN_COLLECT_TIMEOUT_SEC}s) exhausted; "
                    f"skipped {source}"
                )
                continue
            target = self.logs_dir / "sessions" / Path(source).name
            try:
                target.mkdir(parents=True, exist_ok=True)
                await asyncio.wait_for(environment.download_dir(source, target), timeout=remaining)
            except Exception as exc:  # noqa: BLE001 - collection is best effort
                # repr, not str: a bare asyncio TimeoutError stringifies to ""
                # and would produce a warning that says nothing.
                self.logger.warning(f"Could not collect session dir {source}: {exc!r}")

    async def _collect_session_dirs_guarded(self, environment: BaseEnvironment) -> None:
        """Collect session dirs even while this coroutine is being cancelled.

        Same hazard as the fallback commit. This is NOT an edge case: on a
        full-budget run the agent hitting its timeout is a likely outcome, and
        that is exactly the trial whose trajectory and token/cost data we most
        want. A bare await here would be cancelled and lose it. See
        ``_await_guarded``.
        """
        await self._await_guarded(self._collect_session_dirs(environment), "Session collection")
