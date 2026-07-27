---
name: code-review
description: SHADOW-WS-COPY. Launch-directory override of the built-in code-review skill. If this description ever appears in a listing, the built-in LOST and precedence changed.
disable-model-invocation: true
user-invocable: true
---

# code-review (launch-dir override attempt)

A deliberate name collision with the vendored built-in `code-review` skill, used only by the
amplifier-agent e2e shadow-reporting tests. The built-in root is searched FIRST, so this file is
expected to lose and to be reported under the built-in entry's `shadowed` list.

It intentionally does nothing: it is never meant to run.
