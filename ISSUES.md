# Known Issues

Deferred work that needs attention but is not blocking the current development line.
Each issue has a status, a summary, a concrete reproducer, and everything needed
to re-open and complete the work in a future session.

---

## ISSUE-001 — hooks-approval not wired end-to-end for headless mode

**Status:** Deferred — temporarily unmounted from default bundle.

**Summary:**
`hooks-approval` (upstream: <https://github.com/microsoft/amplifier-module-hooks-approval>)
is an **opt-in** hook per its own
[USAGE_GUIDE.md](https://github.com/microsoft/amplifier-module-hooks-approval/blob/main/USAGE_GUIDE.md):

> *"Backward compatible — Tools work without approval hook."*

The module is no longer in `src/amplifier_agent_lib/bundle/bundle.md`.
When it is eventually re-mounted, all five gaps below must be closed first.

---

### Why it was unmounted

Two prior fix attempts exposed a **dual-system impedance mismatch** and were both
reverted before merge:

| Attempt | What it tried | What went wrong |
|---------|---------------|-----------------|
| PR #38 | Propagate `"approval.request"` capability from parent → child via the coordinator capability registry | Registry propagation is correct for the *wire-protocol surface*, but `ApprovalHook` reads `self.provider` (an instance attribute set via `register_provider()`), not the registry — so every command outside `DEFAULT_RULES` still auto-denied |
| Bridge fix (reverted, same commit as this unmount) | Push `WireApprovalProvider` into `ApprovalHook.self.provider` via `approval.register_provider` capability | Connected the systems, but `WireApprovalProvider.request_approval` returns a plain `dict` (wire-envelope shape); the hook calls `.approved` on the return value and crashed: `'dict' object has no attribute 'approved'` |

Three failed fix attempts is the signal to stop patching symptoms and fix the
architecture. The unmount is the correct short-term outcome.

---

### What is needed to wire it properly

Five concrete pieces, in the order they should be built:

**1. `AutoApprovalProvider` class**

A new class that takes `mode: Literal["yes", "no"]` and returns
`ApprovalResponse(approved=True/False)` immediately, without any wire-protocol
round-trip. Currently only `WireApprovalProvider` exists, and it is designed
for the JSON-RPC host-callback path — it is the wrong abstraction for
headless auto-approve/deny modes.

```python
from amplifier_core import ApprovalProvider, ApprovalRequest, ApprovalResponse

class AutoApprovalProvider(ApprovalProvider):
    def __init__(self, mode: Literal["yes", "no"]) -> None:
        self._approved = mode == "yes"

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(
            approved=self._approved,
            reason="Auto-approved (mode=yes)" if self._approved else "Auto-denied (mode=no)",
        )
```

**2. `WireApprovalProvider.request_approval` return type fix**

`WireApprovalProvider.request_approval` currently returns a raw `TypedDict`
(wire-envelope shape). It must be updated to return `ApprovalResponse(**wire_dict)`
so that any code path that calls it through the hook contract gets the correct
Pydantic object.

**3. Mode-aware provider factory in `_runtime.py`**

The current code unconditionally wraps `ctx.approval` in `WireApprovalProvider`
for the wire-protocol surface, but never selects a provider for the hook.
`_runtime.py` must choose the right provider at mount time:

| `approval.mode` | TTY? | Host capability? | Provider to register |
|-----------------|------|------------------|----------------------|
| `yes`           | any  | any              | `AutoApprovalProvider("yes")` |
| `no`            | any  | any              | `AutoApprovalProvider("no")` |
| `prompt`        | yes  | any              | `CliApprovalProvider` (interactive TTY) |
| `prompt`        | no   | `approval` cap present | `WireApprovalProvider` |
| `prompt`        | no   | no host approval  | fail-closed: `AutoApprovalProvider("no")` |

This factory should call `coordinator.get_capability("approval.register_provider")`
and pass the selected provider, exactly as the USAGE_GUIDE shows.

**4. Host-side wire responder (paperclip / `amplifier-agent-ts`)**

The engine emits `approval/request` JSON-RPC envelopes over stdout, but the
`amplifier-agent-ts` TypeScript wrapper currently has no handler that receives
these inbound requests from the engine and routes them to the VS Code extension
or host UI. Without this, `WireApprovalProvider` (used in `prompt` mode with a
remote host) will time out waiting for a response that never arrives.

This is a host-side concern: `paperclip` or `amplifier-agent-ts` needs new
infrastructure to receive `approval/request` notifications and either:
(a) surface them in the extension UI, or
(b) auto-respond based on extension settings.

**5. End-to-end tests for each (mode × environment) combination**

| Scenario | Expected outcome |
|----------|-----------------|
| `mode=yes`, no host, headless | `AutoApprovalProvider("yes")` → bash runs, no denial logged |
| `mode=no`, no host, headless | `AutoApprovalProvider("no")` → bash denied, `approval.denied` event emitted |
| `mode=prompt`, TTY present | `CliApprovalProvider` → user sees prompt |
| `mode=prompt`, no TTY, host wire | `WireApprovalProvider` → `approval/request` envelope sent, host responds |

---

### Reproducer

When re-wiring, this command should produce **zero** `No approval provider` or
denial log lines:

```bash
mkdir -p ~/amp-cwd-test
env -i PATH=$PATH HOME=$HOME USER=$USER LANG=$LANG TERM=$TERM TMPDIR=$TMPDIR \
  ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  amplifier-agent run --session-id approval-verify --fresh \
    --cwd ~/amp-cwd-test --output json --protocol-version 0.3.0 -y \
    "Use the bash tool to run 'cat /etc/hostname'." < /dev/null \
    2>&1 | grep -iE 'approval|denied'
```

Today (hooks-approval unmounted): empty output — bash runs unchecked (backward
compatible per USAGE_GUIDE).

After re-wire with `mode=yes`: empty output — `AutoApprovalProvider` returns
`approved=True`; the tool runs identically from the user's perspective, but
audit events are now emitted by the hook.

---

### Reference material

- USAGE_GUIDE: <https://github.com/microsoft/amplifier-module-hooks-approval/blob/main/USAGE_GUIDE.md>
- `ApprovalResponse` schema: `from amplifier_core import ApprovalResponse` — Pydantic `BaseModel`
  with fields `approved: bool`, `reason: str | None`, `remember: bool`.
- The `approval` config forwarding is still in place in `config/merger.py:168-171`; the
  `host_config["approval"]["mode"]` key is preserved and flows to `merged["hooks-approval"]`
  (which is currently unused). When the hook is re-mounted, it will pick up this config.
- PR #38 capability-inheritance loop in `spawn.py` is still present; it propagates the
  wire-protocol `"approval.request"` capability to child sessions and remains correct
  scaffolding for when the wire provider path is fully wired.
- Unmount commit: `<this PR>` (the commit that introduced this file).
