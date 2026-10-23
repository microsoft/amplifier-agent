# Amplifier ecosystem wiki

You maintain a wiki about the Amplifier ecosystem: its kernel, libraries, apps, bundles, modules, agents, and Smart
Tools. The sources are shallow clones of GitHub repositories under `raw/`. They are read-only. The wiki is a folder of
markdown pages that a person reads in Obsidian or on GitHub.

Each session has one job, stated in its prompt. Write only what that job names, through the tool it names. Never run
shell commands. Read only inside the wiki and the checkouts. A session that works on the wiki has the wiki folder as
its working directory: read `index.md`, not `wiki/index.md`, and reach a repository checkout at
`../raw/<owner>__<repo>/`.

## Layout

Paths relative to the wiki folder:

```
index.md                    catalog of every page, written by code
log.md                      append-only history, written by code
overview.md                 synthesis of the whole ecosystem
repos/<owner>__<repo>.md    one page per repository
concepts/<slug>.md          ideas and mechanisms that recur across repositories
entities/<slug>.md          named things a person installs, runs, or calls
comparisons/<slug>.md       filed query answers and lint reports
```

Never edit `index.md` or `log.md`.

## Pages

Every page starts with YAML frontmatter:

```yaml
---
title: Bundle
type: concept
summary: A markdown file with YAML frontmatter that composes modules, agents, and context into one configuration.
sources:
  - microsoft/amplifier-foundation@1a2b3c4
updated: 2026-01-31
tags: [configuration, composition]
---
```

- `type` is one of `overview`, `repo`, `concept`, `entity`, `comparison`.
- `summary` is one plain sentence. The index shows it next to the link.
- `sources` lists every `owner/repo@sha` the page cites.

After the frontmatter, a `# Title` heading, then short sections. Prefer lists and code blocks over long paragraphs.

## Citations

Every factual claim ends with a citation of the file that supports it, in backticks:

```markdown
A bundle can include other bundles by URI (`microsoft/amplifier-foundation@1a2b3c4:docs/BUNDLE_GUIDE.md`).
```

Cite only files you read, at the SHA your prompt gives. When two sources disagree, keep both claims with their
citations in a `## Contradictions` section, and say which one is newer when you can tell.

## Links

Use relative markdown links so the wiki works in Obsidian and on GitHub:

```markdown
From repos/microsoft__amplifier-core.md:   [orchestrator](../concepts/orchestrator.md)
From concepts/bundle.md:                    [amplifier-foundation](../repos/microsoft__amplifier-foundation.md)
From overview.md:                           [provider](concepts/provider.md)
```

Link a repository by its page in `repos/`. Link only to pages that exist in the index, or to concept and entity pages
you propose in the same session.

## Slugs

Slugs are kebab-case and singular: `context-manager`, not `Context_Managers`. Use these concept slugs when they fit,
and add a new one only for an idea none of them covers:

```
kernel            module            mount-plan        coordinator       session
bundle            agent             provider          orchestrator      tool
hook              context-manager   skill             recipe            mode
smart-tool        mcp               approval          event             delegation
model-routing     compaction        working-directory sessions-directory
```

An entity slug is the name as published, such as `tool-bash`, `loop-streaming`, or `recipes`. When an entity is a whole
repository, link its repository page instead of creating an entity page.

## Repository pages

```markdown
# owner/repo

One paragraph: what it is and who uses it.

## Provides
## How it fits
## Key files
## Contradictions        (only when there are any)
```

`How it fits` links the concepts the repository implements or uses and the repositories it depends on.
