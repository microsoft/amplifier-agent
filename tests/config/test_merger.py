"""Tests for amplifier_agent_lib.config.merger (C1+).

C1 scope: stub merge_config() that returns bundle module configs unchanged
when host_config is None. Per-block per-key shallow merge for non-None host
configs lands in C2/C3/C4.

D5: layered merge of host config over bundle module configs. Bundle's static
config is the base; the four pass-through blocks override per-key. No
translation, no key renaming, no curation -- amplifier-agent only
parameterizes what bundle.md already declares (D4 pass-through stance).
"""

from __future__ import annotations

import copy

import pytest

from amplifier_agent_lib.config import merge_config
from amplifier_agent_lib.protocol.errors import AaaError


def test_merge_config_returns_bundle_unchanged_when_host_is_none() -> None:
    """D5: host_config=None returns bundle modules unchanged with no input mutation."""
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-mcp": {
            "config_path": "/etc/amplifier/mcp.json",
            "verbose_servers": False,
        },
        "hooks-approval": {
            "auto_approve": False,
            "patterns": ["ls *", "cat *"],
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=None)

    # Result equals the input snapshot (semantically unchanged).
    assert result == snapshot
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_config_layers_mcp_block_over_tool_mcp_module() -> None:
    """D4, D5: host.mcp keys override bundle's tool-mcp config per-key (shallow).

    Bundle declares ``tool-mcp`` with three keys; host's ``mcp`` block overrides
    one (``verbose_servers``), adds one new key (``configPath``), and omits the
    remaining two (``server_log_dir``, ``max_content_size``) — for which the
    bundle defaults must stand.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-mcp": {
            "verbose_servers": False,
            "server_log_dir": "/bundle/default",
            "max_content_size": 50000,
        },
    }
    host_config: dict[str, object] = {
        "mcp": {
            "verbose_servers": True,
            "configPath": "/etc/host/mcp.json",
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["tool-mcp"] == {
        "verbose_servers": True,
        "server_log_dir": "/bundle/default",
        "max_content_size": 50000,
        "configPath": "/etc/host/mcp.json",
    }
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_config_layers_approval_block_over_hooks_approval_module() -> None:
    """D4, D5: host.approval keys override bundle's hooks-approval config per-key.

    The shallow per-key overlay means list values (``patterns``) are replaced
    wholesale by the host's list -- there is no array merge. Keys the host omits
    (``default_action``, ``policy_driven_only``) keep the bundle defaults.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "hooks-approval": {
            "patterns": ["rm -rf"],
            "auto_approve": False,
            "default_action": "deny",
            "policy_driven_only": False,
        },
    }
    host_config: dict[str, object] = {
        "approval": {
            "auto_approve": True,
            "patterns": ["sudo", "rm -rf /"],
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["hooks-approval"] == {
        "patterns": ["sudo", "rm -rf /"],
        "auto_approve": True,
        "default_action": "deny",
        "policy_driven_only": False,
    }
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_config_uses_provider_module_field_to_pick_target() -> None:
    """D4, D5: host.provider.module names which provider module receives the config.

    The merger maps the friendly ``provider.module`` name (e.g. ``"anthropic"``)
    to the corresponding bundle module key (``"anthropic-provider"``) and
    overlays ``provider.config`` per-key on top of the bundle's static config
    for that module.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "anthropic-provider": {
            "default_model": "claude-sonnet-4-5",
        },
    }
    host_config: dict[str, object] = {
        "provider": {
            "module": "anthropic",
            "config": {
                "default_model": "claude-opus-4-5",
                "max_tokens": 16000,
            },
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["anthropic-provider"] == {
        "default_model": "claude-opus-4-5",
        "max_tokens": 16000,
    }
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_config_provider_module_required_when_provider_block_present() -> None:
    """D4, D5: missing ``provider.module`` falls through defensively.

    Without a ``module`` field the merger cannot know which provider module the
    config targets, so it leaves the bundle's provider config intact rather than
    guessing. Schema-level enforcement of the ``module`` requirement happens in
    the validator (D7); the merger's job is to be defensive.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "anthropic-provider": {
            "default_model": "claude-sonnet-4-5",
        },
    }
    host_config: dict[str, object] = {
        "provider": {
            "config": {
                "default_model": "claude-opus-4-5",
            },
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    # Bundle config intact -- no module name means no merge target.
    assert result["anthropic-provider"] == {
        "default_model": "claude-sonnet-4-5",
    }
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_config_returns_allow_protocol_skew_flag() -> None:
    """D4: allowProtocolSkew is engine-level, surfaced as a separate return field.

    The merger returns ``(merged_modules, allow_protocol_skew)`` so the engine
    boot path can read the flag without re-parsing the host config dict.
    """
    _modules, allow_skew = merge_config(
        bundle_modules={},
        host_config={"allowProtocolSkew": True},
    )

    assert allow_skew is True


def test_merge_config_skew_defaults_to_false() -> None:
    """D4: allowProtocolSkew defaults to False when the host omits it.

    The engine-level skew flag is opt-in; absence of the key in the host
    config means the engine enforces protocol version compatibility.
    """
    _modules, allow_skew = merge_config(
        bundle_modules={},
        host_config={},
    )

    assert allow_skew is False


def test_merge_skills_list_concatenates_bundle_then_host() -> None:
    """D12: bundle's ``skills.skills`` list comes first; host's list is appended.

    ``skills.skills`` is the host_config sub-key the user pushes new sources
    through; the bundle's curated sources are the floor.  The merge order is
    fixed by D12 -- bundle first, host appended -- so that host_config can
    only **extend** the bundle's catalog, never silently erase it.  Same
    rationale that makes ``allowProtocolSkew`` engine-level: the host can
    opt-in, it cannot opt-out of the bundle's curation.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-skills": {"skills": ["bundle-source-1"]},
    }
    host_config: dict[str, object] = {
        "skills": {"skills": ["host-source-1", "host-source-2"]},
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["tool-skills"]["skills"] == [
        "bundle-source-1",
        "host-source-1",
        "host-source-2",
    ]
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_skills_empty_host_list_preserves_bundle() -> None:
    """D12: an empty host ``skills.skills`` list leaves the bundle list intact.

    ``list(bundle_value or []) + list(host_value)`` with ``host_value == []``
    reduces to the bundle list verbatim.  This is the no-op case for the
    list-concat merge -- the host has nothing to add, the bundle's curated
    floor stands.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-skills": {"skills": ["b1", "b2"]},
    }
    host_config: dict[str, object] = {
        "skills": {"skills": []},
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["tool-skills"]["skills"] == ["b1", "b2"]
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_skills_no_bundle_list_uses_host_only() -> None:
    """D12: a bundle ``tool-skills`` config with no ``skills`` key still gets host additions.

    Mirror of the empty-host case from the other direction: when the bundle
    omits the ``skills`` sub-key, the merged result is the host list alone.
    Bundle-as-floor degenerates to bundle-as-empty-floor; the helper's
    ``bundle_value or []`` normalisation makes this symmetric with the
    empty-host case above.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-skills": {},
    }
    host_config: dict[str, object] = {
        "skills": {"skills": ["h1"]},
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["tool-skills"]["skills"] == ["h1"]
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_skills_visibility_overlays_per_key() -> None:
    """D5: ``skills.visibility`` is a dict-overlay sub-key (shallow per-key).

    Unlike ``skills.skills`` (list-concat, D12), ``skills.visibility`` is a
    dict whose keys the host overrides one-by-one on top of the bundle's
    declared visibility block.  Keys the host does not mention are preserved
    from the bundle; keys the host sets win.  This mirrors the D5 stance
    applied to ``host.mcp``, ``host.approval``, and ``host.provider.config``:
    the bundle is the floor, the host parameterizes per key, never strips
    silently.

    Regression anchor for the visibility branch of ``_merge_skills`` -- the
    bundle declares ``enabled``, ``priority``, and ``inject_role``; the host
    only overrides ``priority``; the merged result keeps ``enabled`` and
    ``inject_role`` from the bundle and takes ``priority`` from the host.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-skills": {
            "visibility": {
                "enabled": True,
                "priority": 20,
                "inject_role": "user",
            },
        },
    }
    host_config: dict[str, object] = {
        "skills": {
            "visibility": {"priority": 10},
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert result["tool-skills"]["visibility"] == {
        "enabled": True,
        "inject_role": "user",
        "priority": 10,
    }
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_skills_block_without_tool_skills_mount_raises() -> None:
    """D7: host declares a skills: block but bundle has no tool-skills mount -> raise.

    The host config layer is pass-through (D4): amplifier-agent only
    parameterizes what bundle.md already declares.  If the host pushes a
    non-empty ``skills:`` block at a bundle that never mounted ``tool-skills``,
    there is no module to parameterize -- the merger must refuse rather than
    silently inventing a ``tool-skills`` config dict that no engine module
    will consume.  Surfaces as ``config_no_matching_module`` (classification
    ``protocol``) so the CLI's error envelope tells the user exactly which
    side to fix.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-todo": {"max_items": 10},
    }
    host_config: dict[str, object] = {
        "skills": {"skills": ["host-source"]},
    }

    with pytest.raises(AaaError) as excinfo:
        merge_config(bundle_modules=bundle_modules, host_config=host_config)

    assert excinfo.value.code == "config_no_matching_module"
    assert excinfo.value.classification == "protocol"


def test_merge_empty_skills_block_without_tool_skills_is_noop() -> None:
    """D7: an empty host ``skills:`` block + no ``tool-skills`` mount is a no-op.

    The boundary case of the D7 rule above: when the host's ``skills:`` block
    is empty (no ``skills`` list, no ``visibility`` overlay), there is
    nothing to push into a module config -- so the absence of a
    ``tool-skills`` mount is not an error.  The merger returns the bundle
    module configs untouched; no ``tool-skills`` entry is silently
    fabricated.  This preserves the symmetry that "empty host block = no-op"
    holds whether or not the corresponding mount is present.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-todo": {"max_items": 10},
    }
    snapshot = copy.deepcopy(bundle_modules)
    host_config: dict[str, object] = {
        "skills": {},
    }

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    # No tool-skills mount entry was fabricated; mount plan unchanged.
    assert "tool-skills" not in result
    assert result == snapshot
    # Input was not mutated.
    assert bundle_modules == snapshot


def test_merge_full_skills_block_against_bundle_defaults() -> None:
    """D11/D12/D5/D7: end-to-end skills block merge — canonical round-trip.

    Regression anchor for the full canonical example from D11: a bundle that
    declares both ``skills`` (list-shaped, D12) and ``visibility`` (dict-shaped,
    D5) sub-keys, plus a host that exercises both merge semantics in a single
    block.  Asserts the two semantics compose:

    * ``skills.skills`` is list-concat (D12): bundle URI floor, then host's
      two paths appended in declared order.  Three elements total, order
      preserved end-to-end.
    * ``skills.visibility`` is shallow per-key dict overlay (D5): bundle's
      four keys (``enabled``, ``inject_role``, ``max_skills_visible``,
      ``ephemeral``) come through untouched; host's single ``priority``
      override wins on its key only.

    This test should PASS with the implementation from Tasks 2.1-2.4 already
    in place; if it fails, ``_merge_skills`` left a gap in the composition
    of the two sub-key semantics that earlier per-sub-key tests didn't
    catch.  No new implementation is expected.
    """
    bundle_modules: dict[str, dict[str, object]] = {
        "tool-skills": {
            "skills": [
                "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
            ],
            "visibility": {
                "enabled": True,
                "inject_role": "user",
                "max_skills_visible": 50,
                "ephemeral": True,
                "priority": 20,
            },
        },
    }
    host_config: dict[str, object] = {
        "skills": {
            "skills": [".amplifier/skills", "~/.amplifier/skills"],
            "visibility": {"priority": 10},
        },
    }
    snapshot = copy.deepcopy(bundle_modules)

    result, _allow_skew = merge_config(bundle_modules=bundle_modules, host_config=host_config)

    # D12: list-concat — bundle URI first, host paths appended in declared order.
    assert result["tool-skills"]["skills"] == [
        "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
        ".amplifier/skills",
        "~/.amplifier/skills",
    ]
    # D5: shallow dict overlay — host overrides ``priority`` only; other four
    # bundle-declared visibility keys pass through untouched.
    assert result["tool-skills"]["visibility"] == {
        "enabled": True,
        "inject_role": "user",
        "max_skills_visible": 50,
        "ephemeral": True,
        "priority": 10,
    }
    # Input was not mutated.
    assert bundle_modules == snapshot
