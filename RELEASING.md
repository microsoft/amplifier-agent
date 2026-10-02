# Releasing

One tag, `v<X.Y.Z>`, releases every package at the same version.

```
amplifier-agent             packages/python       Git install, wheel and sdist on the release
amplifier-agent-engine      packages/engine       Git install, wheel and sdist on the release
amplifier-agent-http        packages/http         Git install, wheel and sdist on the release
amplifier-agent-ts          packages/typescript   npm, and the package archive on the release
```

Python users install from the tag with `uv add ... --tag v<X.Y.Z>`. The binding and the
HTTP face pin their dependencies on this repository to the same tag, so the tag must
exist before those installs resolve.

## Steps

1. Set the version everywhere it appears: package metadata, `__version__`, the
   TypeScript `version`, the Git refs the packages pin, and the install commands in
   `README.md`, `docs/install.md`, and `skills/amplifier-agent/SKILL.md`.

   ```bash
   python scripts/release_version.py set X.Y.Z
   uv lock
   ```

1. Open a PR with the change and merge it to `main`. CI runs
   `scripts/release_version.py check`, which fails if any site disagrees.

1. Tag the merge commit and push the tag:

   ```bash
   git fetch origin
   git tag -a vX.Y.Z -m "amplifier-agent X.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

## What the tag runs

`.github/workflows/release.yml`:

1. Fails unless the tag equals `v` plus the version in every site.
1. Builds the Python wheels and sdists.
1. Builds the production Linux x86-64 runtime on Ubuntu 22.04 (glibc 2.35) and packs
   the TypeScript package with it.
1. Creates the GitHub Release with generated notes and attaches every artifact.
1. Dispatches `.github/workflows/publish-wrapper.yml`, which publishes the release's
   `amplifier-agent-ts` archive to npm with provenance.

npm authenticates through trusted publishing, which names `publish-wrapper.yml` as the
package's workflow; there is no token. To retry the npm publish for a release:

```bash
gh workflow run publish-wrapper.yml -f tag=vX.Y.Z
```

## Verify

```bash
uv init --bare /tmp/release-check && cd /tmp/release-check
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --tag vX.Y.Z
uv run python -c "import amplifier_agent; print(amplifier_agent.__version__)"
```

```bash
mkdir /tmp/release-check-ts && cd /tmp/release-check-ts && npm init -y
npm install amplifier-agent-ts@X.Y.Z
node --input-type=module -e 'import { version } from "@microsoft/amplifier-agent"; console.log(version)'
```
