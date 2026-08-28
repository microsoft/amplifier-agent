# Skills

A skill is packaged knowledge or a packaged procedure the agent picks up when a
task calls for it. Instructions that would otherwise bloat every prompt live in a
skill and cost nothing until they are used.

```
skills/
  code-review/
    SKILL.md          name, description, and the body
    checklist.md      optional companion files
```

## Why they are not just longer instructions

Skills load in three stages, and only the first is always present.

- **Name and description**, roughly a hundred tokens per skill, is what the agent
  sees when deciding whether a skill applies.
- **The body**, one to five thousand tokens, loads only when the agent decides it
  applies.
- **Companion files** cost nothing until the agent reads one.

So a hundred skills is a hundred descriptions, not a hundred bodies. That is the
whole reason the mechanism exists: expertise available at the moment of need
rather than resident in every prompt.

## Configuration

```python
@dataclass(frozen=True)
class SkillsConfig:
    sources: list[str] = field(default_factory=list)
    show_catalog: bool = False
    max_catalog_entries: int = 50
```

- **`sources`** are additional places to find skills. Each entry is a git URL, a
  local directory path, or a bundle reference.
- **`show_catalog`** puts the name and description of every discovered skill into
  the model's context so it can choose skills itself.
- **`max_catalog_entries`** caps that catalog.

```python
AgentConfig(
    provider=ProviderConfig(name="anthropic"),
    skills=SkillsConfig(
        sources=[
            "git+https://github.com/my-org/team-skills@main#subdirectory=skills",
            "/srv/skills",
        ],
    ),
)
```

`sources` extends the built-in set. It does not replace it, and there is no way
to remove a built-in skill through configuration.

## Invoking a skill

With `show_catalog=False`, the default, skills are invoked explicitly. Start a
prompt with the skill sigil and it routes straight to the loader instead of to
the model:

```python
await session.run("!amplifier:skill code-review src/parser.py")
```

The sigil is honored only at the start of a user prompt. Anywhere else it is
ordinary text, because a skill executes tools and tool execution should not be
triggerable by something the model wrote.

With `show_catalog=True`, the agent sees the catalog and loads skills on its own
when it judges one relevant.

## Skills fail open

An unknown skill name, or a loader that errors, does not fail the turn. The
prompt runs as ordinary text instead.

A mistyped skill name costs you the skill, not the turn.

## Discovery and shadowing

Skills are found in order, and the first match for a given name wins:

```
1. built-in skills
2. sources, in the order you listed them
3. .amplifier/skills relative to the working directory
4. ~/.amplifier/skills
```

A name defined in more than one place is shadowed rather than merged. The winner
runs and the losers are reported alongside it, so a skill quietly overriding
another is visible instead of mysterious.

One consequence worth knowing: a source that is a remote URL is invocable but
does not appear in listings, because listing does not fetch.

## Listing

```python
async def list_skills(self) -> list[SkillInfo]: ...


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    source: str
    shadowed: list[str]
```

`list_skills` is on `Agent` rather than module-level, because what is discoverable
depends on `SkillsConfig.sources` and there is nothing to list until an agent
resolves them.

```python
for skill in await agent.list_skills():
    print(skill.name, skill.description)
    for loser in skill.shadowed:
        print("  shadowed:", loser)
```

- **`source`** is where the winning definition was found.
- **`shadowed`** is every other place the same name was defined, in discovery
  order, and is empty when there was no collision.

Listing reads only each skill's name and description, never a body or a
companion file. A remote source is not fetched, so skills reachable only through
one are invocable but absent here.

`shadowed` is on the listing rather than in a log because a skill quietly
overriding another is the failure worth seeing, and it is only visible when
something puts the winner and the losers side by side.

## Forked skills

A skill whose frontmatter declares `context: fork` runs as an isolated
sub-session rather than loading into the current conversation. It gets its own
context window, does its work, and returns only its result.

That keeps a large piece of work from consuming the conversation it was invoked
from. From your side nothing changes: it is still a tool call in the event
stream, and its activity arrives as ordinary tool events.

## Events

Skill activity reaches you as `tool/call` and `tool/result` for the loader, like
any other tool. There is no separate skill event type in the
[registry](events.md).
