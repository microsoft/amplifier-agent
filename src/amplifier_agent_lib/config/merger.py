"""Layered merge of host config over bundle module configs (D5).

Per the host config design (``docs/designs/2026-06-01-host-config-layer-revisit.md``
\u00a7D5), the engine composes the effective module config at bundle-mount time
by overlaying the host's pass-through config on top of the bundle's static
config.  The bundle's declaration is the base; host overrides apply per
block, per key.

The merge is a **shallow** per-key overlay within each module block
(``{**bundle_static, **host_overrides}``).  There is no recursive
merging, no key renaming, no curation, and no schema translation:
amplifier-agent only parameterizes what ``bundle.md`` already declares
(D4 pass-through stance).  If the host wants to override a key that the
bundle exposes, it sets that key; otherwise the bundle default stands.

When the host did not provide a config tier at all (``host_config is None``,
returned by :func:`amplifier_agent_lib.config.load_config` when neither
``--config`` nor ``$AMPLIFIER_AGENT_CONFIG`` is present), the merge is a
no-op and the bundle module configs pass through unchanged.

``allowProtocolSkew`` is the one top-level host key that is **not** a module
pass-through -- it's engine-level and controls whether the engine boot path
enforces protocol version compatibility.  The merger surfaces it as a
separate return field so :mod:`amplifier_agent_lib._runtime.engine.boot` can
read the flag without re-parsing the host config dict (D4).
"""

from __future__ import annotations

import copy
from typing import Any

from amplifier_agent_lib.protocol.errors import AaaError

# D4: friendly provider names accepted in ``host.provider.module`` mapped to
# the bundle module key whose config block receives the overlay.  The host
# config uses the friendly name (e.g. ``"anthropic"``) so the YAML stays
# concise; the merger translates to the module key (``"anthropic-provider"``)
# used by the bundle module config dict.
_PROVIDER_NAME_TO_MODULE_KEY = {
    "anthropic": "anthropic-provider",
    "openai": "openai-provider",
    "azure-openai": "azure-openai-provider",
    "ollama": "ollama-provider",
}


def _concat_list_pass_through(bundle_value: list | None, host_value: list) -> list:
    """D12: list-concat merge for list-shaped pass-through values.

    The bundle's curated list comes first; the host's list is appended.  The
    bundle is the floor, the host extends, and the host *cannot* silently
    erase what the bundle declared.  This is the same opt-in-only stance the
    rest of the host config layer takes (D4): host_config can parameterize
    what the bundle already exposes, never strip it.

    The ``bundle_value or []`` normalisation lets callers pass ``None`` or a
    missing key through ``dict.get(...)`` without a guard at every call site
    -- a bundle that omits the sub-key is semantically an empty floor.

    Generalizes per D12's closing paragraph: applies to any future list-shaped
    pass-through sub-key the host config grows, not just ``skills.skills``.
    """
    return list(bundle_value or []) + list(host_value)


def _merge_skills(merged: dict[str, dict[str, Any]], skills_block: dict[str, Any]) -> None:
    """D11/D12: overlay the host ``skills`` block onto the ``tool-skills`` module config.

    The host ``skills`` block has two sub-keys with distinct merge semantics:

    * ``skills.skills`` is list-shaped and merges list-concat per D12 --
      bundle first, host appended.  The bundle is the floor; the host can
      only extend.
    * ``skills.visibility`` is dict-shaped and merges shallow per-key per
      D5 -- bundle keys come through unless the host overrides them.  The
      bundle's declared visibility floor stands; the host parameterizes
      per key, never silently strips a bundle-declared key.

    D7: if the bundle declares no ``tool-skills`` mount at all, the host has
    nothing to parameterize.  A non-empty ``skills`` block in that situation
    is a configuration error -- the host is pushing config at a module that
    won't be mounted, so the merger refuses with
    ``config_no_matching_module`` (classification ``protocol``) rather than
    silently fabricating a ``tool-skills`` config dict that no module will
    consume.  The empty-block-plus-missing-mount boundary remains a no-op:
    the host has nothing to push, so the absence of a target is harmless.
    """
    entry = merged.get("tool-skills")
    if entry is None:
        if not skills_block:
            # D7 boundary: empty host block + missing mount = no-op.
            # The host has nothing to push, so the absence of the target
            # module is harmless; we leave ``merged`` untouched (no
            # fabricated ``tool-skills`` entry) and return.
            return
        raise AaaError(
            code="config_no_matching_module",
            classification="protocol",
            message=(
                "host_config declares a skills: block but the bundle has no "
                "tool-skills mount entry. Either add tool-skills to the bundle "
                "or remove the skills: block from host_config."
            ),
        )
    cfg = entry
    if "skills" in skills_block:
        cfg["skills"] = _concat_list_pass_through(cfg.get("skills"), skills_block["skills"])
    if "visibility" in skills_block:
        # D5: shallow per-key dict overlay.  Bundle's declared visibility keys
        # come through unless the host overrides them; the host's keys win.
        # Mirrors the inline overlay applied to host.mcp, host.approval, and
        # host.provider.config -- same opt-in stance, dict-shaped sub-key.
        base = cfg.get("visibility", {})
        host_visibility = skills_block["visibility"]
        if isinstance(base, dict) and isinstance(host_visibility, dict):
            cfg["visibility"] = {**base, **host_visibility}


def merge_config(
    *,
    bundle_modules: dict[str, dict[str, Any]],
    host_config: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Overlay host config over bundle module configs (D5, shallow per-key).

    Arguments are keyword-only so call sites at the bundle-mount seam stay
    self-documenting; the two dicts are easy to swap by accident.

    :param bundle_modules: The bundle's declared module configs, keyed by
        module id (e.g. ``"tool-mcp"``, ``"hooks-approval"``).  Each value
        is the static config block that ``bundle.md`` declares for that
        module.  This dict is treated as immutable input \u2014 callers can
        rely on it being unchanged on return.
    :param host_config: The parsed host config dict from
        :func:`amplifier_agent_lib.config.load_config`, or ``None`` when
        neither config tier (``--config`` flag, ``$AMPLIFIER_AGENT_CONFIG``
        env var) was provided.  When ``None``, the bundle module configs
        are returned unchanged (D5 no-op path) and ``allowProtocolSkew``
        defaults to ``False``.
    :returns: A tuple ``(merged_modules, allow_protocol_skew)``.
        ``merged_modules`` is a new dict of module configs with host
        overrides applied per-block per-key; callers receive a deep copy
        so mutating the result will not affect ``bundle_modules``.
        ``allow_protocol_skew`` is the engine-level flag surfaced from the
        host's top-level ``allowProtocolSkew`` key (D4); it defaults to
        ``False`` when the host omits the key or supplies no host config.
    """
    # Deep copy so callers cannot reach back into the bundle's declared
    # config by mutating our return value, and so per-block overlays can
    # mutate merged-block dicts in place safely.
    merged = copy.deepcopy(bundle_modules)
    if host_config is None:
        # D5 no-op path: no host tier, bundle defaults pass through.
        # D4: allowProtocolSkew defaults to False (opt-in engine flag).
        return merged, False

    # D4, D5: host.mcp -> tool-mcp module config (shallow per-key overlay).
    mcp_overrides = host_config.get("mcp")
    if isinstance(mcp_overrides, dict):
        base = merged.get("tool-mcp", {})
        merged["tool-mcp"] = {**base, **mcp_overrides}

    # D4, D5: host.approval -> hooks-approval module config (shallow per-key
    # overlay). List values (e.g. ``patterns``) are replaced wholesale by the
    # host's list -- there is no array merge, consistent with the per-key
    # overlay stance of D5.
    approval_overrides = host_config.get("approval")
    if isinstance(approval_overrides, dict):
        base = merged.get("hooks-approval", {})
        merged["hooks-approval"] = {**base, **approval_overrides}

    # D4, D5: host.provider.{module,config} -> the named provider module's
    # config (shallow per-key overlay).  The host names the target provider
    # module via the friendly ``module`` field (e.g. ``"anthropic"``); the
    # merger translates that to the module key (``"anthropic-provider"``) and
    # overlays ``config`` on top of the bundle's static block.  If ``module``
    # is missing, unknown, or ``config`` is not a dict, we fall through
    # defensively -- the validator (D7) is responsible for surfacing those
    # cases to the user; the merger leaves the bundle config intact rather
    # than guessing which provider to target.
    provider_block = host_config.get("provider")
    if isinstance(provider_block, dict):
        provider_module = provider_block.get("module")
        provider_config = provider_block.get("config")
        if (
            isinstance(provider_module, str)
            and provider_module in _PROVIDER_NAME_TO_MODULE_KEY
            and isinstance(provider_config, dict)
        ):
            module_key = _PROVIDER_NAME_TO_MODULE_KEY[provider_module]
            base = merged.get(module_key, {})
            merged[module_key] = {**base, **provider_config}

    # D11/D12: host.skills -> tool-skills module config.  Unlike the dict-shaped
    # overlay blocks above, ``skills`` contains list-shaped sub-keys (currently
    # ``skills.skills``) that merge list-concat per D12 -- bundle first, host
    # appended.  The bundle is the floor, host can only extend.  The block is
    # processed only when it's a dict; non-dict values fall through to the
    # validator (D7) which surfaces shape errors at parse time.
    skills_block = host_config.get("skills")
    if isinstance(skills_block, dict):
        _merge_skills(merged, skills_block)

    # `providers` (plural) is server-mode only and read directly by the HTTP
    # lifespan from host_config; no overlay into bundle_modules here.
    # The loader's _validate_providers_registry enforces the schema; the merger
    # intentionally ignores this key to keep single-turn mode unaffected.

    # D4: ``allowProtocolSkew`` is engine-level, not a module pass-through.
    # Surface it as a separate return field so the engine boot path can read
    # it without re-parsing host_config. Defaults to False when absent or
    # falsy; the bool() guard normalises non-bool truthy/falsy values from
    # the parsed YAML (the validator (D7) enforces the type contract upstream).
    allow_skew = bool(host_config.get("allowProtocolSkew", False))
    return merged, allow_skew
