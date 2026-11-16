---
smart_tool_format: 1
name: word-tally
version: 0.2.0
author: Example Team
description: >
  Counts the words in a piece of text, the same way every time. Use it when an exact word
  count matters, such as checking a summary against a length limit.
use_cases:
  - Check that a summary stays under a word limit
  - Compare the length of two drafts
platforms:
  - linux
  - macos
---

## When to reach for it

Exact word counts for text you already have. It never calls a model.

## Worked invocation

```bash
word-tally count --text "one two three"
```
