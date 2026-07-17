---
name: echo-args
description: Writes the exact text it received as arguments, verbatim, to a file. Used to verify skill argument passthrough.
user-invocable: true
---

You have been given some argument text. Do exactly this and nothing else:

Write the EXACT text you received as $ARGUMENTS, verbatim, with no extra
characters, no quoting, no commentary, and no trailing explanation, into the file
`/workspace/args_out.txt`.

The file must contain only that text. Then stop.
