"""Golden Incus images for the JobBench harness.

260 trials (65 tasks x 4 agents) each launching a fresh DTU makes
cold-provisioning the full toolchain on every launch untenable -- LibreOffice
alone takes minutes to install. So we bake once and relaunch from a published
Incus image instead. This is the convention documented in
amplifier-bundle-evaluation's ``context/harness/golden_image_caching.md``;
this module is JobBench's instance of it.

Two images, one dependency chain:

    jobbench-base              apt + pip toolchain every agent gets
    jobbench-<agent>           jobbench-base + that agent's CLI, no secrets

Baking is idempotent: ``ensure_image`` skips the `incus launch` + `publish`
round trip entirely when the alias already exists, unless ``force`` is set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from jobbench.dtu import DTU, DTUError

logger = logging.getLogger(__name__)

PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"

BASE_ALIAS = "jobbench-base"
BASE_PROFILE = PROFILES_DIR / "jobbench-base.bake.yaml"

# Bake profiles live at profiles/agents/<agent>.bake.yaml; images are
# published as jobbench-<agent>.
AGENT_PROFILES_DIR = PROFILES_DIR / "agents"

# Baking involves a full package/toolchain install (apt, pip, uv, LibreOffice,
# or an agent CLI) which can legitimately take tens of minutes. The caller is
# expected to run this out of band from any per-trial timeout budget.
DEFAULT_BAKE_TIMEOUT_S = 1800.0


class ImageError(RuntimeError):
    """A bake, publish, or lookup step failed."""


@dataclass
class BakeResult:
    """Outcome of one `ensure_image` call, for progress reporting."""

    alias: str
    baked: bool  # False when the image already existed and we skipped baking
    elapsed_s: float


def agent_alias(agent: str) -> str:
    """Published image alias for an agent, e.g. ``jobbench-amplifier-agent``."""
    return f"jobbench-{agent}"


def agent_bake_profile(agent: str) -> Path:
    """Path to an agent's bake profile."""
    return AGENT_PROFILES_DIR / f"{agent}.bake.yaml"


async def _run(*args: str) -> None:
    """Run an `incus` subcommand, raising on non-zero exit.

    Used for the plumbing steps (`stop`, `publish`, `delete`) around a DTU
    launch, where `incus` output isn't needed -- only success/failure.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        raise ImageError(f"command failed: {' '.join(args)}: {stderr or stdout}")


async def image_exists(alias: str) -> bool:
    """True if a local Incus image with this alias is already published."""
    proc = await asyncio.create_subprocess_exec(
        "incus",
        "image",
        "info",
        alias,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def ensure_image(
    bake_profile: Path | str,
    alias: str,
    *,
    force: bool = False,
    launch_timeout_s: float = DEFAULT_BAKE_TIMEOUT_S,
) -> BakeResult:
    """Bake `alias` from `bake_profile` if it doesn't already exist.

    Skips the bake entirely when the image is already published, unless
    `force` -- the common path for every run after the first. On failure at
    any step the build container is always deleted in `finally` so a broken
    bake never leaks a container that then blocks a retry (Incus refuses to
    reuse a name that's still in use).
    """
    start = time.monotonic()

    if not force and await image_exists(alias):
        return BakeResult(alias=alias, baked=False, elapsed_s=time.monotonic() - start)

    bake_profile = Path(bake_profile)
    if not bake_profile.is_file():
        raise ImageError(f"bake profile not found: {bake_profile}")

    if force and await image_exists(alias):
        await _run("incus", "image", "delete", alias)

    build_name = f"{alias}-build"
    dtu = await DTU.launch(bake_profile, name=build_name, launch_timeout_s=launch_timeout_s)
    try:
        await _run("incus", "stop", dtu.id)
        try:
            await _run("incus", "publish", dtu.id, "--alias", alias)
        except ImageError as exc:
            raise ImageError(
                f"incus publish failed for {alias} (built from {bake_profile}): {exc}"
            ) from exc
    finally:
        # Always clean up the build container, success or failure, so a
        # failed bake never leaks and blocks the next attempt.
        try:
            await _run("incus", "delete", "--force", dtu.id)
        except ImageError as exc:
            logger.warning("could not delete build container %s: %s", dtu.id, exc)

    if not await image_exists(alias):
        raise ImageError(f"publish reported success but image {alias!r} is not present")

    return BakeResult(alias=alias, baked=True, elapsed_s=time.monotonic() - start)


async def ensure_base_image(*, force: bool = False) -> BakeResult:
    """Bake jobbench-base, the toolchain shared by every agent."""
    return await ensure_image(BASE_PROFILE, BASE_ALIAS, force=force)


async def ensure_agent_image(agent: str, *, force: bool = False) -> BakeResult:
    """Bake jobbench-<agent> on top of jobbench-base.

    Callers must ensure jobbench-base already exists (see
    `ensure_base_image`) -- the agent bake profile references it as
    `base.image: local:jobbench-base` and will fail outright if it's
    missing.
    """
    profile = agent_bake_profile(agent)
    if not profile.is_file():
        raise ImageError(f"no bake profile for agent {agent!r}; expected {profile}")
    return await ensure_image(profile, agent_alias(agent), force=force)


__all__ = [
    "AGENT_PROFILES_DIR",
    "BASE_ALIAS",
    "BASE_PROFILE",
    "BakeResult",
    "DTUError",
    "ImageError",
    "agent_alias",
    "agent_bake_profile",
    "ensure_agent_image",
    "ensure_base_image",
    "ensure_image",
    "image_exists",
]
