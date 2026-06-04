"""spawn.py — session.spawn capability for amplifier-agent.

Implements in-process sub-agent spawning so the ``delegate`` tool can create
child ``AmplifierSession`` instances inside the parent engine's process.

Design references
-----------------
* §8 of aaa-v2-design-checkpoint.md: spawn is LIBRARY-INTERNAL — no adapter
  override surface, no spawn_fn parameter on any public API.
* OpenClaw's CLISpawnManager precedent (amplifier-app-openclaw/src/spawn.py).
* amplifier-app-cli's session_spawner.py (lines 199-700) for the reference
  merge + lifecycle pattern.

MVP scope (Phase 4)
--------------------
Implements:
  - hydrate_agent_overlay   — parse agent .md file → overlay dict
  - merge_configs           — deep-merge parent config with agent overlay
  - spawn_sub_session       — create, run, and clean up a child session

Explicitly OUT OF SCOPE for this MVP (noted with comments):
  - Recursive session.spawn on child coordinator (grandchild delegation will
    fail with the delegate tool's own clear error message; that is acceptable)
  - Cost bridging (bridge_child_cost)
  - Display nesting (push_nesting / pop_nesting)
  - Provider preference plumbing beyond simple config inclusion
  - session.resume capability (only spawn, not resume)
  - Hook for capturing status / turn_count / metadata from the child session
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "hydrate_agent_overlay",
    "merge_configs",
    "spawn_sub_session",
]


# ---------------------------------------------------------------------------
# hydrate_agent_overlay
# ---------------------------------------------------------------------------


def hydrate_agent_overlay(agent_md_path: Path) -> dict[str, Any]:
    """Parse an agent markdown file into an overlay config dict.

    The file format is::

        ---
        meta:
          name: explorer
        model_role: [research, general]
        tools:
          - module: tool-bash
            source: git+https://...
        ---

        # Explorer

        You map workspace slices …

    The YAML frontmatter (between the ``---`` delimiters) is parsed for
    ``tools``, ``hooks``, ``model_role``, and ``meta``.  The markdown body
    (everything after the second ``---``) becomes the agent's system
    instruction (``instruction`` key).

    Args:
        agent_md_path: Absolute path to the agent's ``.md`` file.

    Returns:
        Overlay dict with at least ``instruction`` and the frontmatter fields
        present in the file.  Keys: ``instruction`` (str), ``tools`` (list),
        ``hooks`` (list, optional), ``model_role`` (str | list), ``meta``
        (dict, optional).
    """
    import yaml

    text = agent_md_path.read_text(encoding="utf-8")

    # Split on the YAML frontmatter delimiters: ---\nYAML\n---
    if not text.startswith("---"):
        # No frontmatter — treat entire file as system instruction.
        return {"instruction": text.strip()}

    # Find the closing '---' (searching from position 3 to skip the opening)
    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        # Malformed — treat entire file as system instruction.
        return {"instruction": text.strip()}

    frontmatter_text = text[3:end_idx].strip()
    body = text[end_idx + 4 :].strip()  # +4 to skip '\n---'

    frontmatter: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}

    overlay: dict[str, Any] = {}

    # Markdown body → system instruction
    overlay["instruction"] = body

    # Tools list (required for agent to function)
    if "tools" in frontmatter:
        overlay["tools"] = frontmatter["tools"]

    # Hooks list (optional; most agents don't declare hooks)
    if "hooks" in frontmatter:
        overlay["hooks"] = frontmatter["hooks"]

    # Model role for provider routing (e.g. "coding", ["research", "general"])
    if "model_role" in frontmatter:
        overlay["model_role"] = frontmatter["model_role"]

    # Meta (name, description) — carried for identification / logging
    if "meta" in frontmatter:
        overlay["meta"] = frontmatter["meta"]

    return overlay


# ---------------------------------------------------------------------------
# Config merge helpers (ported from amplifier-app-openclaw/src/spawn.py)
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two dicts.  *overlay* wins on conflicts; arrays are replaced."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merge_module_lists(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge module lists by ``module`` key.  Overlay configs deep-merge."""
    base_by_key: dict[str, dict[str, Any]] = {}
    for item in base:
        key = item.get("module")
        if key:
            base_by_key[key] = item.copy()
    for item in overlay:
        key = item.get("module")
        if key and key in base_by_key:
            base_by_key[key] = _deep_merge(base_by_key[key], item)
        elif key:
            base_by_key[key] = item.copy()
    return list(base_by_key.values())


def _merge_agent_dicts(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge *child* into *parent*.  Module lists merge by module ID."""
    merged = parent.copy()
    for key, child_value in child.items():
        if key not in merged:
            merged[key] = child_value
        elif key in ("hooks", "tools", "providers"):
            merged[key] = _merge_module_lists(
                merged.get(key) or [],
                child_value,  # type: ignore[arg-type]
            )
        elif isinstance(child_value, dict) and isinstance(merged[key], dict):
            merged[key] = _deep_merge(merged[key], child_value)
        else:
            merged[key] = child_value
    return merged


def _apply_spawn_tool_policy(parent: dict[str, Any]) -> dict[str, Any]:
    """Filter parent tools per ``spawn.exclude_tools`` / ``spawn.tools``."""
    spawn_config = parent.get("spawn", {})
    if not spawn_config:
        return parent

    filtered = parent.copy()
    parent_tools = parent.get("tools", [])

    # Explicit spawn.tools replaces inheritance entirely
    if "tools" in spawn_config:
        spawn_tools = spawn_config["tools"]
        if isinstance(spawn_tools, list):
            filtered["tools"] = spawn_tools
        return filtered

    # Blocklist mode
    exclude = spawn_config.get("exclude_tools", [])
    if exclude and isinstance(exclude, list):
        filtered["tools"] = [t for t in parent_tools if t.get("module") not in exclude]
    return filtered


def _filter_tools(
    config: dict[str, Any],
    policy: dict[str, list[str]],
) -> dict[str, Any]:
    """Filter tools per inheritance policy (exclude list or allow list)."""
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude = policy.get("exclude_tools", [])
    inherit = policy.get("inherit_tools")

    if inherit is not None:
        filtered = [t for t in tools if t.get("module") in inherit]
    elif exclude:
        filtered = [t for t in tools if t.get("module") not in exclude]
    else:
        return config

    return {**config, "tools": filtered}


def _filter_hooks(
    config: dict[str, Any],
    policy: dict[str, list[str]],
) -> dict[str, Any]:
    """Filter hooks per inheritance policy (exclude list or allow list)."""
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude = policy.get("exclude_hooks", [])
    inherit = policy.get("inherit_hooks")

    if inherit is not None:
        filtered = [h for h in hooks if h.get("module") in inherit]
    elif exclude:
        filtered = [h for h in hooks if h.get("module") not in exclude]
    else:
        return config

    return {**config, "hooks": filtered}


def merge_configs(
    parent_config: dict[str, Any],
    agent_overlay: dict[str, Any],
) -> dict[str, Any]:
    """Deep merge parent session config with agent overlay.

    Merge policy (mirrors amplifier-app-cli's agent_config.merge_configs):

    * Module lists (``tools``, ``hooks``, ``providers``) — merged by ``module``
      ID; overlay entries deep-merge into matching parent entries; new entries
      are appended.
    * Nested dicts — recursively deep-merged (overlay wins on scalar conflict).
    * ``instruction`` — passed through the merge unchanged; ``spawn_sub_session``
      reads it back to inject as the child's system message.
    * ``agents`` Smart Single Value — ``"none"`` disables all sub-delegation;
      a list of names filters to only those agents; ``"all"`` or absent means
      inherit the full parent agent dict.

    Args:
        parent_config: Parent session's complete mount plan (``session.config``).
        agent_overlay:  Agent's partial config overlay (from ``hydrate_agent_overlay``).

    Returns:
        Merged config dict suitable for passing to ``AmplifierSession``.
    """
    filtered_parent = _apply_spawn_tool_policy(parent_config)

    overlay_copy = agent_overlay.copy()
    agent_filter = overlay_copy.pop("agents", None)

    result = _merge_agent_dicts(filtered_parent, overlay_copy)

    # Smart Single Value for agent access control
    if agent_filter == "none":
        result["agents"] = {}
    elif isinstance(agent_filter, list):
        parent_agents = parent_config.get("agents", {})
        result["agents"] = {k: v for k, v in parent_agents.items() if k in agent_filter}

    return result


# ---------------------------------------------------------------------------
# spawn_sub_session
# ---------------------------------------------------------------------------


async def spawn_sub_session(**kwargs: Any) -> dict[str, Any]:
    """Spawn a child ``AmplifierSession`` for agent delegation.

    This is the function registered as ``session.spawn`` on the parent
    session's coordinator so the ``delegate`` tool can invoke it.

    The kwarg shape exactly matches what ``tool-delegate`` passes (see
    ``amplifier_module_tool_delegate/__init__.py``, line 1072)::

        spawn_fn(
            agent_name=...,
            instruction=...,
            parent_session=...,
            agent_configs=...,
            sub_session_id=...,
            tool_inheritance=...,
            hook_inheritance=...,
            orchestrator_config=...,
            provider_preferences=...,   # unused in MVP — out of scope
            self_delegation_depth=...,  # carried but not used in MVP
            session_metadata=...,
        )

    MVP out-of-scope items (marked with # MVP-SKIP comments):
    - Recursive session.spawn on child coordinator (grandchild delegation fails
      with delegate tool's own error message — acceptable for MVP)
    - Cost bridging (bridge_child_cost)
    - Display nesting (push_nesting / pop_nesting)
    - Provider preference plumbing beyond config inclusion

    Args:
        **kwargs: See kwarg shape above.

    Returns:
        Dict with keys: ``output`` (str), ``session_id`` (str),
        ``status`` (str), ``turn_count`` (int), ``metadata`` (dict).

    Raises:
        ValueError: If ``agent_name`` is not in ``agent_configs`` and is not
            ``"self"``.
    """
    from amplifier_core import AmplifierSession
    from amplifier_foundation import generate_sub_session_id

    # -- Unpack kwargs (matches tool-delegate calling convention) --------
    agent_name: str = kwargs["agent_name"]
    instruction: str = kwargs["instruction"]
    parent_session = kwargs["parent_session"]
    agent_configs: dict[str, dict[str, Any]] = kwargs.get("agent_configs") or {}
    sub_session_id: str | None = kwargs.get("sub_session_id")
    tool_inheritance: dict[str, Any] = kwargs.get("tool_inheritance") or {}
    hook_inheritance: dict[str, Any] = kwargs.get("hook_inheritance") or {}
    orchestrator_config: dict[str, Any] | None = kwargs.get("orchestrator_config")
    session_metadata: dict[str, Any] | None = kwargs.get("session_metadata")
    # provider_preferences — MVP-SKIP: out of scope; routing hook handles this
    # self_delegation_depth — MVP-SKIP: carried through but not used in child

    # -- Resolve agent config -------------------------------------------
    if agent_name == "self":
        # Self-delegation: use parent config unchanged (no overlay)
        agent_overlay: dict[str, Any] = {}
        logger.debug("Self-delegation: using parent config without agent overlay")
    elif agent_name not in agent_configs:
        available = list(agent_configs.keys())
        raise ValueError(f"Agent '{agent_name}' not found in configuration. Available agents: {available}")
    else:
        agent_overlay = dict(agent_configs[agent_name])

    # Extract system instruction BEFORE merge so we can inject it after init
    system_instruction: str = str(agent_overlay.get("instruction", ""))

    # -- Merge parent config with agent overlay -------------------------
    merged_config = merge_configs(parent_session.config, agent_overlay)

    # -- Apply tool inheritance filtering -------------------------------
    if tool_inheritance and "tools" in merged_config:
        merged_config = _filter_tools(merged_config, tool_inheritance)

    # -- Apply hook inheritance filtering -------------------------------
    if hook_inheritance and "hooks" in merged_config:
        merged_config = _filter_hooks(merged_config, hook_inheritance)

    # -- Apply orchestrator config override -----------------------------
    if orchestrator_config:
        session_cfg: dict[str, Any] = merged_config.setdefault("session", {})
        orch_cfg: dict[str, Any] = session_cfg.setdefault("orchestrator", {})
        if isinstance(orch_cfg, dict):
            orch_inner: dict[str, Any] = orch_cfg.setdefault("config", {})
            orch_inner.update(orchestrator_config)

    # -- Inject session metadata ----------------------------------------
    if session_metadata:
        session_cfg2: dict[str, Any] = merged_config.setdefault("session", {})
        session_cfg2["metadata"] = session_metadata

    # -- Generate child session ID --------------------------------------
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )
    assert sub_session_id is not None

    # -- Create child session ------------------------------------------
    # Inherit approval and display systems from parent where available.
    # Fall back to None — AmplifierSession uses defaults in that case.
    approval_system = getattr(parent_session.coordinator, "approval_system", None)
    display_system = getattr(parent_session.coordinator, "display_system", None)

    child_session = AmplifierSession(
        merged_config,
        session_id=sub_session_id,
        parent_id=parent_session.session_id,
        approval_system=approval_system,
        display_system=display_system,
    )

    # -- Inherit module-source-resolver BEFORE initialize() -----------
    # This is critical: modules with 'source: git+https://...' directives
    # are resolved by this resolver during initialize().  Inheriting from
    # the parent ensures the child can load its tool modules without
    # additional network / git-clone operations.
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)

    # -- Share parent sys.path additions --------------------------------
    # Bundle packages (e.g. tool modules' own Python packages) must remain
    # importable inside the child process.  Copy any extra paths the parent
    # added to sys.path.
    paths_to_share: list[str] = []
    if hasattr(parent_session, "loader") and parent_session.loader is not None:
        paths_to_share.extend(getattr(parent_session.loader, "_added_paths", []))
    bundle_paths = parent_session.coordinator.get_capability("bundle_package_paths")
    if bundle_paths:
        paths_to_share.extend(bundle_paths)
    for p in paths_to_share:
        if p not in sys.path:
            sys.path.insert(0, p)

    # -- Initialize child session (loads all modules) ------------------
    await child_session.initialize()

    # -- Inherit capability-registry entries from parent ---------------
    # The parent registers approval.request and display.emit via
    # coordinator.register_capability(...) in _runtime.py (the capability
    # registry).  The getattr reads above target a separate Rust-backed
    # storage slot (coordinator.approval_system / display_system property)
    # that register_capability does NOT populate, so they always return
    # None for parents created by amplifier-agent.  Without this loop,
    # the child receives approval_system=None and hooks-approval auto-
    # denies every side-effecting tool call in delegate sub-sessions.
    # Run after child_session.initialize() so the registry exists, and
    # before the child executes its first turn.
    for cap_key in ("approval.request", "display.emit"):
        parent_cap = parent_session.coordinator.get_capability(cap_key)
        if parent_cap is not None:
            child_session.coordinator.register_capability(cap_key, parent_cap)

    # -- Inject agent system prompt into context manager ---------------
    # The agent's markdown body is the system instruction that defines
    # the child agent's persona and constraints.  We inject it after
    # initialize() because the context module is only available then.
    if system_instruction:
        context_manager = child_session.coordinator.get("context")
        if context_manager is not None:
            if hasattr(context_manager, "set_system_prompt_factory"):
                # Preferred: dynamic factory (context-simple supports this)
                _captured_prompt = system_instruction

                async def _system_factory() -> str:
                    return _captured_prompt

                await context_manager.set_system_prompt_factory(_system_factory)
            elif hasattr(context_manager, "add_message"):
                # Fallback: inject as a static system message
                await context_manager.add_message({"role": "system", "content": system_instruction})

    # -- Inherit mention resolver from parent --------------------------
    # MVP-SKIP: mention_resolver inheritance deferred; vendored agents do not
    # use @-mention bundle references so this is safe to skip for MVP.
    parent_mention = parent_session.coordinator.get_capability("mention_resolver")
    if parent_mention:
        child_session.coordinator.register_capability("mention_resolver", parent_mention)

    # -- Propagate display.emit capability and mount streaming hook --------
    # The streaming hook is mounted programmatically (not via bundle.md) per
    # the 2026-05-20 fix for the 'source: local' URI handler bug.  Child
    # sessions need the same hook so delegate-spawned agents emit display
    # events.  We inherit the parent's display.emit capability directly since
    # display.emit is a coordinator capability, not a session attribute.
    from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook

    parent_display_emit = parent_session.coordinator.get_capability("display.emit")
    if parent_display_emit:
        child_session.coordinator.register_capability("display.emit", parent_display_emit)
    await mount_streaming_hook(child_session.coordinator, {})

    # -- Execute the task and clean up ---------------------------------
    try:
        response = await child_session.execute(instruction)
    finally:
        await child_session.cleanup()

    logger.debug(
        "Child session %s completed for agent '%s'",
        sub_session_id,
        agent_name,
    )

    return {
        "output": response,
        "session_id": sub_session_id,
        "status": "success",
        # MVP-SKIP: turn_count and metadata are placeholders; hook-based
        # capture is out of scope for this MVP.
        "turn_count": 1,
        "metadata": {},
    }
