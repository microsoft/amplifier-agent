"""Convert the official SWE-bench Pro Dockerfiles into a DTU profile.

The base + instance Dockerfiles are self-contained (git clone + inline heredoc
build scripts, no COPY from build context), so they translate almost directly
into DTU ``provision.setup_cmds``:

  RUN <body>   -> a setup_cmd (prefixed with the current WORKDIR and any ENV
                  exports accumulated so far, mirroring Docker's semantics)
  ENV K=V      -> accumulated and exported before subsequent RUNs
  WORKDIR d    -> tracked and applied as `cd d` on subsequent RUNs
  FROM ref     -> mapped to an Incus base image (+ a small preamble to make the
                  Incus base resemble the upstream Docker base)
  COPY/ADD     -> refused (these instances have none; fail loud if that changes)

The Incus DTU engine only accepts `images:`/`local:` bases and cannot pull a
Docker Hub image directly, which is why we reconstruct the environment from the
Dockerfile steps rather than running the prebuilt image.

Copy-adapted verbatim from the proven prior-art swe_bench_pro package.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

import yaml


class ConversionError(RuntimeError):
    """Raised when a Dockerfile uses a construct we deliberately refuse to guess at."""


@dataclass
class BaseMapping:
    """An Incus base image plus preamble cmds to match the upstream Docker base.

    ``env`` carries environment the *upstream Docker base image* sets in its own
    image metadata (not in the instance Dockerfile) but that the build + grade
    steps rely on -- e.g. the golang image puts ``/usr/local/go/bin`` on PATH and
    sets GOPATH, and the node image puts node/npm on PATH. Because we reconstruct
    from the Dockerfile text only, that image-level ENV would otherwise be lost,
    so we re-inject it here for every RUN and at grade time.

    ``readiness_cmd`` is a toolchain sanity check for the environment's primary
    language (e.g. ``python --version``, ``go version``, ``node --version``). It
    must be language-appropriate: the old hardcoded python check fails on
    node/go bases. ``None`` means only the repo-present check is used.
    """

    image: str
    preamble: list[str] = field(default_factory=list)
    env: list[tuple[str, str]] = field(default_factory=list)
    readiness_cmd: str | None = "python --version"
    # Override the default container memory when a base's test runner needs more
    # (e.g. jest spawns parallel jsdom workers that OOM element-web at 6GiB).
    memory: str | None = None
    # (regex, replacement) pairs applied to each RUN body. Used when the Incus
    # base differs enough from the Docker base that a few OS-specific package
    # names in the base Dockerfile need rewriting (e.g. Ubuntu's versioned
    # `python3.9` apt packages -> Debian's unversioned `python3`). The goal is a
    # fair, equivalent environment, not a byte-identical image.
    run_substitutions: list[tuple[str, str]] = field(default_factory=list)


# Preamble shared by Debian-based bases that ship neither python nor pip (needed
# because the official grader's parser.py is python, and many build.sh call pip).
_DEBIAN_PYTHON_PREAMBLE = [
    "apt-get update && apt-get install -y --no-install-recommends "
    "python3 python3-pip python3-venv python3-dev python-is-python3 ca-certificates curl git "
    "&& rm -rf /var/lib/apt/lists/*",
    # Docker's python image is not PEP-668 externally-managed; Debian is.
    "rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED || true",
    'command -v pip >/dev/null 2>&1 || ln -s "$(command -v pip3)" /usr/local/bin/pip',
]

# The go toolchain the golang:1.24 image ships; installed into /usr/local/go so
# the image-level PATH/GOPATH we inject (see below) find it. GOTOOLCHAIN is left
# at its default (auto) so a go.mod `toolchain` directive can fetch the exact
# patch release over the network (the DTU has external passthrough).
_GO_VERSION = "1.24.4"
_GO_PREAMBLE = [
    "apt-get update && apt-get install -y --no-install-recommends "
    "ca-certificates curl git build-essential "
    "&& rm -rf /var/lib/apt/lists/*",
    f"curl -fsSL https://go.dev/dl/go{_GO_VERSION}.linux-amd64.tar.gz -o /tmp/go.tgz "
    "&& rm -rf /usr/local/go && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz",
    "mkdir -p /go/bin /go/src /go/pkg",
]
_GO_ENV = [
    (
        "PATH",
        "/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ),
    ("GOPATH", "/go"),
    ("GOROOT", "/usr/local/go"),
]


def _go_mapping() -> BaseMapping:
    return BaseMapping(
        image="images:debian/12",
        preamble=list(_GO_PREAMBLE),
        env=list(_GO_ENV),
        readiness_cmd="go version",
    )


def _node_mapping(major: str) -> BaseMapping:
    """node:<major>-bullseye -> Debian 11 + NodeSource node + corepack (yarn/pnpm).

    The upstream node image ships node/npm on PATH; on plain Debian we install
    the matching major via NodeSource. corepack (bundled with node >=16) provides
    yarn/pnpm shims that several JS repos' build scripts expect. The instance base
    Dockerfiles apt-install their own extras (python for node-gyp, redis-server for
    NodeBB, etc.), so those still run on top of this.
    """
    return BaseMapping(
        image="images:debian/11",
        preamble=[
            "apt-get update && apt-get install -y --no-install-recommends "
            "ca-certificates curl git gnupg && rm -rf /var/lib/apt/lists/*",
            f"curl -fsSL https://deb.nodesource.com/setup_{major}.x | bash -",
            "apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*",
            # corepack ships with node but must be enabled to expose yarn/pnpm.
            "corepack enable || npm install -g yarn || true",
        ],
        # corepack otherwise prints an interactive "about to download yarn"
        # confirmation that reads stdin; during non-interactive provisioning that
        # read gets EOF and the yarn/build step wedges. Disable the prompt so
        # corepack downloads the pinned package manager silently.
        env=[("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")],
        readiness_cmd="node --version",
        # jest spawns (cpus-1) parallel jsdom workers; heavy front-end suites
        # (element-web) OOM-kill workers at the 6GiB default, silently dropping
        # whole test files. Give JS runners more headroom.
        memory="12GiB",
    )


# qutebrowser's base Dockerfiles apt-install a big X/Qt list including `xvfb` but
# omit `libfontconfig1`, which Qt's offscreen platform plugin (libqoffscreen.so)
# dlopen's at runtime. On the upstream python:3.x-slim Docker image it was present
# transitively; on the Debian Incus base it is not, so any test that builds a GUI
# QApplication aborts the whole pytest process. Sibling qutebrowser Dockerfiles in
# the dataset DO list it -- restore it. Keyed on the `xvfb \` line so it only fires
# for the qutebrowser apt block (ansible and other python tasks have no xvfb line).
_QUTE_FONTCONFIG_SUB = [(r"xvfb \\", r"xvfb libfontconfig1 \\")]


# FROM (upstream Docker base) -> Incus base. Start minimal and grow per instance;
# refuse unmapped bases rather than guessing (wrong base = wrong Python/glibc).
BASE_IMAGE_MAP: dict[str, BaseMapping] = {
    "python:3.11-slim": BaseMapping(
        image="images:debian/12",
        preamble=list(_DEBIAN_PYTHON_PREAMBLE),
        readiness_cmd="python --version",
        run_substitutions=list(_QUTE_FONTCONFIG_SUB),
    ),
    # qutebrowser and some others pin python 3.9, which is Debian 11 (bullseye).
    "python:3.9-slim": BaseMapping(
        image="images:debian/11",
        preamble=list(_DEBIAN_PYTHON_PREAMBLE),
        readiness_cmd="python --version",
        run_substitutions=list(_QUTE_FONTCONFIG_SUB),
    ),
    # ansible on ubuntu:20.04. The linuxcontainers images: server no longer
    # carries Ubuntu, and the DTU engine only accepts images:/local: bases, so we
    # run on Debian 11 -- which ships python3.9 exactly like Ubuntu 20.04, giving
    # the `ansible-test --python 3.9` runner the interpreter it needs. The base
    # Dockerfile's Ubuntu-versioned apt packages (python3.9{,-dev,-venv}) do not
    # exist under those names on Debian, so rewrite them to the unversioned
    # Debian equivalents (which are python 3.9). The /usr/bin/python3.9 binary
    # still exists on Debian 11, so the Dockerfile's symlink lines are untouched.
    "ubuntu:20.04": BaseMapping(
        image="images:debian/11",
        preamble=[
            "apt-get update && apt-get install -y --no-install-recommends ca-certificates "
            "&& rm -rf /var/lib/apt/lists/*",
        ],
        readiness_cmd="python3 --version",
        run_substitutions=[
            (r"python3\.9-dev", "python3-dev"),
            (r"python3\.9-venv", "python3-venv"),
            (r"(?<![\w./-])python3\.9(?![\w./-])", "python3"),
        ],
    ),
    # Go family: the instance base Dockerfiles apt-install python themselves, but
    # rely on the golang image's PATH/GOPATH (image-level ENV) which we re-inject.
    "golang:1.24": _go_mapping(),
    "golang:1.24-bookworm": _go_mapping(),
    # Node family: bullseye is Debian 11, matching the upstream `-bullseye` tag.
    # NodeBB apt-installs + self-starts redis-server; element-web needs no DB.
    "node:18-bullseye": _node_mapping("18"),
    "node:22-bullseye": _node_mapping("22"),
}

# Instructions that do not affect the runnable environment for our purposes.
_IGNORED = {
    "ENTRYPOINT",
    "CMD",
    "LABEL",
    "EXPOSE",
    "USER",
    "ARG",
    "MAINTAINER",
    "SHELL",
    "VOLUME",
    "STOPSIGNAL",
    "HEALTHCHECK",
    "ONBUILD",
}

_KW_RE = re.compile(r"^\s*([A-Za-z]+)\s?(.*)$")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def tokenize(dockerfile: str) -> list[tuple[str, str]]:
    """Parse a Dockerfile into [(INSTRUCTION, body), ...].

    Handles comments, backslash line-continuations, and heredocs (``<<'EOF'``).
    """
    lines = dockerfile.splitlines()
    n = len(lines)
    instrs: list[tuple[str, str]] = []
    i = 0
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = _KW_RE.match(lines[i])
        if not m:
            i += 1
            continue
        kw = m.group(1).upper()
        collected = [m.group(2)]
        # 1) gather backslash continuations. Keep the trailing backslashes so the
        # joined body stays a single valid shell command (a `\`-newline
        # continuation), rather than collapsing into separate broken lines.
        while collected[-1].rstrip().endswith("\\"):
            i += 1
            if i >= n:
                break
            collected.append(lines[i])
        # 2) consume heredoc bodies until their terminators
        pending = [mm.group(2) for mm in _HEREDOC_RE.finditer("\n".join(collected))]
        while pending and i + 1 < n:
            i += 1
            collected.append(lines[i])
            if lines[i].strip() == pending[0]:
                pending.pop(0)
        instrs.append((kw, "\n".join(collected)))
        i += 1
    return instrs


def env_exports(base_dockerfile: str, instance_dockerfile: str) -> list[str]:
    """Collect ENV from both Dockerfiles as ``export K=V`` lines.

    The official grader re-exports the images' ENV before running tests; we do
    the same so grade-time behavior matches the build environment.
    """
    pairs: list[tuple[str, str]] = []
    # Seed with the base image's implied ENV (e.g. golang's PATH/GOPATH) so the
    # official run_script.sh finds the toolchain at grade time, mirroring how the
    # build steps saw it. Resolve leniently: if the base is unmapped, convert()
    # would already have failed, so absence here just means no base env to add.
    from_ref = next((body for kw, body in tokenize(base_dockerfile) if kw == "FROM"), None)
    if from_ref is not None:
        try:
            pairs.extend(_resolve_base(from_ref).env)
        except ConversionError:
            pass
    for df in (base_dockerfile, instance_dockerfile):
        for kw, body in tokenize(df):
            if kw == "ENV":
                pairs.extend(_parse_env(body))
    return [f"export {k}={shlex.quote(v)}" for k, v in pairs]


def _normalize_image(image: str) -> str:
    """Strip a private-registry prefix so mirrored images map like the public one.

    Some instances pin an ECR/registry mirror, e.g.
    ``084828598639.dkr.ecr.us-west-2.amazonaws.com/docker-hub/library/python:3.11-slim``.
    That is the same image as ``python:3.11-slim`` for our purposes, so if the
    full ref is unmapped, fall back to its final path segment.
    """
    if image in BASE_IMAGE_MAP:
        return image
    if "/" in image:
        tail = image.rsplit("/", 1)[-1]
        if tail in BASE_IMAGE_MAP:
            return tail
    return image


def _resolve_base(from_ref: str) -> BaseMapping:
    tokens = [t for t in from_ref.split() if not t.startswith("--")]
    image = _normalize_image(tokens[0] if tokens else from_ref)
    if image not in BASE_IMAGE_MAP:
        raise ConversionError(
            f"Unmapped base image {image!r}. Add a BASE_IMAGE_MAP entry mapping it "
            "to an Incus image (+ any preamble) before converting this instance."
        )
    return BASE_IMAGE_MAP[image]


def _parse_env(body: str) -> list[tuple[str, str]]:
    """Parse an ENV instruction body into [(key, value), ...]."""
    try:
        tokens = shlex.split(body)
    except ValueError:
        tokens = body.split()
    pairs: list[tuple[str, str]] = []
    if all("=" in t for t in tokens) and tokens:
        for t in tokens:
            k, _, v = t.partition("=")
            pairs.append((k, v))
    elif len(tokens) >= 2:  # `ENV KEY VALUE` form
        pairs.append((tokens[0], " ".join(tokens[1:])))
    return pairs


_RUNS_BUILD_RE = re.compile(r"(?:^|[\s;&|])(?:bash\s+|sh\s+|\./)?/build\.sh(?:\s|$)")


def _writes_build_script(instrs: list[tuple[str, str]]) -> bool:
    """True if a RUN heredoc writes /build.sh (`... > /build.sh`)."""
    return any(kw == "RUN" and "> /build.sh" in body for kw, body in instrs)


def _runs_build_script(instrs: list[tuple[str, str]]) -> bool:
    """True if a RUN actually executes /build.sh (not just writes/chmods it)."""
    return any(
        kw == "RUN"
        and "> /build.sh" not in body
        and "chmod" not in body
        and _RUNS_BUILD_RE.search(body)
        for kw, body in instrs
    )


def convert(
    base_dockerfile: str,
    instance_dockerfile: str,
    *,
    instance_id: str,
    cpu: str = "4",
    memory: str = "6GiB",
    allow_external: bool = True,
) -> dict[str, Any]:
    """Return a DTU profile dict reconstructing the instance's build environment."""
    instrs = tokenize(base_dockerfile) + tokenize(instance_dockerfile)

    from_ref = next((body for kw, body in instrs if kw == "FROM"), None)
    if from_ref is None:
        raise ConversionError("No FROM instruction found in base Dockerfile.")
    mapping = _resolve_base(from_ref)

    setup_cmds: list[str] = list(mapping.preamble)
    # Seed with the base image's implied ENV (PATH/GOPATH/etc.) so every RUN sees
    # the toolchain the upstream Docker base put on PATH but that is absent from
    # the Dockerfile text. Dockerfile ENV appended after can still override it.
    env: list[tuple[str, str]] = list(mapping.env)
    workdir = "/"

    for kw, body in instrs:
        if kw in ("FROM", *_IGNORED):
            continue
        if kw in ("COPY", "ADD"):
            raise ConversionError(
                f"{kw} is not supported by the converter (needs build-context files): {body!r}"
            )
        if kw == "ENV":
            env.extend(_parse_env(body))
            continue
        if kw == "WORKDIR":
            target = shlex.split(body)[0] if body.strip() else "/"
            workdir = target
            continue
        if kw == "RUN":
            run_body = body
            for pattern, repl in mapping.run_substitutions:
                run_body = re.sub(pattern, repl, run_body)
            script_lines = [f"export {k}={shlex.quote(v)}" for k, v in env]
            if workdir and workdir != "/":
                script_lines.append(f"cd {shlex.quote(workdir)}")
            script_lines.append(run_body)
            setup_cmds.append("\n".join(script_lines))
            continue
        # Unknown instruction: refuse rather than silently drop.
        raise ConversionError(f"Unhandled Dockerfile instruction {kw}: {body!r}")

    # Some published instance Dockerfiles (e.g. element-web) WRITE /build.sh --
    # the dependency-install script -- but omit the `RUN /build.sh` that actually
    # executes it (NodeBB and most others include it). In the official benchmark
    # deps are baked into a prebuilt image; in our from-Dockerfile reconstruction
    # they are only installed if the Dockerfile runs the script. Without it,
    # node_modules/site-packages are never installed and the graded test command
    # yields zero parseable tests. If build.sh was written but never run, run it.
    if _writes_build_script(instrs) and not _runs_build_script(instrs):
        script_lines = [f"export {k}={shlex.quote(v)}" for k, v in env]
        # Run via the script's own `#!/bin/sh` shebang (as the instances that do
        # include `RUN /build.sh` do). build.sh embeds a nested `node <<'EOF'`
        # heredoc that bash and dash lex differently; invoking it as `bash
        # /build.sh` mis-parses that heredoc, so run it the way the authors do.
        script_lines.append("chmod +x /build.sh && /build.sh")  # build.sh cd's into /app itself
        setup_cmds.append("\n".join(script_lines))

    memory = mapping.memory or memory
    return {
        "name": f"swebench-pro-{_short(instance_id)}",
        "description": (
            f"SWE-bench Pro environment for {instance_id}, reconstructed from the "
            "official base+instance Dockerfiles (Docker -> DTU conversion)."
        ),
        "base": {"image": mapping.image, "config": {"limits.cpu": cpu, "limits.memory": memory}},
        "passthrough": {"allow_external": allow_external},
        "provision": {"setup_cmds": setup_cmds},
        "readiness": _readiness(mapping),
    }


def _readiness(mapping: BaseMapping) -> list[dict[str, str]]:
    """Language-agnostic readiness: repo present, plus a toolchain check keyed to
    the base image's primary language (python/go/node) when the mapping sets one."""
    checks = [{"name": "repo-present", "command": "test -d /app/.git"}]
    if mapping.readiness_cmd:
        checks.insert(0, {"name": "toolchain-present", "command": mapping.readiness_cmd})
    return checks


def _short(instance_id: str) -> str:
    """A short, DNS-safe-ish slug for the profile name."""
    slug = instance_id.replace("instance_", "").replace("__", "-")
    slug = re.sub(r"[^A-Za-z0-9-]", "-", slug)
    return slug[:60].strip("-").lower()


class _BlockDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _str_representer)


def to_yaml(profile: dict[str, Any]) -> str:
    return yaml.dump(profile, Dumper=_BlockDumper, sort_keys=False, width=4096)


if __name__ == "__main__":
    import sys

    from eval.task_loaders.swe_bench_support.official_assets import fetch_assets

    iid = sys.argv[1]
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    assets = fetch_assets(iid, cache)
    profile = convert(assets.base_dockerfile, assets.instance_dockerfile, instance_id=iid)
    print(to_yaml(profile))
