"""Windows-container primitives: docker wrappers, preflight, image build.

Stdlib only, so ``cli.py`` can import this as a standalone uv script without a
project virtualenv.

Cross-platform by design. The only thing that differs between running from WSL2
and running natively on Windows is which docker binary is invoked, and that is
isolated to :func:`docker_exe`. Everything else, including the container paths,
is identical on both hosts.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PROVISIONING = Path(__file__).resolve().parent / "provisioning"

# The Windows container engine is a separate daemon from the Linux one, always
# reached by explicit context rather than whatever the active context happens
# to be.
CONTEXT = os.environ.get("WIN_E2E_DOCKER_CONTEXT", "desktop-windows")
BASE_IMAGE = os.environ.get("WIN_E2E_BASE_IMAGE", "mcr.microsoft.com/windows/servercore:ltsc2025")
AGENT_REF = os.environ.get("WIN_E2E_AGENT_REF", "main")
IMAGE = os.environ.get("WIN_E2E_IMAGE", "amplifier-agent-win-e2e:latest")

# Docker Desktop's default install location, as seen from WSL.
_WSL_DOCKER = "/mnt/c/Program Files/Docker/docker.exe"


class WinE2EError(RuntimeError):
    """Any failure driving the Windows container engine."""


def on_windows() -> bool:
    return sys.platform == "win32"


def on_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def docker_exe() -> str:
    """Locate the docker binary that can reach the Windows engine.

    Native Windows uses ``docker``. From WSL the Linux ``docker`` cannot speak
    npipe, so the Windows ``docker.exe`` is used over interop instead.
    """
    override = os.environ.get("WIN_E2E_DOCKER_EXE")
    if override:
        return override
    found = shutil.which("docker" if on_windows() else "docker.exe")
    if found:
        return found
    if not on_windows() and Path(_WSL_DOCKER).exists():
        return _WSL_DOCKER
    raise WinE2EError(
        "docker not found. Install Docker Desktop with Windows containers enabled"
        " (from WSL, also enable WSL integration), or set WIN_E2E_DOCKER_EXE."
    )


def redact(text: str) -> str:
    """Replace any forwarded secret value with a placeholder.

    Container creation passes API keys as `-e KEY=value`, so an unredacted
    argv or subprocess output reaching an exception message would put the key
    into a pytest traceback, and from there into CI logs. Everything that can
    surface to a human goes through here first.
    """
    if not text:
        return text
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, f"<{key} redacted>")
    return text


def dk(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker command pinned to the Windows context."""
    argv = [docker_exe(), "-c", CONTEXT, *args]
    proc = subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise WinE2EError(
            redact(
                f"docker {' '.join(args)} failed (exit {proc.returncode})\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        )
    return proc


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def engine_info() -> tuple[str, str]:
    """Return (OSType, Isolation) for the Windows engine, or ("", "") if unreachable."""
    try:
        exe = docker_exe()
    except WinE2EError:
        return "", ""
    probe = subprocess.run(
        [exe, "-c", CONTEXT, "info", "--format", "{{.OSType}}|{{.Isolation}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode != 0:
        return "", ""
    ostype, _, isolation = probe.stdout.strip().partition("|")
    return ostype, isolation


def preflight() -> list[str]:
    """Return a list of human-readable problems. Empty means good to go."""
    problems: list[str] = []

    if not on_windows() and not on_wsl():
        problems.append(
            "not running on Windows or WSL. This suite drives a Windows container engine and needs one of those hosts."
        )

    try:
        docker_exe()
    except WinE2EError as exc:
        return [*problems, str(exc)]

    ostype, isolation = engine_info()
    if not ostype:
        problems.append(
            f"cannot reach the Windows Docker engine via context '{CONTEXT}'."
            " Is Docker Desktop running with Windows containers enabled?"
        )
        return problems

    if ostype != "windows":
        problems.append(
            f"context '{CONTEXT}' points at an engine with OSType='{ostype}', expected 'windows'."
            " Switch Docker Desktop to Windows containers."
        )
    if isolation and isolation != "hyperv":
        # Process isolation demands the container's build match the host's
        # exactly, which a pinned base image tag cannot promise.
        problems.append(
            f"isolation is '{isolation}', expected 'hyperv'."
            f" With process isolation the base image build must match the host exactly, and {BASE_IMAGE} likely will not."
        )
    return problems


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def image_exists(image: str = IMAGE) -> bool:
    return dk("image", "inspect", image, check=False).returncode == 0


def build_image(
    image: str = IMAGE,
    base: str = BASE_IMAGE,
    ref: str = AGENT_REF,
    no_cache: bool = False,
) -> None:
    """Build the provisioned image. Installs amplifier-agent from upstream.

    ``no_cache`` is what makes a rebuild actually rebuild. The install step is
    `uv tool install --from git+...@<ref>`, whose command text never changes
    when upstream moves, so docker happily serves a cached layer built from an
    older commit. A cache-hitting "rebuild" would silently keep testing stale
    code, which is the one thing this suite exists to prevent.
    """
    args = ["build", "-t", image, "-f", str(PROVISIONING / "Dockerfile.windows")]
    if no_cache:
        args.append("--no-cache")
    args += [
        "--build-arg",
        f"BASE_IMAGE={base}",
        "--build-arg",
        f"AMPLIFIER_AGENT_REF={ref}",
        str(PROVISIONING),
    ]
    dk(*args, capture=False)


def remove_image(image: str = IMAGE) -> None:
    dk("image", "rm", "-f", image, check=False)


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

# Environment forwarded into the container when set on the host. Suites that
# need one of these enforce their own presence; missing keys are not an error
# here.
PASSTHROUGH_ENV = ("ANTHROPIC_API_KEY",)


def state_of(name: str) -> str:
    proc = dk("container", "inspect", name, "--format", "{{.State.Status}}", check=False)
    return proc.stdout.strip() or "absent"


def start(name: str, image: str = IMAGE) -> None:
    """Create and start a detached container, replacing any stale one."""
    if state_of(name) != "absent":
        remove(name)
    args = ["run", "-d", "--name", name]
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value:
            args += ["-e", f"{key}={value}"]
    args.append(image)
    dk(*args)
    if state_of(name) != "running":
        logs = redact(dk("logs", name, check=False).stdout)
        raise WinE2EError(f"container {name} did not reach running state\nlogs:\n{logs}")


def remove(name: str) -> None:
    dk("rm", "-f", name, check=False)


def exec_(name: str, argv: list[str]) -> dict[str, Any]:
    """Run argv inside the container. Returns exit_code, stdout, stderr."""
    proc = dk("exec", name, *argv, check=False)
    return {
        "command": argv,
        "exit_code": proc.returncode,
        "stdout": redact(proc.stdout),
        "stderr": redact(proc.stderr),
    }
