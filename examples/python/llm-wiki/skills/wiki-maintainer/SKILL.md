---
name: wiki-maintainer
description: Merge proposals into a concept or entity page, fix lint findings, and write lint reports for the Amplifier ecosystem wiki. Load before writing any wiki page other than a repository page.
---

# Wiki maintainer

## Merging proposals into a page

1. Read the current page when it exists. Keep every claim that still has a citation.
2. Add each proposal where it belongs. Merge proposals that say the same thing into one claim with all their
   citations.
3. A `contradiction` proposal goes into `## Contradictions` with the claim it disagrees with. Keep both citations.
4. A `link` proposal becomes a link in `## Related` or in the sentence it supports. Fill `## Related` from the
   link proposals, related pages in the index, and the pages that link here. Leave it out when nothing is related;
   never write a placeholder such as "None yet".
5. Rewrite the summary in the frontmatter when the page now says more.
6. Write the whole page in one `write_file` call.

## Concept page

```markdown
# Title

What it is, in two or three sentences.

## How it works
## Where it appears        one bullet per repository page, with what that repository does with it
## Related                 links to other concept and entity pages; left out when there are none
## Contradictions          only when there are any
```

## Entity page

```markdown
# Name

What it is and which repository publishes it.

## Use
## Interfaces              the configuration keys, commands, or functions a person calls
## Related                 left out when there are none
## Contradictions
```

## Fixing lint findings

```
Broken link       point it at the right page, or remove the link and keep the text
Missing page      link the closest page that exists, or remove the link
No sources        add citations from the pages this one links to, or from raw/ when you read the file
Stale citation    read the file at the current SHA and update the claim and its citation
```

## Lint report

```markdown
# Lint report YYYY-MM-DD

## Contradictions
## Missing concept pages
## Broken and missing links
## Stale or missing citations
## Suggested questions
```

Each finding names the page as a relative link, states the problem in one sentence, and gives one fix. Write "none"
under a heading with no findings.
