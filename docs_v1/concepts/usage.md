# Usage

Cumulative snapshots, grouped by the model that actually ran.

```
Usage       { entries: [UsageEntry...] }

UsageEntry  { provider, model,
              tokens_in, tokens_out,
              cache_read_tokens, cache_write_tokens,
              cost? }
```

The four counters are exact integers. An absent value means unknown, not zero.

## Cost

```
cost { "USD": "0.0142" }
```

Keyed by ISO 4217, valued as decimal strings, never rounded through binary floating
point. Each language exposes its most faithful native decimal type.

An absent `cost` means the provider did not make it knowable.

## Cadence

Each `usage` event replaces the one before it. The last one follows all work and comes
before `terminal`, and `terminal.usage` equals it exactly.

Every selection actually used appears, including work you did not name. See
[models](models.md).

## Truthfulness

A counter is accurate or it is absent. A field wired to a constant teaches every reader
to stop reading it, which costs more than the missing number ever would.
