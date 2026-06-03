"""Admin command: doctor — self-diagnostic for provider, XDG paths, Python, bundle cache.

Checks (in order):
  1. Python version (>= 3.11)
  2. Provider configured (any provider env var set)
  3. XDG config home writable
  4. XDG cache home writable
  5. XDG state home writable
  6. Bundle module presence (CR-1/A4/SC-2)
  7. WireApprovalProvider shape (CR-2)
  8. SessionStore roundtrip (A2)
  9. mcp module importable (when tool-mcp is declared in bundle) — G4
 10. Prepared-bundle cache present for the current version (INFO only — never causes FAIL)

Exit 0 if checks 1-9 all pass; exit 1 if any of checks 1-9 fail.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
import yaml as _yaml

from amplifier_agent_lib import __version__, persistence
from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.cache import cache_dir_for_version

_OK: str = "[ OK ]"
_FAIL: str = "[FAIL]"
_INFO: str = "[INFO]"


@dataclass
class CacheState:
    """Represents the current state of the prepared-bundle cache."""

    status: str  # 'prepared' | 'needs prepare'
    cache_dir: Path


def check_cache_state(aaa_version: str) -> CacheState:
    """Check whether a prepared bundle exists for the given AaA version.

    Returns a :class:`CacheState` with ``status='prepared'`` if both
    ``manifest.json`` and at least one non-manifest artifact exist in the
    version-keyed cache directory; otherwise ``status='needs prepare'``.
    """
    cache_dir = cache_dir_for_version(aaa_version)
    manifest = cache_dir / "manifest.json"

    if cache_dir.exists() and manifest.exists():
        artifacts = [f for f in cache_dir.iterdir() if f.name != "manifest.json"]
        if artifacts:
            return CacheState(status="prepared", cache_dir=cache_dir)

    return CacheState(status="needs prepare", cache_dir=cache_dir)


def _check_provider() -> tuple[bool, str]:
    """Return (True, OK line) if bundle.md declares a string ``default_provider``,
    (False, FAIL line) otherwise.

    D6: provider selection comes from config / bundle.md. The doctor's job is
    to verify the bundle integrity invariant — that the vendored manifest
    actually declares a default — not to autodetect from env vars.
    """
    try:
        manifest = _yaml.safe_load(BUNDLE_MD.read_text(encoding="utf-8").split("---\n")[1])
    except Exception as exc:
        return (False, f"{_FAIL} bundle default_provider: parse failed ({exc.__class__.__name__})")
    default = manifest.get("default_provider") if isinstance(manifest, dict) else None
    if isinstance(default, str):
        return (True, f"{_OK} bundle default_provider: {default}")
    return (False, f"{_FAIL} bundle default_provider: missing in bundle.md (D6)")


def _check_writable(label: str, path: Path) -> tuple[bool, str]:
    """Return (True, OK line) if *path* is writable; (False, FAIL line) on OSError."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-probe"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        return (True, f"{_OK} {label}: {path}")
    except OSError as exc:
        return (False, f"{_FAIL} {label}: {path} ({exc.__class__.__name__})")


def _check_python_version() -> tuple[bool, str]:
    """Return (True, OK line) if Python >= 3.11; (False, FAIL line) otherwise."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    micro = sys.version_info.micro
    label = f"python: {major}.{minor}.{micro}"
    if (major, minor) < (3, 11):
        return (False, f"{_FAIL} {label} (need >= 3.11)")
    return (True, f"{_OK} {label}")


def _emit_bundle_shas() -> None:
    """Emit sha256-of-source-URL lines for every module declared in bundle.md.

    v1 stub: SHA is computed over the ``source:`` URL string, not over the
    installed module's content. This still detects supply-chain drift at the
    *manifest* level — if bundle.md is edited (URL changed, pin added/removed),
    a baseline diff will fire. Full content-pinning is tracked as D-v1.x-02.

    Output format (one line per module, sorted by module name):
        sha256_prefix=<16-hex>  module=<name>  source=<url>

    Errors (missing bundle, malformed YAML) are reported as ``[FAIL]`` lines on
    stderr; this function does not raise.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    try:
        text = BUNDLE_MD.read_text("utf-8")
    except FileNotFoundError as exc:
        click.echo(f"{_FAIL} emit-sha: bundle.md not found ({exc})", err=True)
        return

    parts = text.split("---\n")
    if len(parts) < 3:
        click.echo(
            f"{_FAIL} emit-sha: bundle.md has no YAML frontmatter "
            f"(expected at least 3 '---'-delimited parts, got {len(parts)})",
            err=True,
        )
        return

    try:
        manifest = _yaml.safe_load(parts[1])
    except _yaml.YAMLError as exc:
        click.echo(f"{_FAIL} emit-sha: bundle.md YAML parse error: {exc}", err=True)
        return

    if not isinstance(manifest, dict):
        click.echo(
            f"{_FAIL} emit-sha: bundle.md frontmatter is not a mapping (got {type(manifest).__name__})",
            err=True,
        )
        return

    session = manifest.get("session", {}) or {}
    entries: list[tuple[str, str]] = []

    for slot in ("orchestrator", "context", "provider"):
        block = session.get(slot)
        if isinstance(block, dict):
            name = block.get("module")
            src = block.get("source")
            if isinstance(name, str) and isinstance(src, str):
                entries.append((name, src))

    for collection_key in ("tools", "hooks"):
        items = manifest.get(collection_key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("module")
            src = item.get("source")
            if isinstance(name, str) and isinstance(src, str):
                entries.append((name, src))

    click.echo("# bundle module source SHAs (v1: sha of source URL string)")
    for name, src in sorted(entries, key=lambda pair: pair[0]):
        sha_prefix = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        click.echo(f"sha256_prefix={sha_prefix}  module={name}  source={src}")


# ---------------------------------------------------------------------------
# A7c: extended bundle / approval-shim / session-store checks (Design §4.9).
#
# These checks detect regressions of the Phase 1 / A4 invariants:
#   - _check_bundle_modules        — catches A4 / CR-1 / SC-2 regressions in bundle.md
#   - _check_approval_provider_shape — catches Phase 1 A3 (CR-2) regressions
#   - _check_session_store_roundtrip — catches Phase 1 A2 regressions
# Run only in the full (non-quick) path.
# ---------------------------------------------------------------------------


def _check_bundle_modules() -> tuple[bool, str]:
    """Static parse of bundle.md — verify required modules are present / forbidden absent.

    Required (any failure ⇒ FAIL):
      * ``session.context.module == 'context-simple'``  (CR-1)
      * ``tool-mcp`` appears in ``tools[*].module``       (A4)
      * ``hooks-approval`` appears in ``hooks[*].module`` (A4)
      * ``hooks-logging`` NOT in ``hooks[*].module``      (SC-2)
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    try:
        text = BUNDLE_MD.read_text("utf-8")
    except FileNotFoundError as exc:
        return (False, f"{_FAIL} bundle modules: bundle.md not found ({exc})")

    parts = text.split("---\n")
    if len(parts) < 3:
        return (
            False,
            f"{_FAIL} bundle modules: bundle.md has no YAML frontmatter "
            f"(expected at least 3 '---'-delimited parts, got {len(parts)})",
        )

    try:
        manifest = _yaml.safe_load(parts[1])
    except _yaml.YAMLError as exc:
        return (False, f"{_FAIL} bundle modules: bundle.md YAML parse error: {exc}")

    if not isinstance(manifest, dict):
        return (
            False,
            f"{_FAIL} bundle modules: bundle.md frontmatter is not a mapping (got {type(manifest).__name__})",
        )

    # CR-1: context-simple
    ctx_block = (manifest.get("session") or {}).get("context") or {}
    ctx_module = ctx_block.get("module", "") if isinstance(ctx_block, dict) else ""
    if ctx_module != "context-simple":
        return (
            False,
            f"{_FAIL} bundle modules: session.context.module must be 'context-simple' (CR-1); got {ctx_module!r}",
        )

    # A4: tool-mcp present
    tools = manifest.get("tools") or []
    tool_modules = [t.get("module") for t in tools if isinstance(t, dict)]
    if "tool-mcp" not in tool_modules:
        return (
            False,
            f"{_FAIL} bundle modules: tool-mcp missing from tools list (A4); present: {tool_modules!r}",
        )

    # A4: hooks-approval present
    hooks = manifest.get("hooks") or []
    hook_modules = [h.get("module") for h in hooks if isinstance(h, dict)]
    if "hooks-approval" not in hook_modules:
        return (
            False,
            f"{_FAIL} bundle modules: hooks-approval missing from hooks list (A4); present: {hook_modules!r}",
        )

    # SC-2: hooks-logging absent
    if "hooks-logging" in hook_modules:
        return (
            False,
            f"{_FAIL} bundle modules: hooks-logging must be absent (SC-2); present in hooks list: {hook_modules!r}",
        )

    return (
        True,
        f"{_OK} bundle modules: context-simple, tool-mcp, hooks-approval present; hooks-logging absent",
    )


def _check_approval_provider_shape() -> tuple[bool, str]:
    """Verify WireApprovalProvider conforms to the ApprovalProvider contract.

    Checks:
      * ``WireApprovalProvider`` imports cleanly (else Phase 1 A3 missing).
      * It is a subclass of ``amplifier_core.ApprovalProvider`` (or has it
        in its MRO — handles the case where ``ApprovalProvider`` is a
        non-runtime-checkable ``Protocol``). Skipped if amplifier_core is
        not installed.
      * Source defines all three approval error codes
        (``approval_translation_failed``, ``approval_timeout``,
        ``approval_protocol_violation``) — else CR-2 may be incomplete.
    """
    try:
        from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider
    except ImportError as exc:
        return (
            False,
            f"{_FAIL} wire_approval_provider: import failed (Phase 1 A3 may not be merged): {exc}",
        )

    # Subclass / MRO check (optional — only if amplifier_core is installed).
    try:
        from amplifier_core.interfaces import ApprovalProvider
    except ImportError:
        subclass_note = "subclass check skipped (amplifier_core not installed)"
    else:
        # ``ApprovalProvider`` is a ``typing.Protocol`` and is not
        # ``@runtime_checkable``, so ``issubclass()`` would raise ``TypeError``.
        # Inspect the MRO directly — this is the semantically equivalent check
        # for the inheritance-based subclass relationship the spec verifies
        # (``class WireApprovalProvider(ApprovalProvider)``).
        if ApprovalProvider not in WireApprovalProvider.__mro__:
            return (
                False,
                f"{_FAIL} wire_approval_provider: WireApprovalProvider is not a "
                f"subclass of amplifier_core.ApprovalProvider",
            )
        subclass_note = "subclass check passed"

    # Error-code presence check.
    try:
        src = inspect.getsource(WireApprovalProvider)
    except OSError as exc:
        return (
            False,
            f"{_FAIL} wire_approval_provider: could not read source: {exc}",
        )

    required_codes = (
        "approval_translation_failed",
        "approval_timeout",
        "approval_protocol_violation",
    )
    missing = [code for code in required_codes if code not in src]
    if missing:
        return (
            False,
            f"{_FAIL} wire_approval_provider: missing error code(s) {missing!r} in source (CR-2 may be incomplete)",
        )

    return (
        True,
        f"{_OK} wire_approval_provider: {subclass_note}; all three error codes present",
    )


def _bundle_declares_tool_mcp() -> bool:
    """Return True iff ``tool-mcp`` appears in ``bundle.md``'s ``tools`` list.

    Used to gate ``_check_mcp_importable()`` — we only require ``mcp`` to be
    importable when the bundle actually declares the ``tool-mcp`` module.
    Bundles that omit ``tool-mcp`` (e.g. air-gapped deployments) don't need
    ``mcp`` installed and shouldn't be penalised by this check.
    """
    try:
        text = BUNDLE_MD.read_text("utf-8")
    except FileNotFoundError:
        return False
    parts = text.split("---\n")
    if len(parts) < 3:
        return False
    try:
        manifest = _yaml.safe_load(parts[1])
    except _yaml.YAMLError:
        return False
    if not isinstance(manifest, dict):
        return False
    tools = manifest.get("tools") or []
    if not isinstance(tools, list):
        return False
    return any(isinstance(t, dict) and t.get("module") == "tool-mcp" for t in tools)


def _check_mcp_importable() -> tuple[bool, str]:
    """Verify that the ``mcp`` Python package imports cleanly (G4).

    ``tool-mcp`` Python-imports ``mcp`` at mount time. If the package isn't
    installed, the bundle fails to mount and the user sees a downstream
    ``'Bundle' object has no attribute 'origins'`` AttributeError that masks
    the real cause. This check surfaces the missing-mcp condition with a
    clear remediation line rather than letting the failure cascade into
    an opaque runtime error.

    Skipped (reported as INFO) when the bundle doesn't declare ``tool-mcp``.
    """
    if not _bundle_declares_tool_mcp():
        return (True, f"{_INFO} mcp module: skipped (tool-mcp not in bundle)")
    try:
        import mcp  # noqa: F401  — import-only probe
    except ImportError as exc:
        return (
            False,
            f"{_FAIL} mcp module: import failed ({exc}). "
            "Install with `uv tool install --reinstall --from "
            "git+https://github.com/microsoft/amplifier-agent` "
            "(mcp is now a declared transitive dep; reinstall to pick it up).",
        )
    return (True, f"{_OK} mcp module: importable")


async def _check_session_store_roundtrip() -> tuple[bool, str]:
    """Roundtrip a probe transcript through SessionStore in a tempdir.

    Verifies that ``SessionStore.save`` + ``SessionStore.load`` round-trip
    transcript and metadata losslessly (Phase 1 A2 invariant).
    """
    try:
        from amplifier_agent_lib.session_store import SessionStore
    except ImportError as exc:
        return (
            False,
            f"{_FAIL} session_store: import failed (Phase 1 A2 may not be merged): {exc}",
        )

    transcript: list[dict] = [
        {"role": "user", "content": "doctor probe message"},
        {"role": "assistant", "content": "probe acknowledged"},
    ]
    metadata: dict = {"probe": True, "doctor_check": "roundtrip"}
    session_id = "doctor-probe-roundtrip"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir))
            store.save(session_id, transcript, metadata)
            result = store.load(session_id)
    except Exception as exc:  # pragma: no cover — narrow exception surface in error path
        return (
            False,
            f"{_FAIL} session_store: roundtrip raised {exc.__class__.__name__}: {exc}",
        )

    if result is None:
        return (
            False,
            f"{_FAIL} session_store: load returned None after save for {session_id!r}",
        )

    loaded_transcript, loaded_metadata = result
    if loaded_transcript != transcript:
        return (
            False,
            f"{_FAIL} session_store: transcript not lossless; saved={transcript!r}, loaded={loaded_transcript!r}",
        )
    if loaded_metadata.get("probe") is not True:
        return (
            False,
            f"{_FAIL} session_store: metadata.probe not preserved; loaded metadata={loaded_metadata!r}",
        )

    return (
        True,
        f"{_OK} session_store: write/read roundtrip in tempdir succeeded",
    )


@click.command()
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help=(
        "Exit non-zero on any warning (for CI / image-build gating). "
        "Without --strict, a missing prepared cache is [INFO] only."
    ),
)
@click.option(
    "--quick",
    is_flag=True,
    default=False,
    help=(
        "Run minimal checks only: Python version and prepared-cache presence. "
        "Skips provider, XDG writability, and extended bundle checks."
    ),
)
@click.option(
    "--emit-sha",
    is_flag=True,
    default=False,
    help=(
        "Emit sha256 of each bundle module source URL for supply-chain "
        "baseline diffing. v1 stub: SHA is of the source URL string. "
        "Full content SHA is D-v1.x-02."
    ),
)
def doctor(strict: bool, quick: bool, emit_sha: bool) -> None:
    """Run self-diagnostics and report system health."""
    cfg = persistence.config_root()
    cache = persistence.cache_root()
    state = persistence.state_root()

    cache_info = check_cache_state(__version__)
    is_prepared = cache_info.status == "prepared"

    if quick:
        python_ok, python_line = _check_python_version()
        click.echo(python_line)
        cache_prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
        click.echo(f"{cache_prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")
        all_ok = python_ok and (is_prepared or not strict)
        if not all_ok:
            sys.exit(1)
        return

    checks: list[tuple[bool, str]] = [
        _check_python_version(),
        _check_provider(),
        _check_writable("config home", cfg),
        _check_writable("cache home", cache),
        _check_writable("state home", state),
    ]

    for _ok, line in checks:
        click.echo(line)

    # A7c: bundle module presence
    bundle_ok, bundle_line = _check_bundle_modules()
    click.echo(bundle_line)
    checks.append((bundle_ok, bundle_line))
    # A7c: wire_approval_provider shape-check
    approval_ok, approval_line = _check_approval_provider_shape()
    click.echo(approval_line)
    checks.append((approval_ok, approval_line))
    # A7c: session_store roundtrip
    store_ok, store_line = asyncio.run(_check_session_store_roundtrip())
    click.echo(store_line)
    checks.append((store_ok, store_line))

    # G4: mcp module importable (only when tool-mcp is in the bundle).
    # Surfaces the "forgot `--with mcp`" install failure with a clear
    # remediation line instead of a downstream `bundle.origins` AttributeError.
    mcp_ok, mcp_line = _check_mcp_importable()
    click.echo(mcp_line)
    checks.append((mcp_ok, mcp_line))

    cache_prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
    click.echo(f"{cache_prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")

    if emit_sha:
        _emit_bundle_shas()

    hard_failures = not all(ok for ok, _ in checks)
    cache_failure = strict and not is_prepared
    if hard_failures or cache_failure:
        sys.exit(1)
