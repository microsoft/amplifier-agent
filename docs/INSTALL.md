# Install

`amplifier-agent` is a Python tool installed with [`uv`](https://docs.astral.sh/uv/). The installer resolves the latest tagged release and installs from it.

**Prerequisites:** `uv`, `curl`, and `git`. The installer tells you exactly what to install if any is missing. It will not bootstrap them silently.

`git` is needed at run time, not just at install time: bundles and modules are fetched by cloning git repositories, so a machine without `git` on `PATH` can neither prime the cache nor mount a bundle. On Windows, installing [Git for Windows](https://git-scm.com/download/win) satisfies this and also provides the `bash` that the shell tool looks for.

```bash
# Linux, MacOS
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash

# Windows requires Git, Git Bash, and git long paths enabled
git config --global core.longpaths true
# Fill in C:\<Path To Git> with where your Git is installed (just bash.exe may launch WSL instead)
& "C:\<Path To Git>\Git\bin\bash.exe" -lc "curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash"
```

Installs the latest released version and primes the bundle cache so your first run is instant.

## Review the script first

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh -o install.sh
less install.sh
bash install.sh
```

## Pin a specific version

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --tag v0.12.0
```

Available tags: <https://github.com/microsoft/amplifier-agent/releases>

## Installer flags

| Flag | Default | Behavior |
|---|---|---|
| `--tag <ref>` | (latest release) | Install a specific tag, branch, or commit |
| `--no-prime` | (prime) | Skip the bundle cache priming step |
| `--yes` | (interactive) | Skip the confirmation prompt (for CI/automation) |
| `--help` | | Print usage |

## Manual install (no script)

```bash
# Resolve the latest release tag
TAG=$(curl -fsSL https://api.github.com/repos/microsoft/amplifier-agent/releases/latest \
    | grep -m1 '"tag_name":' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')

# Install
uv tool install --from "git+https://github.com/microsoft/amplifier-agent@${TAG}" amplifier-agent

# Prime the bundle cache (optional but recommended)
amplifier-agent-post-install
```

Without `amplifier-agent-post-install`, your first `amplifier-agent run` will pause roughly 30 to 60 seconds while bundle modules are fetched. The priming step moves that delay to install time.

## Verify

```bash
amplifier-agent doctor     # environment, providers, paths, bundle cache
amplifier-agent verify     # install integrity and hook coverage
amplifier-agent version    # engine version and wire protocol version
```

## Update

```bash
amplifier-agent update
```

Resolves the latest release and reinstalls if your version is behind. The bundle cache is re-primed automatically; there is no separate step.

## Uninstall

```bash
uv tool uninstall amplifier-agent
rm -rf ~/.amplifier-agent
```

## Installing for a host application

Hosts that spawn `amplifier-agent` as a subprocess inherit the invoking user's `PATH`. Install as **the same user that will run the host process**. Installing as a different user (for example `root` while the service runs unprivileged) leaves the binary undiscoverable at spawn time.

For CI and container images, use `--yes` to skip the confirmation prompt and run the priming step during the image build so the cache is baked in:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --yes --tag v0.12.0
```

## Language SDKs

The SDKs are separate packages and do not include the engine. You need both.

```bash
npm install amplifier-agent-ts        # Node.js 20+
uv add amplifier-agent-py             # or: pip install amplifier-agent-py
```

Both are released independently of the engine, the TypeScript wrapper under `wrapper-v*` tags and the Python wrapper under `py-v*`. See the [integration guide](INTEGRATION.md).

## Related

- Normative distribution contract: [`spec/install-and-distribution.md`](spec/install-and-distribution.md)
- Bundle cache internals: [`spec/bundle-and-cache.md`](spec/bundle-and-cache.md)
