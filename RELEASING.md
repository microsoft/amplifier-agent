# Releasing amplifier-agent

This repo contains three independently-versioned artifacts, each with its own release path.

| Artifact | Package name | Tag | Published to |
|---|---|---|---|
| Python engine + CLI | `amplifier-agent` | `v<version>` | PyPI (OIDC, `publish-python.yml`) |
| Python wrapper SDK | `amplifier-agent-py` | `py-v<version>` | PyPI (OIDC, `publish-python.yml`) |
| TypeScript wrapper SDK | `amplifier-agent-ts` | `wrapper-v<version>` | npm (OIDC, `publish-wrapper.yml`) |

GitHub Releases are auto-created by `release-notes.yml` for `v*` and `wrapper-v*` tags.

---

## Engine release (`amplifier-agent`, PyPI)

```bash
# 1. Bump version in the root pyproject.toml
#    Edit [project] version = "X.Y.Z"

# 2. Commit and merge to main
git add pyproject.toml
git commit -m "chore: bump amplifier-agent to X.Y.Z"
# PR + merge

# 3. Push the release tag from the tip of main
git fetch origin
git checkout main && git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers:
- `publish-python.yml` (job `publish-engine`) — builds and publishes `amplifier-agent` to PyPI
- `release-notes.yml` — creates a GitHub Release with generated changelog

---

## Python wrapper release (`amplifier-agent-py`, PyPI)

```bash
# 1. Bump version in wrappers/python-py/pyproject.toml
#    Edit [project] version = "X.Y.Z"

# 2. Commit and merge to main
git add wrappers/python-py/pyproject.toml
git commit -m "chore: bump amplifier-agent-py to X.Y.Z"
# PR + merge

# 3. Push the wrapper release tag
git fetch origin
git checkout main && git pull
git tag py-vX.Y.Z
git push origin py-vX.Y.Z
```

This triggers `publish-python.yml` (job `publish-wrapper`) — builds and publishes
`amplifier-agent-py` to PyPI.

---

## TypeScript wrapper release (`amplifier-agent-ts`, npm)

```bash
# See wrappers/typescript/package.json for the version field.
git tag wrapper-vX.Y.Z
git push origin wrapper-vX.Y.Z
```

Triggers `publish-wrapper.yml` → npm OIDC publish.

---

## One-time setup: PyPI trusted publishers

Before the **first** PyPI release of each package, configure a *pending trusted publisher*
on PyPI. This only needs to be done once per package.

### amplifier-agent (engine)

At <https://pypi.org/manage/account/publishing/> add a pending publisher:

| Field | Value |
|---|---|
| PyPI project name | `amplifier-agent` |
| GitHub repository owner | `microsoft` |
| GitHub repository name | `amplifier-agent` |
| Workflow filename | `publish-python.yml` |
| Environment name | `pypi` |

### amplifier-agent-py (Python wrapper)

At <https://pypi.org/manage/account/publishing/> add a second pending publisher:

| Field | Value |
|---|---|
| PyPI project name | `amplifier-agent-py` |
| GitHub repository owner | `microsoft` |
| GitHub repository name | `amplifier-agent` |
| Workflow filename | `publish-python.yml` |
| Environment name | `pypi` |

### GitHub Actions environment

Create an environment named `pypi` in repo settings
(`Settings → Environments → New environment`). No secrets are needed.
Optional: add a required reviewer for an extra approval gate.

> **Note:** The OIDC trusted-publisher handshake can only be proven by a real tag-triggered
> run after PyPI-side configuration. No local test can fully verify this step.

---

## Cross-component version coordination

When bumping the protocol version, see the **Cross-component invariants** section in
[`AGENTS.md`](AGENTS.md) — protocol bumps require coordinated wrapper updates and must
land in one PR.
