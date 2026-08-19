"""Admin commands: ``auth`` subgroup for persistent provider credentials.

Manages a credentials file at ``~/.amplifier-agent/credentials.json``
(mode ``0o600``) so users can set their provider API keys once and have
every amplifier-agent invocation -- CLI, HTTP server, or any wrapper
like ``amplifier-opencode`` -- pick them up automatically.

The credential resolution chain (see
:func:`amplifier_agent_cli.provider_sources._resolve_env_credential`):

    1. Shell environment variable (highest priority -- enables ad-hoc
       overrides, CI runners, ephemeral sessions, and per-shell isolation
       without disturbing the persisted file)
    2. ``credentials.json`` entry (this command's surface)
    3. Empty -- caller decides whether to fail loudly or skip the provider

File schema (v1)::

    {
      "version": 1,
      "providers": {
        "anthropic": {"api_key": "sk-ant-..."},
        "openai":    {"api_key": "sk-..."},
        "azure-openai": {"api_key": "...", "endpoint": "https://..."}
      }
    }

The wrapped wire-shape avoids surprising mutations: any provider key
unknown to the v1 schema still round-trips through reads/writes (we
preserve it verbatim) so adding new providers in future versions does
not silently drop user configuration.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Final

import click

from amplifier_agent_cli.provider_sources import (
    KNOWN_PROVIDERS,
    CredentialResolution,
    resolve_credential_detailed,
)
from amplifier_agent_lib.persistence import amplifier_agent_home

logger = logging.getLogger(__name__)

CREDENTIALS_VERSION = 1
CREDENTIALS_FILENAME = "credentials.json"
CREDENTIALS_FILE_MODE = 0o600
CREDENTIALS_DIR_MODE = 0o700

#: TEMPORARY: providers that ``auth set`` refuses because a stored credential
#: would never reach them.
#:
#: The agent normalizes every resolved credential into ``config["api_key"]`` and
#: injects it into the provider's mount config. ``github-copilot``'s module
#: ignores that value entirely -- it resolves its token from ``os.environ`` only
#: (``COPILOT_AGENT_TOKEN`` -> ``COPILOT_GITHUB_TOKEN`` -> ``GH_TOKEN`` ->
#: ``GITHUB_TOKEN``), then falls back to the SDK's cached OAuth. So ``auth set``
#: would succeed, report the provider configured, and change nothing.
#:
#: The real fix belongs in the provider module: its token resolver must read the
#: agent-delivered credential from config first, then its env chain, then cached
#: OAuth. When that lands, DELETE this constant and its gate in ``auth_set``
#: outright. Do NOT grow it into a general provider-capability mechanism.
_CONFIG_CREDENTIAL_UNSUPPORTED: Final[frozenset[str]] = frozenset({"github-copilot", "openai-chatgpt"})


# ---------------------------------------------------------------------------
# Path + IO helpers
# ---------------------------------------------------------------------------


def credentials_path() -> Path:
    """Return the canonical path to the credentials file.

    ``~/.amplifier-agent/credentials.json`` by default; honours
    ``AMPLIFIER_AGENT_HOME`` like every other on-disk artefact in this
    project (see :mod:`amplifier_agent_lib.persistence`).
    """
    return amplifier_agent_home() / CREDENTIALS_FILENAME


def _load_credentials() -> dict[str, Any]:
    """Load the credentials file, returning the empty v1 envelope if absent.

    Tolerant of legacy shapes: if the existing file is missing the
    ``version`` envelope, treat the whole body as the ``providers`` dict
    and silently upgrade on next write. Raises ``click.ClickException``
    with a clear remediation hint on either failure mode -- bytes that
    are not valid UTF-8, or text that is not valid JSON. Neither is
    allowed to surface as a raw traceback.
    """
    path = credentials_path()
    if not path.exists():
        return {"version": CREDENTIALS_VERSION, "providers": {}}
    try:
        # encoding="utf-8-sig", matching hydrate_agent_overlay: a file touched
        # by Windows tooling (Notepad's "UTF-8 with BOM", Windows PowerShell
        # 5.1 Set-Content/Out-File) is UTF-8 WITH a BOM. Read as plain "utf-8"
        # the leading U+FEFF survives into the string and json.loads then dies
        # with "Unexpected UTF-8 BOM (decode using utf-8-sig)" -- surfaced to
        # the user as "not valid JSON", which is both wrong and unactionable.
        # utf-8-sig strips a leading BOM if present and is identical to utf-8
        # for files without one. We always write plain utf-8 (_atomic_write),
        # so this only ever forgives a file some other tool re-saved.
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        # Reachable whenever the bytes on disk are not valid UTF-8: a file
        # written by an older build under a non-UTF-8 locale default (Windows
        # cp1252), hand-edited in a legacy encoding, or truncated mid-sequence.
        # Without this branch the UnicodeDecodeError escapes uncaught and
        # every `auth` command -- plus each provider credential lookup behind
        # `run`/`models`/`serve` -- dies with a raw traceback.
        raise click.ClickException(
            f"Credentials file at {path} is not valid UTF-8 ({exc}). "
            "It was likely written by an older build under a non-UTF-8 locale. "
            "Re-save the file as UTF-8, or clear all stored credentials with "
            "`amplifier-agent auth clear --force` and re-add them."
        ) from exc
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Credentials file at {path} is not valid JSON ({exc}). "
            "Remove or fix the file, then retry. "
            "If you want to clear all stored credentials, run "
            "`amplifier-agent auth clear --force`."
        ) from exc
    if not isinstance(data, dict):
        raise click.ClickException(
            f"Credentials file at {path} has unexpected shape (expected object, got {type(data).__name__})."
        )
    # Legacy shape (flat dict of provider→key) → wrap into v1 envelope.
    if "providers" not in data:
        return {
            "version": CREDENTIALS_VERSION,
            "providers": {k: (v if isinstance(v, dict) else {"api_key": str(v)}) for k, v in data.items()},
        }
    if not isinstance(data.get("providers"), dict):
        raise click.ClickException(f"Credentials file at {path} has a non-object ``providers`` field.")
    return data


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` atomically (write tmp, fsync, rename).

    Also sets the directory and file modes to user-only (``0o700`` /
    ``0o600``) so the credentials file matches the conventions used by
    other CLIs (``aws``, ``gh``, ``claude``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, CREDENTIALS_DIR_MODE)
    except OSError:
        # Best-effort: parent dir may be a shared mount the user can't chmod.
        pass

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp_path, CREDENTIALS_FILE_MODE)
    os.replace(tmp_path, path)


def _save_credentials(data: dict[str, Any]) -> Path:
    """Persist a v1-envelope dict to disk and return the path written."""
    payload = {
        "version": CREDENTIALS_VERSION,
        "providers": data.get("providers", {}),
    }
    path = credentials_path()
    _atomic_write(path, payload)
    return path


# ---------------------------------------------------------------------------
# Resolver helper (consumed by provider_sources._resolve_env_credential)
# ---------------------------------------------------------------------------

# Module-level latch for _warn_credentials_unreadable_once. A single command
# resolves credentials once per provider (and Azure-style providers resolve an
# extra endpoint field), so an unlatched warning would print a dozen identical
# lines for one broken file.
_credentials_warning_emitted = False


def _warn_credentials_unreadable_once(exc: click.ClickException) -> None:
    """Surface an unreadable credentials file to the user exactly once.

    The resolvers below deliberately never raise: one bad write must not
    brick every subsequent invocation. But degrading silently is its own
    failure -- with only a DEBUG log, a corrupt file makes every provider
    report ``<not set>`` and exit 0, which reads as "no credentials
    configured" rather than "your credentials file is broken." The user
    then re-adds keys that were never actually lost.

    So: resilient *and* visible. Emit the remediation hint (which carries
    the path and the specific parse failure) once per process on stderr,
    leaving stdout clean for callers that parse it.
    """
    global _credentials_warning_emitted
    logger.debug("credentials.json unreadable; resolving as empty (%s)", exc.message)
    if _credentials_warning_emitted:
        return
    _credentials_warning_emitted = True
    click.echo(f"Warning: {exc.message}", err=True)


def resolve_credential_from_file(provider_name: str) -> str:
    """Look up ``provider_name``'s ``api_key`` in the credentials file.

    Returns ``""`` if no file exists or the entry is missing. Never
    raises -- a malformed file is treated as empty so a one-time bad
    write doesn't break every subsequent invocation -- but the failure is
    reported once per process via :func:`_warn_credentials_unreadable_once`
    so the degradation is never silent.

    The caller (``_resolve_env_credential``) chains this AFTER the env var
    lookup so shell env always wins.
    """
    try:
        data = _load_credentials()
    except click.ClickException as exc:
        _warn_credentials_unreadable_once(exc)
        return ""
    providers = data.get("providers") or {}
    entry = providers.get(provider_name) or {}
    if not isinstance(entry, dict):
        return ""
    key = entry.get("api_key")
    return key if isinstance(key, str) else ""


def resolve_field_from_file(provider_name: str, field: str) -> str:
    """Read an arbitrary string field for a provider entry.

    Used by Azure-style providers that store endpoint URLs alongside
    the api_key. Returns ``""`` when absent. Shares the never-raise /
    warn-once contract of :func:`resolve_credential_from_file`.
    """
    try:
        data = _load_credentials()
    except click.ClickException as exc:
        _warn_credentials_unreadable_once(exc)
        return ""
    providers = data.get("providers") or {}
    entry = providers.get(provider_name) or {}
    if not isinstance(entry, dict):
        return ""
    value = entry.get(field)
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _mask(value: str) -> str:
    """Return a display-safe redaction of an API key.

    Shows the first 6 + last 4 chars (matching the ``aws cli`` convention)
    when the value is long enough to be meaningfully partial; otherwise
    fully redacts.
    """
    if not value:
        return "<not set>"
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _file_perm_octal(path: Path) -> str:
    """Return the file's mode as a 3-digit octal string for display."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return "???"
    return f"{mode:03o}"


def _display_value(resolution: CredentialResolution) -> str:
    """Render a resolution's credential fields for display, masking secrets.

    ``api_key`` fields are masked via :func:`_mask` (matches the existing
    ``auth list`` convention). ``host`` fields (ollama) are not secrets and
    are shown verbatim. Falls back to ``"<not set>"`` when neither is
    present.
    """
    if "api_key" in resolution.fields:
        return _mask(resolution.fields["api_key"])
    if "host" in resolution.fields:
        return resolution.fields["host"]
    return "<not set>"


# ---------------------------------------------------------------------------
# Click surface
# ---------------------------------------------------------------------------


@click.group(name="auth")
def auth_group() -> None:
    """Manage persistent provider credentials.

    Credentials are stored at ``~/.amplifier-agent/credentials.json``
    (mode 0600). Set once via ``auth set``; every subsequent
    ``amplifier-agent`` invocation -- including the HTTP server, the
    ``models list`` command, and any wrapper such as
    ``amplifier-opencode`` -- reads from this file automatically.

    Shell environment variables (``ANTHROPIC_API_KEY``,
    ``OPENAI_API_KEY``, etc.) take precedence over the file so you can
    still override per-shell or per-invocation without touching the
    persisted credentials.
    """


@auth_group.command("set")
@click.argument("provider")
@click.argument("api_key", required=False)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help=(
        "Read the API key from stdin instead of passing it as an argument. "
        "Avoids exposing the key in the process list (argv) -- use this from "
        "scripts and wrappers. "
        'Example: printf %s "$KEY" | amplifier-agent auth set anthropic --stdin'
    ),
)
@click.option(
    "--endpoint",
    default=None,
    help=("Endpoint URL (for Azure-style providers that need a deployment URL alongside the API key)."),
)
def auth_set(provider: str, api_key: str | None, read_stdin: bool, endpoint: str | None) -> None:
    """Set the API key for PROVIDER.

    PROVIDER must be one of the known provider IDs (``anthropic``,
    ``openai``, ``azure-openai``, ``ollama``, ``github-copilot``). The key
    is stored in ``~/.amplifier-agent/credentials.json`` with mode 0600.

    ``github-copilot`` is the one exception and is refused: that provider
    reads its token from the environment and never sees a stored
    credential. Export ``GITHUB_TOKEN`` instead.

    Pass the key as an argument, or (recommended for scripts and wrappers)
    pipe it via ``--stdin`` so the secret never appears in the process
    list. Examples::

        amplifier-agent auth set anthropic sk-ant-...
        printf %s "$KEY" | amplifier-agent auth set anthropic --stdin
        amplifier-agent auth set azure-openai sk-... --endpoint https://...
    """
    if provider not in KNOWN_PROVIDERS:
        known = ", ".join(KNOWN_PROVIDERS)
        raise click.ClickException(f"Unknown provider {provider!r}. Known providers: {known}")

    # TEMPORARY: see _CONFIG_CREDENTIAL_UNSUPPORTED. Delete both together.
    if provider in _CONFIG_CREDENTIAL_UNSUPPORTED:
        raise click.ClickException(
            f"Refusing to store a credential for {provider!r}: it reads its token from the "
            "environment, not from stored credentials, so the stored value would never reach it.\n"
            "Export one of these instead (first non-empty wins): COPILOT_AGENT_TOKEN, "
            "COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN. For example:\n"
            "    export GITHUB_TOKEN=$(gh auth token)\n"
            "An existing `gh` or VS Code login may already authenticate it via cached OAuth, so "
            "try it before exporting anything.\n"
            "This is a temporary limitation, not permanent: the provider module will accept the "
            "agent-delivered credential in a future release."
        )

    if read_stdin:
        if api_key is not None:
            raise click.ClickException("Pass the API key via --stdin OR as an argument, not both.")
        # Read exactly one line so a trailing newline from ``echo``/``printf``
        # doesn't get stored as part of the key.
        api_key = sys.stdin.readline().strip()
    if not api_key:
        raise click.ClickException(
            "No API key provided. Pass it as an argument, or pipe it via --stdin "
            '(e.g. `printf %s "$KEY" | amplifier-agent auth set anthropic --stdin`).'
        )

    data = _load_credentials()
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise click.ClickException("Credentials file has malformed providers section; refusing to overwrite.")
    entry = providers.get(provider) or {}
    if not isinstance(entry, dict):
        entry = {}
    entry["api_key"] = api_key
    if endpoint is not None:
        entry["endpoint"] = endpoint
    providers[provider] = entry

    path = _save_credentials(data)
    click.secho(
        f"Stored credentials for {provider!r} at {path} (mode {_file_perm_octal(path)}).",
        fg="green",
    )


@auth_group.command("list")
def auth_list() -> None:
    """List configured providers (api keys masked).

    Shows the credential source for each known provider, resolved via the
    single canonical chain (:func:`amplifier_agent_cli.provider_sources.resolve_credential_detailed`):

      env=<VAR>     value comes from a shell environment variable
      file          value comes from credentials.json
      default       ollama's built-in localhost default (not "configured")
      not set       no credential available

    Shell environment variables ALWAYS take precedence over file entries.
    """
    path = credentials_path()
    file_exists = path.exists()

    click.echo(f"Credentials file: {path}")
    if not file_exists:
        click.echo("  (not present)")
    else:
        click.echo(f"  (mode {_file_perm_octal(path)})")
    click.echo()

    rows: list[tuple[str, str, str]] = []
    for provider in KNOWN_PROVIDERS:
        resolution = resolve_credential_detailed(provider)
        value = _display_value(resolution)
        if resolution.source == "env":
            source_label = f"env={resolution.env_var}"
        elif resolution.source == "file":
            source_label = "file"
        elif resolution.source == "default":
            source_label = "default"
        else:
            source_label = "—"
        rows.append((provider, value, source_label))

    width_provider = max(len(r[0]) for r in rows)
    width_value = max(len(r[1]) for r in rows)
    for provider, masked, source in rows:
        click.echo(
            f"  {provider:<{width_provider}}  {masked:<{width_value}}  {source}",
        )


@auth_group.command("remove")
@click.argument("provider")
def auth_remove(provider: str) -> None:
    """Remove the stored credential for PROVIDER.

    Only affects the file -- the shell environment variable is untouched.
    """
    data = _load_credentials()
    providers = data.get("providers") or {}
    if not isinstance(providers, dict) or provider not in providers:
        click.secho(f"No stored credential for {provider!r} (no change).", fg="yellow")
        return
    providers.pop(provider, None)
    path = _save_credentials(data)
    click.secho(f"Removed {provider!r} from {path}.", fg="green")


@auth_group.command("status")
def auth_status() -> None:
    """Diagnose the credential-resolution chain.

    For each known provider, shows the resolved source
    (:func:`amplifier_agent_cli.provider_sources.resolve_credential_detailed`)
    so it's clear which value wins.
    """
    path = credentials_path()
    file_exists = path.exists()

    click.echo(f"Credentials file: {path}")
    click.echo(f"  exists: {file_exists}")
    if file_exists:
        click.echo(f"  mode:   {_file_perm_octal(path)}")
    click.echo()

    click.echo("Per-provider resolution (env wins if both are set):")
    for provider in KNOWN_PROVIDERS:
        resolution = resolve_credential_detailed(provider)
        primary_env_var = resolution.env_var or "?"

        if resolution.source == "env":
            verdict = f"USING env={resolution.env_var}"
        elif resolution.source == "file":
            verdict = "USING file entry"
        elif resolution.source == "default":
            verdict = f"USING built-in default ({resolution.fields.get('host', '')})"
        else:
            verdict = f"NOT SET (export {primary_env_var} or run `auth set {provider} ...`)"
        click.echo(f"  {provider:<14}  {verdict}")


@auth_group.command("clear")
@click.option("--force", is_flag=True, help="Required to confirm destructive deletion.")
def auth_clear(force: bool) -> None:
    """Remove ALL stored credentials.

    Does NOT touch shell environment variables. Use this to reset the
    credentials file (e.g. after key rotation or for clean dev setups).
    """
    if not force:
        click.secho(
            "auth clear is destructive. Re-run with --force to proceed.",
            fg="yellow",
            err=True,
        )
        sys.exit(2)
    path = credentials_path()
    if not path.exists():
        click.secho("No credentials file to clear.", fg="yellow")
        return
    path.unlink()
    click.secho(f"Removed {path}.", fg="green")
