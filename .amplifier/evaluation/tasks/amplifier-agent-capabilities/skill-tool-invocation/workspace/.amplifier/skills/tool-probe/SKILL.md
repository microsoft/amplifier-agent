---
name: tool-probe
description: Writes a fixed sentinel to a file to prove the agent invoked this skill through its skills tool in response to a natural-language request. Invoke this skill when asked to run or invoke tool-probe.
---

You have been invoked as the tool-probe skill. Do exactly this and nothing else:

Write the exact text `TOOL-INVOKE-OK-QX83` (and nothing else, no quoting, no
commentary, no trailing explanation) into the file
`/workspace/tool_invoke_ran.txt`.

The file must contain only that text. Then stop.
