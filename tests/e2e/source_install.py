"""Verify the installed monorepo packages resolve to the requested Git tree."""

import importlib
import importlib.metadata
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PACKAGES = {
    "amplifier-agent": ("packages/python", "amplifier_agent"),
    "amplifier-agent-engine": ("packages/engine", "amplifier_agent_engine"),
    "amplifier-agent-http": ("packages/http", "amplifier_agent_http"),
}


def validate(
    expected: str, direct_urls: dict, locked: dict, packages: list[str] | None = None
) -> None:
    for name in packages if packages is not None else PACKAGES:
        subdirectory, _ = PACKAGES[name]
        assert name in direct_urls, f"Missing installed distribution: {name}"
        assert name in locked, f"Missing locked distribution: {name}"
        direct = direct_urls[name]
        assert direct["vcs_info"]["commit_id"] == expected, f"Installed revision differs: {name}"
        assert direct.get("subdirectory") == subdirectory, f"Installed subdirectory differs: {name}"
        source = urlsplit(locked[name])
        assert source.fragment == expected, f"Locked revision differs: {name}"
        assert parse_qs(source.query).get("subdirectory") == [subdirectory], (
            f"Locked subdirectory differs: {name}"
        )


def main(expected: str, packages: list[str] | None = None) -> None:
    selected = packages if packages is not None else list(PACKAGES)
    lock = tomllib.loads(Path("/opt/consumer/uv.lock").read_text())
    locked = {
        package["name"]: package["source"]["git"]
        for package in lock["package"]
        if package["name"] in selected
    }
    direct_urls = {}
    for name in selected:
        _, module = PACKAGES[name]
        distribution = importlib.metadata.distribution(name)
        direct_urls[name] = json.loads(distribution.read_text("direct_url.json"))
        assert "site-packages" in Path(importlib.import_module(module).__file__).parts, name
    validate(expected, direct_urls, locked, selected)
    print(json.dumps({"revision": expected, "packages": selected}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] or None)
