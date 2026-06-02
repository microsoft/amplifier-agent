"""Test that the vendored bundle.md declares default_provider: anthropic.

Implements D6: the vendored bundle frontmatter must contain a top-level
default_provider key set to "anthropic". This is read by the engine to
seed the default provider routing before host/CLI overrides apply.
"""

import yaml

from amplifier_agent_lib.bundle import BUNDLE_MD


def test_bundle_md_declares_default_provider_anthropic() -> None:
    text = BUNDLE_MD.read_text()
    parts = text.split("---\n")
    manifest = yaml.safe_load(parts[1])
    assert manifest.get("default_provider") == "anthropic"
