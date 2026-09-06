"""Validate clause references and check registrations."""

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
DOCUMENTS = {
    "agent-interface.json": ("agent-interface/1", "agent-interface.v1.md", "AI"),
    "turn-events.json": ("turn-events/1", "turn-events.v1.md", "TE"),
    "language-binding.json": ("language-binding/1", "language-binding.v1.md", "LB"),
    "host-config.json": ("host-config/1", "host-config.v1.md", "HC"),
    "http-face.json": ("http-face/1", "http-face.v1.md", "HF"),
    "engine-seam.json": ("engine-seam/1", "engine-seam.v1.md", "ES"),
    "house-rules.json": ("house-rules", "README.md", "HR"),
}
SURFACES = {"python", "typescript", "http", "repository"}
KINDS = {"runtime", "static", "review"}
OBLIGATION_FIELDS = {
    "id", "section", "lines", "obligation", "surfaces", "verification",
    "checks", "mutation",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key {key!r}; keep one value")
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}; correct the inventory file") from error


def fields(value, expected, label):
    require(isinstance(value, dict), f"{label}: use an object")
    require(set(value) == expected,
            f"{label}: expected fields {', '.join(sorted(expected))}")


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}: supply nonempty text")


def string_set(value, allowed, label):
    require(isinstance(value, list) and bool(value), f"{label}: supply a nonempty list")
    require(all(isinstance(item, str) for item in value), f"{label}: use string entries")
    require(len(value) == len(set(value)), f"{label}: remove duplicate entries")
    unknown = sorted(set(value) - allowed)
    require(not unknown, f"{label}: unknown entries {unknown}; use registered values")


def source_section(lines, position):
    section = "Preamble"
    for line in lines[:position]:
        if line.startswith("## "):
            section = line[3:]
    return section


def validate(inventory):
    expected = set(DOCUMENTS) | {"checks.json"}
    found = {path.name for path in inventory.glob("*.json")}
    require(found == expected,
            f"{inventory}: restore required files {sorted(expected - found)} "
            f"and remove unregistered JSON files {sorted(found - expected)}")

    catalog = read_json(inventory / "checks.json")
    fields(catalog, {"checks"}, "checks.json")
    require(isinstance(catalog["checks"], list) and bool(catalog["checks"]),
            "checks.json: supply a nonempty checks list")
    checks = {}
    for index, check in enumerate(catalog["checks"]):
        label = f"checks.json[{index}]"
        fields(check, {"id", "kind", "surfaces"}, label)
        text(check["id"], label + ".id")
        require(isinstance(check["kind"], str) and check["kind"] in KINDS,
                f"{label}: kind must be runtime, static, or review")
        require(re.fullmatch(r"(runtime|static|review)\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*", check["id"]),
                f"{label}: use a kind-prefixed, dotted check id")
        require(check["id"].startswith(check["kind"] + "."),
                f"{label}: make the id prefix match its kind")
        require(check["id"] not in checks, f"{label}: duplicate check id {check['id']}")
        string_set(check["surfaces"], SURFACES, label + ".surfaces")
        checks[check["id"]] = check

    obligations = []
    ids = set()
    for filename, (contract, source, prefix) in DOCUMENTS.items():
        document = read_json(inventory / filename)
        fields(document, {"contract", "source", "obligations"}, filename)
        require(document["contract"] == contract, f"{filename}: contract must be {contract}")
        source_path = "contracts/" + source
        require(document["source"] == source_path, f"{filename}: source must be {source_path}")
        lines = (REPOSITORY / source_path).read_text(encoding="utf-8").splitlines()
        require(isinstance(document["obligations"], list) and bool(document["obligations"]),
                f"{filename}: supply a nonempty obligations list")
        for index, obligation in enumerate(document["obligations"]):
            label = f"{filename}[{index}]"
            fields(obligation, OBLIGATION_FIELDS, label)
            for key in ("id", "section", "obligation", "mutation"):
                text(obligation[key], label + "." + key)
            label = obligation["id"]
            require(re.fullmatch(prefix + r"-[0-9]{3}", label),
                    f"{label}: use an id with prefix {prefix} and three digits")
            require(label not in ids, f"{label}: duplicate obligation id; retain distinct stable ids")
            ids.add(label)
            span = obligation["lines"]
            require(isinstance(span, list) and len(span) == 2
                    and all(type(number) is int for number in span),
                    f"{label}: lines must be two integer line numbers")
            require(1 <= span[0] <= span[1] <= len(lines),
                    f"{label}: lines must be an ordered range within {source_path}")
            section = source_section(lines, span[0])
            require(section == obligation["section"] == source_section(lines, span[1]),
                    f"{label}: correct the section/range to identify one source section")
            require(section not in {"Backlogged", "Reserved", "Changelog"},
                    f"{label}: cite a normative clause rather than {section}")
            string_set(obligation["surfaces"], SURFACES, label + ".surfaces")
            kind = obligation["verification"]
            require(isinstance(kind, str) and kind in KINDS | {"indirect"},
                    f"{label}: verification must be runtime, static, review, or indirect")
            if contract == "engine-seam/1":
                require(kind in {"review", "indirect"},
                        f"{label}: verify the seam through public checks or review")
            string_set(obligation["checks"], set(checks), label + ".checks")
            allowed = {"runtime", "static"} if kind == "indirect" else {kind}
            require(all(checks[name]["kind"] in allowed for name in obligation["checks"]),
                    f"{label}: reference checks matching the verification kind")
            mapped_surfaces = set().union(*(set(checks[name]["surfaces"])
                                           for name in obligation["checks"]))
            require(set(obligation["surfaces"]) <= mapped_surfaces,
                    f"{label}: map a check for every declared surface")
            obligations.append((contract, obligation))

    used = {name for _, obligation in obligations for name in obligation["checks"]}
    require(used == set(checks), f"checks.json: remove or map unused checks {sorted(set(checks) - used)}")
    public = {name for contract, obligation in obligations
              if contract != "engine-seam/1" and obligation["verification"] != "indirect"
              for name in obligation["checks"]}
    for _, obligation in obligations:
        if obligation["verification"] == "indirect":
            require(set(obligation["checks"]) <= public,
                    f"{obligation['id']}: map indirect verification to public contract checks")
    return len(obligations), len(checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Path(__file__).resolve().parent / "clauses",
                        help="directory containing the clause inventory and check catalog")
    arguments = parser.parse_args()
    try:
        obligations, checks = validate(arguments.inventory)
    except (OSError, ValueError) as error:
        print(f"Inventory invalid: {error}", file=sys.stderr)
        return 1
    print(f"Inventory structure valid: {obligations} obligations, {checks} registered checks.")
    print("This does not assess implementation conformance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
