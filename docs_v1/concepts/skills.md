# Skills

Skills add reusable instructions and approved commands. Configure `AgentOptions.skills`
with local directories or Git source locations, as described in [tools](tools.md#built-in-tools-and-skills).
The `load_skill` tool lists the discovered names and accepts a name and optional
`arguments` string.

## Named agents

A source can include skill and agent files together:

```text
review-kit/
  skills/review/SKILL.md
  agents/reviewer.md
```

Configure `skills` with `review-kit` or `review-kit/skills`. A skill-local `agents/`
directory is also supported. The skill file selects its agent:

```markdown
---
name: review
description: Review a file for clarity.
context: fork
agent: reviewer
allowed-tools: [read_file, bash]
---
Review the file named in $ARGUMENTS. Explain suggested changes without editing it.
```

The agent file supplies instructions and inherited tool names:

```markdown
---
meta:
  name: reviewer
tools: [read_file, bash]
model_role: general
---
Check terminology, ambiguity, and missing examples. Keep findings concise.
```

The child's instructions include the host's configured instructions followed by the
named agent's instructions. Its tool set is the intersection of the parent's tools,
the agent declaration, and the skill's `allowed-tools`. Omitted restrictions inherit;
an empty list permits no tools. Preprocessing and hooks obey the same restrictions.
Model selections stay within the parent's provider and ceiling.
For inline skills, `allowed-tools` narrows available tools for the rest of the current
turn. The next turn restores the agent's tool set.

An unqualified name must resolve uniquely, except that a skill-local definition takes
precedence. Use `source-name:reviewer` to disambiguate sources. The source name comes
from `bundle.name` in its `bundle.md`, or from the source directory name. Unknown or
ambiguous names and unsupported composition fields are refused during construction.

`agent: self` inherits the parent instructions. Named agents require `context: fork`.
A forked skill cannot load another forked skill. Agent definitions may restrict further
delegation with `agents: none`, `agents: all`, or a list of allowed agent names.

## Command hooks

Add hooks to a skill's frontmatter:

```yaml
hooks:
  PreToolUse:
    - matcher: read_file
      hooks:
        - type: command
          command: 'python3 "$AMPLIFIER_SKILL_DIR/check.py"'
          timeout: 5
```

Supported events are `PreToolUse`, `PostToolUse`, and `Stop`. The shell-list form
`hooks: {shell: [{event: pre-tool, command: ...}]}` also accepts `post-tool` and
`stop`. A matcher is a tool-name regular expression; omit it or use `"*"` to match
all tools. Timeouts are whole seconds from 1 to 120, defaulting to 30.

Each command produces a separate built-in `bash` call, approval, and result. Approving
`load_skill` does not approve its commands. Hook commands do not trigger hooks
recursively. `bash` must be in every applicable tool restriction.

Commands run in the skill's directory with the agent's captured environment.
`AMPLIFIER_SKILL_DIR` and `CLAUDE_SKILL_DIR` identify that directory. JSON arrives on
stdin, rather than through shell interpolation:

```json
{
  "hook_event_name": "PreToolUse",
  "cwd": "/work/project",
  "tool_name": "read_file",
  "tool_input": {"file_path": "README.md"}
}
```

`cwd` identifies the agent's working directory for the requested tool. Post-tool input
also includes `tool_response`. Stop hooks run after successful model/tool work;
cancellation starts no new hook work.

Plain stdout or JSON `hookSpecificOutput.additionalContext` supplies context for later
model requests in that turn. A nonzero exit, malformed result, `continue: false`,
`decision: block`, or `permissionDecision: deny` fails the turn with `tool_failed`.
An allowing hook cannot override host approval. A timed-out command reports an unknown
outcome because an effect may already have happened.

Guard errors remain terminal even with `tool_error_policy="continue"`. After an
unknown outcome elsewhere in the turn, recovery cannot start a hook command or
bypass it to perform guarded work.

Inline hooks activate when the skill loads and expire at the end of the turn. Fork
hooks apply within the child task. For an inline skill with hooks, `auto-load: true`
activates hooks for each admitted turn, without running commands at construction.
Use a YAML boolean; quoted booleans and automatic fork activation are refused.
Session-start/end hooks
and prompt hooks are refused: they have no supported approved execution point in
this skill lifecycle. Use the supported command events for executable work.
