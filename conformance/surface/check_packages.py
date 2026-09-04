"""Check built distributions preserve the SDK, engine, and HTTP package boundaries."""

import argparse
import json
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PACKAGES = {
    "amplifier-agent": "amplifier_agent",
    "amplifier-agent-engine": "amplifier_agent_engine",
    "amplifier-agent-http": "amplifier_agent_http",
}


def check_wheel(path: Path) -> list[str]:
    errors = []
    with ZipFile(path) as wheel:
        files = wheel.namelist()
        metadata_files = [name for name in files if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            return [f"{path.name}: expected one distribution metadata record"]
        metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))
        name = canonicalize_name(metadata["Name"])
        if name not in PACKAGES:
            return [f"{path.name}: unexpected distribution {name}"]
        package = PACKAGES[name]
        metadata_prefix = metadata_files[0].removesuffix("METADATA")
        for entry in files:
            if not entry.startswith((package + "/", metadata_prefix)):
                errors.append(f"{path.name}: foreign wheel content {entry}")
        if package + "/__init__.py" not in files:
            errors.append(f"{path.name}: missing package initializer")
        if name == "amplifier-agent" and any(
            entry.startswith((package + "/_engine/", package + "/_runtime/")) for entry in files
        ):
            errors.append(f"{path.name}: SDK wheel contains execution implementation")
        dependencies = {
            canonicalize_name(Requirement(value).name)
            for value in metadata.get_all("Requires-Dist", [])
        }
        if name == "amplifier-agent" and dependencies != {"amplifier-agent-engine"}:
            errors.append(f"{path.name}: SDK dependencies must contain only the engine")
        if name == "amplifier-agent-engine" and dependencies & {
            "amplifier-agent",
            "amplifier-agent-http",
        }:
            errors.append(f"{path.name}: engine depends on a projection")
        if name == "amplifier-agent-http" and (
            "amplifier-agent" not in dependencies or "amplifier-agent-engine" in dependencies
        ):
            errors.append(f"{path.name}: HTTP must depend on the public SDK")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", type=Path, nargs="+")
    args = parser.parse_args()
    errors = [error for path in args.wheels for error in check_wheel(path)]
    print(json.dumps({"wheels": [path.name for path in args.wheels], "errors": errors}))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
