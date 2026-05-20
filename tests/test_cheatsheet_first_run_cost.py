"""
Tests for CHEATSHEET.md section 3 first-run cost documentation.

Verifies that the cheatsheet accurately documents the first-run cliff
per section 4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.
"""

import re
from pathlib import Path

CHEATSHEET = Path("docs/test-docs/CHEATSHEET.md")

# EN DASH as unicode escape to avoid ruff RUF001/RUF002 warnings
EN_DASH = "\u2013"


def _content() -> str:
    return CHEATSHEET.read_text(encoding="utf-8")


def test_cheatsheet_has_first_run_cost_callout() -> None:
    """Section 3 must contain a 'First-run cost:' callout block."""
    content = _content()
    assert "First-run cost:" in content, "Cheatsheet section 3 must have a 'First-run cost:' callout block"


def test_cheatsheet_documents_5_30_s_cliff() -> None:
    """Section 3 must document the first-run cost with explicit duration and unit space."""
    content = _content()
    # The spec requires "5-30 s" written with an EN DASH and a space before the unit
    target = f"5{EN_DASH}30 s"
    assert target in content, f"Cheatsheet must document '{target}' cliff (with space before unit)"


def test_cheatsheet_mentions_vendored_opinionated_manifest() -> None:
    """The first-run callout must reference the vendored opinionated manifest."""
    content = _content()
    assert "vendored opinionated manifest" in content, (
        "Cheatsheet first-run cost section must mention 'vendored opinionated manifest'"
    )


def test_cheatsheet_mentions_uv_pip_install_no_sources() -> None:
    """The first-run callout must mention 'uv pip install --no-sources'."""
    content = _content()
    assert "uv pip install --no-sources" in content, (
        "Cheatsheet first-run cost section must mention 'uv pip install --no-sources'"
    )


def test_cheatsheet_mentions_sha256_cache_key() -> None:
    """The first-run callout must describe the sha256(bundle.md) cache key."""
    content = _content()
    assert "sha256(bundle.md)" in content, (
        "Cheatsheet first-run cost section must mention 'sha256(bundle.md)' cache key"
    )


def test_cheatsheet_mentions_post_install_hook() -> None:
    """The first-run callout must reference the amplifier-agent-post-install hook."""
    content = _content()
    assert "amplifier-agent-post-install" in content, (
        "Cheatsheet first-run cost section must mention 'amplifier-agent-post-install'"
    )


def test_cheatsheet_no_bare_near_instant() -> None:
    """Any 'near-instant' reference must be qualified (warm cache / after first invocation).

    Bare 'near-instant' without qualification misleads readers into expecting
    sub-second cold-start, which is false.
    """
    content = _content()
    for match in re.finditer(r"near-instant", content):
        start = match.start()
        end = min(len(content), match.end() + 80)
        ctx = content[start:end]
        assert any(
            q in ctx.lower()
            for q in [
                "on warm cache",
                "after first invocation",
                "warm cache",
                "subsequent",
            ]
        ), f"'near-instant' found without qualification. Context: {ctx!r}"
