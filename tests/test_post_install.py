"""Tests for the post-install hook that primes the prepared-bundle cache.

TDD RED phase: these tests must fail before post_install.py is created.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_post_install_primes_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """post_install.main() primes the cache on a fresh install (cold path)."""
    from amplifier_agent_lib import __version__
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version
    from amplifier_agent_lib.post_install import main as post_install_main

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    cache = cache_dir_for_version(__version__)
    assert not cache.exists(), "Cache dir should not exist before post-install runs"

    exit_code = await post_install_main()

    assert exit_code == 0
    assert cache.exists(), "Cache dir should exist after post-install primes it"


@pytest.mark.asyncio
async def test_post_install_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling post_install.main() twice returns 0 both times; second call detects existing cache."""
    from amplifier_agent_lib.post_install import main as post_install_main

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    # First call — cold path, primes the cache.
    first_exit = await post_install_main()
    assert first_exit == 0

    # Second call — idempotent; detects existing cache and exits 0 without re-preparing.
    second_exit = await post_install_main()
    assert second_exit == 0


@pytest.mark.asyncio
async def test_post_install_swallows_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """post_install.main() returns 0 even when load_and_prepare_cached raises."""
    import amplifier_agent_lib.post_install as post_install_mod
    from amplifier_agent_lib.post_install import main as post_install_main

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated prepare failure")

    monkeypatch.setattr(post_install_mod, "load_and_prepare_cached", boom)

    exit_code = await post_install_main()

    assert exit_code == 0, "post-install must exit 0 even when cache prime fails"
