# force-exit-bundle

**Conformance-only.** Do not load this bundle outside the conformance suite.

This bundle exists for a single test fixture:
`wrappers/conformance/fixtures/mode-a-envelope-precedence.yaml` (SC-D).

It installs one `on_turn_end` hook that calls `os._exit(1)` immediately
after the amplifier-agent engine has written and flushed a valid §4.1
envelope to stdout. The fixture uses this to exercise the wrapper's
envelope-precedence rule (Mode A pivot amendment §4.4): when a valid
envelope is present, the wrapper yields based on the envelope, regardless
of the engine's exit code.

## Layout

```
force-exit-bundle/
├── bundle.yaml          # registers the on_turn_end hook
├── hooks/
│   ├── __init__.py      # marker so `hooks.force_exit` resolves
│   └── force_exit.py    # the os._exit(1) call
└── README.md            # this file
```

## Why `os._exit(1)` and not `sys.exit(1)`?

`sys.exit(1)` raises `SystemExit`, which gives `atexit` handlers,
`finally` blocks, and the engine's normal shutdown path a chance to run.
That defeats the purpose of this fixture — SC-D models an *abrupt*
post-flush crash (e.g. a native-extension finalizer or a segfault during
interpreter teardown), not a graceful shutdown. `os._exit(1)` is the
finalizer-skipping kernel-level `_exit(2)` syscall wrapper that matches
the production failure mode.

## Why the 1ms sleep in `force_exit.py`?

Belt-and-suspenders insurance against hypothetical future hook-dispatcher
refactors that could invoke `on_turn_end` concurrently with the
envelope-flush. The engine's stdout-discipline contract (CR-B) already
guarantees ordering, so the sleep is not load-bearing on the current
engine. See `hooks/force_exit.py` docstring for the full rationale.

## Do not extend

This bundle has a deliberately empty surface area beyond the single
`on_turn_end` hook. Do not add agents, providers, tools, or additional
hooks. If you find yourself wanting to add something, you almost
certainly want a new fixture-specific bundle instead.
