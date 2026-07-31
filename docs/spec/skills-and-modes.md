# Skills and Modes

## Scope

Covers the skill-invocation sigil, skill and mode discovery, the built-ins shipped in the bundle,
mode resolution and its fail-closed rule, and the `skills list` / `modes list` surfaces. It does not
cover the `skills` block schema in `host_config.json` (see `host-config.md`) or the HTTP routes that
expose these lists (see `http-face.md`).

## The skill sigil

```
!amplifier:skill <name> [args]
```

A prompt whose text, after leading whitespace is stripped, is exactly the sigil or begins with the
sigil followed by a space or a tab is routed deterministically to the skill loader instead of being
handed to the model as text. That is what makes `!amplifier:skill code-review` reliably fire the
skill rather than depending on the model noticing the text and choosing to act on it.

Grammar:

```
"!amplifier:skill"              bare, no name  -> falls through to a normal turn
"!amplifier:skill <name>"       name only,     arguments = ""
"!amplifier:skill <name> <args>"  name = first token after the sigil, arguments = the rest
```

Only the single separator between name and arguments is consumed. Everything after it is preserved
verbatim, internal spacing included. A bare sigil and a non-sigil prompt both run as an ordinary
turn, unchanged.

### The sigil is honored only on a user turn

The sigil is honored only on a human-authored user turn: never against conversation history, never
against host-supplied system or developer messages (including the `<user_provided_instructions>`
containment wrapper), never against assistant text, never against tool results.

Skills execute tools, so this is a privilege boundary: honoring the sigil anywhere in the message
list would let the host, the model, or an upstream tool result invoke a skill. The rule is enforced,
not advisory. Dispatch requires the observed role of the prompt to be exactly `user`; any other
value, including unknown, runs the prompt as ordinary text, and a sigil seen on a non-user turn is
logged and ignored. Unknown role fails closed.

Both faces use the same dispatch path, so a sigil posted over the wire behaves exactly as one typed
on the CLI, and `/v1/skills` never advertises a skill the wire will not honor.

The containment is the inverse of the mode directive: the directive is host-authored and accepted
only from system or developer messages; the sigil is human-authored and accepted only from a user
turn.

### Dispatch outcomes

```
inline skill   the loader substitutes $ARGUMENTS but does not run the body; the body becomes the
               turn's prompt, so the agent follows the skill's instructions
fork skill     the skill already ran in a spawned sub-session; its response becomes the turn reply
               verbatim
```

Every failure path (loader not mounted, loader raised, load unsuccessful, unknown skill name) falls
back to running the original prompt unchanged, with a warning logged. **Skills fail open; modes fail
closed.** A sigil arrives inside the user's own prose, so refusing the turn over something the user
typed would be wrong.

### Sigil re-hydration on resume

When an inline skill dispatches, the expanded body becomes that turn's prompt, and skill bodies
routinely set rules, a persona, or a working contract the rest of the conversation is meant to obey.
On the CLI that survives, because the post-turn context is persisted and `--resume` reads it back.
Over HTTP it would not: the next request carries the client's history, in which the turn is just the
six words of the raw sigil.

So on resume, a replayed sigil in history is replaced with that skill's expanded inline body before
the context is seeded. This is substitution, not dispatch: nothing invokes a skill on behalf of a
history message. It restores the text of a turn the human really did submit and that really did run.

Guards:

```
eligibility   only entries that came from a genuine client role=user message
role re-check the entry's role must still be "user", independently of eligibility
content shape only a plain string, or a list holding exactly one text part
fork skills   never re-hydrated
unknown fork status is treated as fork
```

Multi-part content is excluded rather than guessed at: rewriting the wrong part would put text in
the user's mouth, and a sigil is only ever the whole of a user turn anyway. Fork skills are excluded
because their body never entered this session's context on the original turn either, and the only
way to obtain the body is to load it, which would re-run the sub-session on every subsequent
request. A stale sigil is a degraded answer; a re-run fork is an unrequested side effect, so unknown
status resolves to leaving the raw text in place.

Skills contributed by an active mode are visible to this lookup. Re-hydration never mutates the
caller's history. One skill-load notification is observable per re-hydrated sigil per turn, matching
what the original turn did.

## Skill discovery

One discovery implementation backs both `amplifier-agent skills list` and `GET /v1/skills`.

Roots, in priority order, first match wins:

```
1. the bundle's vendored built-in skills directory
2. .amplifier/skills (relative to the working directory), then ~/.amplifier/skills
3. local directories listed in host_config skills.skills
```

The built-in directory is first, matching the invocation path, so a built-in shadows a same-named
user override on both listing and execution.

Only local, existing directories are usable as roots. Entries starting with `git+` or containing
`://` are skipped, and `~` is expanded. Roots are collapsed by resolved path before walking, so a
directory reached by two different roots (which happens whenever the process runs from the home
directory) does not report every skill as shadowing itself.

Shadowing is reported, never hidden. Each entry carries the winning `source` plus a `shadowed` list
naming every same-named file that lost. The list is always present, empty when there was no
collision.

The user-invocable filter is the `disable-model-invocation` frontmatter flag, evaluated on the
winner (the skill that would actually run). Filtering on `user-invocable` instead would wrongly
surface skills that are model-invocable tools despite carrying that field.

Discovery works without a running session: a bare `skills list` prepares the cached bundle lazily
and once, which resolves and exposes the discovery packages without booting a session. Any stray
output from that preparation is redirected to stderr so a JSON consumer keeps a clean stdout.
Discovery cannot perform that lazy preparation from inside a running event loop, which is why the
HTTP face serves these lists from a snapshot taken at startup rather than discovering per request.

### `$AMPLIFIER_SKILLS_DIR`

The per-spawn adapter bridge. When set, its directory is placed first among the default skill roots
for a turn, and that whole group is appended after the built-ins and the configured sources. An
adapter can therefore provision skills for one spawned instance by setting one environment variable
on the subprocess environment it already owns, with no file management.

It exists alongside the declarative `skills:` config block because the two serve different
audiences: the config block is the persistent surface for host installs whose skill-source set is
static, the environment variable is the per-spawn surface.

**A skill provided only through `$AMPLIFIER_SKILLS_DIR` is invocable but is not listed.** Discovery
does not read the variable, so such a skill runs when invoked but appears in neither
`amplifier-agent skills list` nor `GET /v1/skills`.

For the `skills` config block schema, its two allowed sub-keys, and its concatenation merge
semantics, see `host-config.md`.

## Built-in skills

Eight skills ship with the bundle.

```
code-review            context: fork   disable-model-invocation: true   user-invocable
council                context: fork   disable-model-invocation: true   user-invocable
cranky-old-sam                                                          model-invocable lens
crusty-old-engineer                                                     model-invocable lens
intent-keeper                                                           model-invocable lens
restless-old-brian                                                      model-invocable lens
tester-breaker                                                          model-invocable lens
user-advocate                                                           model-invocable lens
```

`skills list` and `GET /v1/skills` therefore report exactly `code-review` and `council` on a clean
install. The six lenses carry `user-invocable: true` but lack `disable-model-invocation`, so they
are the model-invocable panel `council` fans out to, not slash commands.

The built-in skills directory is contributed to the skill loader as an absolute path, so
first-match-wins deduplication makes the built-ins win regardless of how the install is laid out.

## Mode resolution

One resolution rule serves both faces, so the two cannot drift.

```
mode omitted             -> no restriction, turn runs      (per-turn disable)
mode named, known        -> activate, turn runs
mode named, NOT known    -> REJECT, turn never starts      (client error)
mode named, unverifiable -> REJECT, turn never starts      (server error)
```

Core invariant: the active mode is only ever set to a name that resolved, never to an unverified
one. A turn running with an active mode that no policy backs is worse than one running
unrestricted, because every downstream reader (the envelope's `activeMode`, the mode hook, a host
UI) would believe a mode is in force while nothing is enforced.

### "Unknown" is not "could not verify"

```
discovery ran, name absent from the result  -> the CALLER is wrong  -> unknown mode
discovery could not run at all              -> WE are wrong         -> mode unverifiable
```

The two are never conflated. Reporting broken discovery machinery as "unknown mode: plan" would send
the user hunting for a typo that does not exist. An unknown-mode failure names the alternatives that
were found, and that list may legitimately be empty (discovery ran and found no modes). An
unverifiable failure says the name could not be verified rather than claiming it is wrong.

Any failure of discovery, whatever its cause, means the same thing to a caller (we could not check)
and produces the same unverifiable outcome. That is not a fail-open: it rejects the turn.

### Fail-closed before irreversible action

An unresolved mode errors before any irreversible action. On the CLI the mode is resolved before
`--fresh` deletes the workspace state directory, so a rejected turn leaves state intact; when the
mode does resolve, `--fresh` proceeds normally. On the HTTP face both rejections are raised before
the streaming response is committed, because once the 200 status line is sent the status can no
longer change.

### Error codes

CLI:

```
argv_mode_unknown    exit 2   classification "protocol" (argv-validation family)
                              remediation: "Run `amplifier-agent modes list` to see the available modes."
modes_unavailable    exit 1   classification "engine"
```

Exit 1 and the engine classification for the second, deliberately: telling the user their mode name
is wrong would be a lie, because we do not know that.

HTTP:

```
400  {"error": {"type": "invalid_request_error", "code": "unknown_mode",
                "message": "... Call GET /v1/modes ..."}}
503  {"error": {"type": "server_error", "code": "modes_unavailable",
                "message": "... This is a server-side failure, not an invalid mode name."}}
```

The 503 covers exactly three situations, indistinguishable from the caller's point of view: a
recorded discovery failure at startup, no recorded discovery result at all, and no recorded mode
list at all. An empty mode list with no recorded error is not one of them: discovery ran and found
nothing, so any requested name really is unknown and 400 is correct.

## Mode discovery

Search paths, in priority order:

```
1. <cwd>/.amplifier/modes
2. ~/.amplifier/modes
3. the bundle's vendored built-in modes directory
```

That order mirrors the activation path, so the mode reported here is the mode that actually runs.
Note this is the opposite ordering from skills, where the built-ins win; each matches its own
activation path.

Every `*.md` file in a root is a candidate and its name is the file stem. No user-invocable filter
applies. An unparseable file claims nothing and shadows nothing, so a later file may still claim
that name. Root collapsing and `shadowed` reporting work exactly as for skills.

The built-in modes directory is contributed to the mode hook as an absolute path, so `--mode plan`
and `--mode brainstorm` resolve deterministically. The mode hook is mounted from
`git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/hooks-mode`.

Mode search paths are conventional, not config-driven.

## Built-in modes

Two modes ship with the bundle.

```
plan        Analyze, strategize, and organize - but don't implement
brainstorm  Exploratory design refinement before implementation - explore, question, and propose, but don't build
```

Each carries a `mode:` frontmatter block with `name`, `description`, `shortcut`, and a `tools:`
policy whose `safe:` list is enforced before every tool call.

## Recording the active mode

```
CLI    --mode <name>, per turn, NON-STICKY
HTTP   [amplifier-agent:mode=<name>] directive in a system or developer message (see http-face.md)
```

Both set the same per-turn session state, and only when a mode resolved. Leaving it unset means no
restriction. The mode's tool policy is enforced before every tool call and its body is injected into
the model request for that turn.

Because it is per turn, **omitting `--mode` on a resume disables a previously-set mode.** Re-pass it
each turn to persist.

The value is echoed back verbatim:

```
CLI    envelope metadata.activeMode
HTTP   top-level activeMode on the terminal chunk and on the non-streaming body
```

The field is always written, even when null, including in error envelopes. Present-and-null is what
lets a consumer distinguish "no mode is active" from "this surface does not report modes".

## `skills list` and `modes list`

```
amplifier-agent skills list [--json] [--output auto|json|table] [--config PATH]
amplifier-agent modes  list [--json] [--output auto|json|table]
```

`--json` wins over `--output`. `auto` resolves to `table` on a TTY and `json` when piped or
redirected. `modes list` takes no `--config`, matching mode discovery not being config-driven.

JSON output is a bare list, not a `{"object": "list"}` envelope; that wrapping is HTTP-only.

```json
[
  { "name": "code-review",
    "description": "Review changed code for reuse, quality, and efficiency, then fix any issues found.",
    "source": "/abs/path/to/skills/code-review/SKILL.md",
    "shadowed": [] },
  { "name": "council", "description": "...", "source": "...",
    "shadowed": [ { "source": "/home/me/.amplifier/skills/council/SKILL.md" } ] }
]
```

Sorted by name. `source` is the winning file. `shadowed` is always present.

With JSON output, stdout carries the payload and nothing else. Any stray write from bundle
preparation lands on stderr. A configuration error from `--config` exits 2 with a message on stderr.

Table output is two columns, `NAME` and `DESCRIPTION`. A row with a non-empty `shadowed` list is
marked `(!)` and expanded in a footer naming both the file that runs and every file that lost:

```
NAME         DESCRIPTION
code-review  Review changed code for reuse, quality, and efficiency, then fix any issues found.
council      Convene the persona panel ...  (!)

(!) 1 name conflict. The file under 'runs' is the one that runs:
  council
    runs:     /abs/path/to/bundle/skills/council/SKILL.md
    shadowed: /home/me/.amplifier/skills/council/SKILL.md
```

No footer is printed when nothing collided. `modes list` renders identically.

## Non-goals

- **Skills are not installable via a flag.** There is no `--skills-dir` flag and there must not be.
  Skill path configuration is stable across the life of a host install and does not belong in
  per-turn argv. The mechanisms are the `skills:` config block, the discovery roots, and
  `$AMPLIFIER_SKILLS_DIR`.
- **The host extends but cannot subtract.** `skills.skills` merges by concatenation, bundle sources
  first. There is no mechanism to remove a bundle-declared skill source.
- **Modes are not config-driven.** No `modes:` host-config block and no configurable mode search
  paths. The paths are conventional.
- **No mode stickiness.** `--mode` is per turn. There is no "set mode for the session" command and
  no persisted mode state.
- **Skills do not fail closed.** An unknown skill name in a sigil runs the prompt as ordinary text.
  Only modes reject the turn.
- **`skills list` does not report model-invocable skills.** The filter is
  `disable-model-invocation`, so the six built-in lenses never appear on either listing surface even
  though they are shipped and loadable.
