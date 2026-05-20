# Cache Serialization Strategy for PreparedBundle

**Date:** 2026-05-18  
**Status:** Decided  
**Decided by:** Empirical spike (task-2-empirical-spike-pickle)

---

## Question

Should the bundle preparation cache store the `PreparedBundle` as a pickled blob, or should it store only the composed bundle source and re-run `prepare(install_deps=False)` on each load?

Two candidate strategies:

| Strategy | Description | Estimated warm load |
|----------|-------------|---------------------|
| `pickle` | Serialize `pickle.dumps(prepared)` to disk; deserialize on load | ~10 ms (single unpickle) |
| `source` | Store composed bundle source; re-run `prepare(install_deps=False)` on each load | ~100–500 ms (module activation without network/install) |

---

## Evidence

**Spike test:** `tests/spike_test_prepared_pickle.py` (now deleted — one-time decision artifact)  
**Command run:** `uv run pytest tests/spike_test_prepared_pickle.py -v -s`

**Outcome: PASS**

```
tests/spike_test_prepared_pickle.py::test_prepared_bundle_is_picklable PASSED

1 passed in 0.10s
```

The spike performed the following steps:
1. Loaded the vendored `bundle.md` via `amplifier_foundation.load_bundle(str(BUNDLE_MD))`
2. Called `bundle.prepare(install_deps=True)` to obtain a `PreparedBundle`
3. Called `pickle.dumps(prepared)` — succeeded without error
4. Called `pickle.loads(data)` — succeeded without error
5. Asserted `revived.mount_plan == prepared.mount_plan` — **assertion passed**

`PreparedBundle` is a `@dataclass` with fields `mount_plan`, `resolver`, `bundle`, and
`bundle_package_paths`. The pickle round-trip preserves the `mount_plan` exactly.

---

## Decision

**STRATEGY = `pickle`**

Cache the `PreparedBundle` as `pickle.dumps(prepared)`. Deserialize with `pickle.loads(data)` on each warm load.

---

## Rationale

- The empirical spike confirmed that `PreparedBundle` survives a pickle round-trip with `mount_plan` integrity intact.
- The `pickle` strategy yields the cheapest warm load (~10 ms for a single deserialize vs. ~100–500 ms to re-activate modules).
- The `source` strategy would require re-running `prepare(install_deps=False)` on every process start, which still activates modules and does filesystem resolution — significantly slower than a direct deserialize.
- No evidence of unpicklable internals (no `PicklingError`, `TypeError`, or `AttributeError` was raised).

---

## Implementation Notes

The rest of Phase 4 uses **STRATEGY = `'pickle'`**. Tasks 4–7 follow the `[pickle]` branch:

- **Cache writer:** serialize with `pickle.dumps(prepared)` and write to the cache path.
- **Cache reader:** read the blob and deserialize with `pickle.loads(data)`.
- **Cache invalidation:** the cache key is derived from the bundle source content hash; any edit to `bundle.md` busts the cache automatically.
- **Fallback:** if deserialization raises any exception (e.g., version mismatch after an upgrade), fall back to a fresh `prepare(install_deps=False)` call and overwrite the cache.

Wherever tasks below reference `<STRATEGY>`, substitute `'pickle'`.
