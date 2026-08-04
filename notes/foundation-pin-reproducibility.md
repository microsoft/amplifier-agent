# The tested artifact is not the shipped artifact

Known gap, deliberately not fixed yet. Recorded so it is not rediscovered.

## The problem

```
pyproject.toml:79-80
  amplifier-foundation = { git = '...', branch = 'main' }
```

`amplifier-foundation` is pinned to a moving branch, not a commit or a tag.

`uv.lock` exists, but the customer install path does not consume it:

```
install.sh:182-184
  uv tool install --reinstall --force \
    --from "git+https://github.com/microsoft/amplifier-agent@$TAG" amplifier-agent
```

`uv tool install --from git+...` resolves dependencies fresh. It does not read
the lockfile committed at that tag.

## What that means

An engine installed from tag `v0.12.0` resolves whatever `amplifier-foundation@main`
happens to be at install time, which may be months after the tag was cut.

Two users installing the same tag on different days can get different code.
The artifact verified before release and the artifact a customer receives are
provably not the same thing.

## Why no CI change closes it

Every gate added in the e2e-first cleanup verifies the tree at a point in time:

```
make verify        lint, types, codegen freshness, wheel contents, version
                   consistency, cross-language wire parity
ci.yml             now runs on tags, so the tag is gated
publish-python.yml runs scripts/verify-wheel.py before upload
install-script.yml installs the real pushed tag and exercises bundle priming
```

All of these are correct and worth having. None of them constrain what
`foundation@main` will be tomorrow. The install-script smoke test comes closest,
but it proves the install worked at that moment, not that it will keep working.

## Shape of a fix

Not decided. The options, roughly:

```
pin at release time    resolve foundation to a commit sha during the release
                       process and commit that pin with the version bump.
                       Reproducible, but adds a step and a coordination burden.

publish foundation     give foundation real versioned releases and depend on a
                       version range instead of a branch. Correct long-term,
                       largest change.

ship the lock          make the install path consume uv.lock. Needs a different
                       install mechanism than `uv tool install --from git+`.
```

This needs a decision about how tightly the two repos should be coupled, which
is an architecture call rather than a CI fix.

## Related

The same class of problem, smaller blast radius:

```
release-notes.yml:38   prerelease: contains(github.ref_name, '-')
```

Every `wrapper-v*` tag contains a hyphen, so every TypeScript wrapper release is
marked prerelease. This is load-bearing by accident: it is what stops wrapper
tags from winning the `releases/latest` lookup that `install.sh:25` depends on.
Fixing it "properly" would break the default install path. Leave it alone
until the install path stops depending on `releases/latest`.
