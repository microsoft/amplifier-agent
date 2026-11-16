# Smart tools

Smart tools are standalone command-line tools that carry their own AI capability: the
agent states an intent, and the tool does the work with its own provider settings. The
[Amplifier Smart Tools](https://github.com/microsoft/amplifier-smart-tools) project
defines them, and its [catalog](https://github.com/microsoft/amplifier-smart-tools-catalog)
lists the published ones.

## What every agent has

`smart_tools` is on by default. Every agent then has:

```text
amplifier-smart-tools skill   offered by load_skill beside the skills in skills; how to
                              find, install, update, and create smart tools
system prompt section         what a smart tool is, that installed tools arrive as
                              skills, and to read <tool> --help before first use;
                              also when instructions is set
```

A skill in `skills` named `amplifier-smart-tools` takes the default's place. Delegated
work gets the same section, and the skill when its tools include `load_skill`.

Amplifier Agent installs no smart tool and does not look for installed ones. An installed
tool reaches the model only through its skill; see
[making an agent aware of a smart tool](#making-an-agent-aware-of-a-smart-tool).

Smart tools need a shell tool, such as `bash`, to run them, and web access to find and
install them. The skill and the section are added whatever the [tool set](tools.md) is.
`load_skill` is a tool call like any other and needs an [approval](approvals.md). The
[HTTP face](../http/limits.md#approvals) counts the skill as a tool.

## Turning it off

Off, the agent has no `amplifier-smart-tools` skill, no section, and fetches nothing.
[Skills](skills.md) in `skills` are unaffected.

```python
AgentOptions(smart_tools=False)
```

```ts
await createAgent({ smartTools: false });
```

```json
{ "smart_tools": false }
```

```bash
export AMPLIFIER_AGENT_SMART_TOOLS=false
```

`AgentOptions` wins, then the environment, then the file; see
[configuration](../configuration.md#smart-tools).

## The skill's copies

Each release bundles the skill from
[microsoft/amplifier-smart-tools](https://github.com/microsoft/amplifier-smart-tools).
When an agent is built, it refreshes a copy from the repository's `main` branch in the
background, at most once a day, into the
[sessions directory](../configuration.md#sessions-directory). Building the agent never
waits for it.

A session uses the refreshed copy present when it starts, else the bundled one. Offline,
or when a refresh fails, the last good copy stays in use and the next agent tries again.

## Nested agents

A smart tool that builds its own Amplifier Agent reads the same host configuration and
`AMPLIFIER_AGENT_*` variables, so its agent has the skill and section too, unless the
tool passes `smart_tools=False`.

## Making an agent aware of a smart tool

A tool the application installs is invisible to the agent until its skill is in
`skills`. For each tool the agent should delegate to:

1. Install the tool with the commands in its `SMART_TOOL.md` or README.
1. Add the tool's skill to `skills`, pinned to the same tag as the install, so the skill
   matches the tool. Most tools ship one at `skills/<name>/` in their repository:

   ```python
   AgentOptions(
       skills=[
           "git+https://github.com/microsoft/amplifier-smart-tool-reality-check@v0.9.1#subdirectory=skills/reality-check"
       ]
   )
   ```

   A local path to a copy of the skill also works.

### Installing it for one application

To keep a tool out of the user's own environment, install it into a directory the
application owns, then put a directory that holds only the tool's executable on the
agent's `PATH` through [`environment`](agents.md#environment). Keep other programs out of
that directory, such as a virtual environment's `python`, so nothing shadows the user's
programs. Each tool's own install instructions name its installer.

Most smart tools are Python packages installed with uv. `UV_TOOL_DIR` and
`UV_TOOL_BIN_DIR` move the install into the application's directory, and the bin
directory then holds only the tools' entry points:

```bash
export UV_TOOL_DIR="$APP_HOME/smart-tools/tools"
export UV_TOOL_BIN_DIR="$APP_HOME/smart-tools/bin"
uv tool install "reality-check[amplifier-agent] @ git+https://github.com/microsoft/amplifier-smart-tool-reality-check@v0.9.1"
"$UV_TOOL_BIN_DIR/reality-check" install
```

For another installer, use its option for an install prefix, or link the tool's
executable into the application's bin directory. Then add that directory to `PATH`:

```python
AgentOptions(environment={"PATH": f"{app_home}/smart-tools/bin{os.pathsep}{os.environ['PATH']}"})
```

```ts
await createAgent({
  environment: { PATH: `${appHome}/smart-tools/bin${path.delimiter}${process.env.PATH}` },
});
```

The packaged [HTTP](../http/quickstart.md) and [ACP](../acp/quickstart.md) executables
take no `AgentOptions`: set `PATH` in the face process's environment, and the skill in
`AMPLIFIER_AGENT_SKILLS` or `"skills"` in the [config file](../configuration.md#skills).

### When a tool has no skill

Create one, or the agent will not know the tool exists. A smart tool's skill is small,
because `<tool> --help` prints the tool's full instructions. It carries:

```text
name          the tool's name
description   which work to delegate to the tool
body          how to install it, and "run <tool> --help and follow it"
```

The [repository skill rules](https://github.com/microsoft/amplifier-smart-tools/blob/main/spec/invocation.md#shipping-an-agent-skill-alongside)
in the Smart Tools spec say what goes in it.
[smart-tool-creator](https://github.com/microsoft/amplifier-smart-tool-creator) builds
smart tools and checks them against the spec.
