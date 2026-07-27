---
name: code-review
description: SHADOW-HOME-COPY. ~/.amplifier override of the built-in code-review skill. If this description ever appears in a listing, the built-in LOST and precedence changed.
disable-model-invocation: true
user-invocable: true
---

# code-review (home override attempt)

A deliberate name collision with the vendored built-in `code-review` skill, used only by the
amplifier-agent e2e shadow-reporting tests. The built-in root is searched FIRST, so this file is
expected to lose and to be reported under the built-in entry's `shadowed` list.

Seeded at `~/.amplifier/skills/code-review/SKILL.md` so the collision does not depend on the
process working directory, which is what makes it observable over HTTP.
