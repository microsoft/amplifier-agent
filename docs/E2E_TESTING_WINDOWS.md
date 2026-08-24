# E2E Testing (Windows)

End-to-end tests that run the real `amplifier-agent` CLI inside a Windows container,
installed from upstream GitHub. They answer one question: does Windows support work.

This is a separate harness from [`E2E_TESTING.md`](E2E_TESTING.md), not an extension of
it. That suite is the contract suite: it proves the shipped binary honors the spec, runs
in a Linux DTU, and installs from a Gitea mirror of your working tree. This one is a
reasonable approximation. It installs from upstream `main`, runs a handful of cases, and
exists to catch Windows-specific breakage early.

The two share no framework code, and parity between them is an explicit non-goal. A case
here does not imply a matching case there, and neither suite constrains the other.

This document describes the framework. The tests live under
`tests/windows/winsuites/<suite>/` and are the source of truth for what is covered.

## Prerequisites

Microsoft's [Set up your environment for Windows
containers](https://learn.microsoft.com/en-us/virtualization/windowscontainers/quick-start/set-up-environment?tabs=dockerce)
is the authoritative setup guide. The short version follows.

### 1. A Windows host that can run Windows containers

- Windows 10 or 11, **Pro, Enterprise, or Education**. Home will not work, because it
  does not ship Hyper-V.
- Hyper-V enabled.

Windows containers are not Linux containers. They run a Windows kernel and can only run
on a Windows host. There is no cross-compilation story and no way to run them from a
Linux machine.

### 2. Docker Desktop, switched to Windows container mode

Install Docker Desktop on Windows, then right-click the tray icon and choose **Switch to
Windows containers**. Docker Desktop runs two engines side by side and only one is
selected at a time, so this step is easy to miss and produces confusing errors when
skipped.

Verify the engine is the Windows one:

```powershell
docker info --format "{{.OSType}} {{.Isolation}}"
# Expected result: windows hyperv
```

### 3. Confirm the setup with a first container

Follow [Run your first Windows
container](https://learn.microsoft.com/en-us/virtualization/windowscontainers/quick-start/run-your-first-container):

```powershell
docker run --rm mcr.microsoft.com/windows/nanoserver:ltsc2022 cmd /c ver
# Microsoft Windows [Version 10.0.20348.xxxx]
```

If that works, the host is ready. If it does not, fix it before touching this harness.
Nothing here can work around a host that cannot run a Windows container.

### 4. uv

```bash
# https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh
```

The harness entry point is a self-contained uv script and is never installed. It is
always run via `uv run`.

## Running from WSL2

This is the case that trips people up, so it gets its own section.

Docker Desktop exposes three contexts, all over Windows named pipes:

```
default          npipe:////./pipe/docker_engine
desktop-linux    npipe:////./pipe/dockerDesktopLinuxEngine
desktop-windows  npipe:////./pipe/dockerDesktopWindowsEngine
```

Inside WSL2 you almost certainly have a Linux `docker` on your PATH pointing at the Linux
engine. It cannot reach the Windows engine at all, because it has no npipe transport:

```bash
docker -c desktop-windows image ls
# Failed to initialize: protocol not available
```

The Windows engine is reachable from WSL only through the Windows `docker.exe` binary
over WSL interop, with the context named explicitly:

```bash
"/mnt/c/Program Files/Docker/docker.exe" -c desktop-windows info --format "{{.OSType}}"
# windows
```

**The harness handles this for you.** `winframework/container.py` resolves the binary:
`docker` on a native Windows host, `docker.exe` from WSL, falling back to the default
Docker Desktop install path. Every call is pinned to the `desktop-windows`
context. You never invoke docker directly, and you never need to switch your own context.

Two consequences worth internalizing:

- A `docker image ls` you run by hand in WSL shows your **Linux** images. The images this
  harness builds will not appear there. Add `-c desktop-windows` to the Windows
  `docker.exe` to see them.
- WSL integration must be enabled for your distro in Docker Desktop settings, otherwise
  `docker.exe` is not on the WSL PATH. The harness falls back to the default install path
  if so, but enabling integration is cleaner.

Overrides, if your setup is non-standard:

```bash
WIN_E2E_DOCKER_EXE=/mnt/c/path/to/docker.exe
WIN_E2E_DOCKER_CONTEXT=desktop-windows
```

Running natively on Windows needs no changes. `docker` resolves on PATH and everything
else, including the container paths, is identical. Only the WSL path has been exercised
in practice so far.

## Base images

The default is the latest Server Core, currently Windows Server 2025:

```
mcr.microsoft.com/windows/servercore:ltsc2025
```

Use the latest if your host can run it. Otherwise pick the one that suits your system
from [Windows container base
images](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/container-base-images)
and point the harness at it:

```bash
WIN_E2E_BASE_IMAGE=mcr.microsoft.com/windows/servercore:ltsc2022
```

One constraint: the image build runs PowerShell, which Nano Server does not ship. Server
Core or larger.

## What this image does not cover

The image installs MinGit, which provides `git` but no `bash` (verified: no `bash.exe`
anywhere under `C:\tools\git`). [`INSTALL.md`](INSTALL.md) lists Git Bash as a Windows
prereq because the shell tool looks for it, so **the shell tool is out of scope here**.
Covering it would mean the full Git for Windows installer, which is a much heavier image
than a light approximation warrants. A suite that needs the shell tool has to change the
Dockerfile first.

## Layout

```
tests/windows/
  conftest.py                 # pytest fixtures (image, per-suite container, api key)
  winframework/               # the machinery (stable; rarely touched)
    container.py              # docker wrappers, preflight, image build, exec
    harness.py                # WinCase model + the case runner
    assertions.py             # reusable checks (expect_contains, expect_names, ...)
    cli.py                    # the doctor/up/run/down entry point (a uv script)
    provisioning/             # how amplifier-agent gets into the image
      Dockerfile.windows
      host-config.json
  winsuites/                  # the tests (grows per suite)
    smoke/                    # does it run at all (keyless)
    hello/                    # does a real model turn work (needs an API key)
```

The `win` prefix on `winframework` and `winsuites` is load-bearing, not decoration.
`tests/e2e/` already owns top-level packages named `framework` and `suites` via a
`sys.path` insert in its conftest. Since `sys.modules` is global, a bare `pytest tests/`
collecting both trees would resolve `framework` to whichever imported first and fail the
other. Distinct names keep the two suites genuinely independent.

## Running

```bash
# from the amplifier-agent repo root
uv run tests/windows/winframework/cli.py doctor   # check prereqs, change nothing
uv run tests/windows/winframework/cli.py up       # build the provisioned image
uv run tests/windows/winframework/cli.py run      # run all suites
uv run tests/windows/winframework/cli.py down     # remove the provisioned image
```

Or through the Makefile:

```bash
make e2e-windows                  # all suites
make e2e-windows SUITE=smoke      # one suite
```

`doctor` is the first thing to run on a new machine. It reports the resolved docker
binary, uv, the context, the configured base image and agent ref, the engine OSType and
isolation, and whether the image is built. On a bad host it prints the problems and exits
non-zero instead.

`up` builds the image once, and that is the slow part: it installs git, uv, a managed
Python, amplifier-agent from GitHub, and primes the bundle cache. Expect several minutes
cold. Runs afterwards are seconds, not minutes.

### Scoping and flags

```bash
uv run tests/windows/winframework/cli.py run smoke         # only winsuites/smoke
uv run tests/windows/winframework/cli.py run smoke hello   # two suites
uv run tests/windows/winframework/cli.py run --skip-setup  # require an existing image
uv run tests/windows/winframework/cli.py run -k version    # pass args through to pytest
uv run tests/windows/winframework/cli.py up --rebuild      # force a rebuild
```

Suite selection is directory-based, matching the DTU harness: bare words matching a
`winsuites/` subdirectory scope the run, and the first `-` or path-like token ends suite
parsing so the rest passes through to pytest. An unknown suite name fails loud with the
valid list.

Everything self-skips when the Windows engine is unreachable or the image has not been
built, so a plain `uv run pytest` stays green on any host, including Linux CI. As with
the DTU suite, that green is a weak statement: it means the tests skipped, not that they
passed.

## Configuration

All optional, all environment variables:

```bash
WIN_E2E_BASE_IMAGE       # default mcr.microsoft.com/windows/servercore:ltsc2025
WIN_E2E_AGENT_REF        # default main; any git ref of microsoft/amplifier-agent
WIN_E2E_IMAGE            # default amplifier-agent-win-e2e:latest
WIN_E2E_DOCKER_CONTEXT   # default desktop-windows
WIN_E2E_DOCKER_EXE       # explicit path to docker.exe
```

`ANTHROPIC_API_KEY`, when set on the host, is forwarded into the container. The `hello`
suite skips without it.

## The isolation model

The expensive work is the image build. The cheap work is starting a container from it.
So the image is built once and each suite gets a fresh container from that image:

```
up   -> build the provisioned image ONCE
test -> fresh container per SUITE, removed afterwards
```

Per suite rather than per test case: startup is a few seconds, and paying it per case
would make the harness heavy for little gain. Suites cannot contaminate each other, which
is the boundary that matters.

The `agent` fixture is function-scoped and keys containers by suite directory in a
session-scoped dict. It deliberately does **not** use pytest's `package` scope: a
package-scoped fixture defined in a root conftest resolves to the session node, which
yields one container for the entire run rather than one per suite. Verified with
`--setup-show` plus `docker ps` sampling during a run: exactly two containers,
`aa-win-e2e-smoke-<hex>` and `aa-win-e2e-hello-<hex>`, both removed at the end.

## The case model

One kind, deliberately fewer than the DTU harness. No HTTP cases, and no multi-step
cases, because nothing needs them yet:

```python
WinCase("smoke-version", command=["--version"], check=expect_non_empty())
```

`command` is argv **without** the leading `amplifier-agent`. The baseline criterion is
the same as the DTU harness: exit 0. A case with `check=None` asserts only that the
command ran clean. Output quality is out of scope and belongs to evaluations.

## Adding a suite

1. Create `tests/windows/winsuites/<name>/` with an `__init__.py`.
2. Put `WinCase` data in `cases.py`.
3. Add `test_<name>.py` parametrizing over it, with `pytestmark = pytest.mark.windows`.

Nothing in `winframework/` needs to change. The suite becomes selectable immediately,
because suite discovery reads the filesystem.

If the suite needs a live model, request the `anthropic_key` fixture so it skips cleanly
without one. Skip rather than fail: an absent key is a fact about the operator's machine,
not a defect in Windows support.

## Troubleshooting

- `Failed to initialize: protocol not available`: you used the Linux `docker` from WSL.
  Use the Windows `docker.exe`, or just let the harness resolve it.
- `OSType='linux', expected 'windows'`: Docker Desktop is in Linux container mode.
  Right-click the tray icon, **Switch to Windows containers**.
- `isolation is 'process', expected 'hyperv'`: the base image build must then match the
  host build exactly. Either match the tag to your host or enable Hyper-V isolation.
- Tests skip with `not built`: run `... cli.py up` first, or just use `run`, which
  builds if needed. The skip is driven by whether the image exists, so it cannot
  disagree with reality.
- Image build fails at the `uv tool install` step with `Filename too long`: git long
  paths are not in effect. The Dockerfile enables them, as
  [`INSTALL.md`](INSTALL.md) requires on Windows; confirm that step ran.
- Stale containers after an interrupted run: the fixture removes its own containers, but
  a hard kill can leave one. They are named `aa-win-e2e-<suite>-<hex>`.
- Disk pressure: the base image and the provisioned image are about 12 GB combined.
  `... cli.py down` removes the provisioned one; the base image stays for the next build.
