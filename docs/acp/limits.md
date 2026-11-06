# What this face cannot carry

These are the limits of the ACP face. Embed a binding for the rest.

## Tools your own process runs

A caller tool is a function in the caller's process. The editor is not a process the face can call into, so there are
no caller tools. Built-in and MCP tools are unaffected, including MCP servers the editor passes in `session/new`. See
[tools](../concepts/tools.md).

## Live tool output

A shell command's output arrives once, when the call ends. See [shell output](reference.md#tool-calls).

## Reasoning and tool calls in load replay

`session/load` replays each turn's input and reply. Reasoning, tool calls, and diffs of earlier turns are not replayed.

## Usage

The editor sees how full the context is and the session's cost. Per-model usage and token counts per turn are not
carried. See [usage](../concepts/usage.md).

## Per-session configuration

A session chooses its working directory, additional directories, MCP servers, and model. Instructions, skills, the
tool set, approvals, and the sessions directory are settings the face was started with, the same for every session;
see [configuration](../configuration.md).

## Unsaved editor buffers

Tools read files from disk. Changes the editor has not saved are not seen.

## Late approvals

A permission request the editor answers after the binding's
[deadline](../concepts/approvals.md#when-the-answer-is-not-allow) has no effect; the turn has already failed
`approval_timeout`.

## The session list

`session/list` is not served. An editor reopens a session it already knows by id with `session/load` or
`session/resume`. Listing sessions lives in [sessions](../concepts/sessions.md), behind a binding.

## Other content and methods

Audio, SSE MCP servers, binary resources other than images, and session fork are not carried. `initialize` advertises
neither audio nor SSE; see [initialize](reference.md#initialize).
