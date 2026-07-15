# Copyright (c) Microsoft. All rights reserved.

"""Validate pixel art sprites for game readiness.

Checks each PNG in a directory for: correct dimensions, alpha channel,
transparent background, limited color palette, and anti-aliasing artifacts.
"""

import json
from pathlib import Path
import sys

import click
from PIL import Image


def validate_sprite(path: Path, expected_size: int | None, expected_palette: int | None) -> dict:
    """Validate a single sprite and return a results dict."""
    result: dict = {
        "file": path.name,
        "width": 0,
        "height": 0,
        "mode": "",
        "unique_colors": 0,
        "transparency_pct": 0.0,
        "checks": {},
    }

    img = Image.open(path)
    result["width"] = img.width
    result["height"] = img.height
    result["mode"] = img.mode

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Dimensions
    if expected_size is not None:
        result["checks"]["dimensions"] = (
            "PASS" if img.width == expected_size and img.height == expected_size else "FAIL"
        )
        result["checks"]["dimensions_detail"] = (
            f"expected {expected_size}x{expected_size}, got {img.width}x{img.height}"
        )

    # Alpha channel
    result["checks"]["has_alpha"] = "PASS" if "A" in img.getbands() else "FAIL"

    # Transparency percentage
    pixels = list(img.get_flattened_data())
    total = len(pixels)
    transparent = sum(1 for p in pixels if p[3] == 0)
    result["transparency_pct"] = round(transparent / total * 100.0, 1) if total > 0 else 0.0
    result["checks"]["background_removed"] = "PASS" if result["transparency_pct"] > 10.0 else "FAIL"
    result["checks"]["background_detail"] = f"{result['transparency_pct']}% transparent pixels"

    # Color count
    colors = img.getcolors(maxcolors=total)
    num_colors = len(colors) if colors else -1
    result["unique_colors"] = num_colors
    if expected_palette is not None:
        result["checks"]["palette"] = "PASS" if 0 < num_colors <= expected_palette else "FAIL"
        result["checks"]["palette_detail"] = f"expected <={expected_palette} colors, got {num_colors}"

    # Anti-aliasing check: in pixel art, very few colors should appear only once or twice
    if num_colors > 0 and colors:
        color_counts = {tuple(c[1]): c[0] for c in colors}
        rare = sum(1 for count in color_counts.values() if count < 3)
        ratio = round(rare / len(color_counts) * 100.0, 1)
        result["checks"]["no_antialiasing"] = "PASS" if ratio <= 30.0 else "FAIL"
        result["checks"]["no_antialiasing_detail"] = f"{ratio}% of colors appear fewer than 3 times"

    return result


@click.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option("--expected-size", type=int, default=None, help="Expected width and height in pixels")
@click.option("--expected-palette", type=int, default=None, help="Maximum number of unique colors")
def main(directory: Path, expected_size: int | None, expected_palette: int | None) -> None:
    """Validate all PNG sprites in DIRECTORY."""
    png_files = sorted(directory.glob("**/*.png"))
    if not png_files:
        click.echo(f"No PNG files found in {directory}")
        sys.exit(1)

    results = []
    for png in png_files:
        try:
            results.append(validate_sprite(png, expected_size, expected_palette))
        except Exception as e:
            results.append({"file": png.name, "error": str(e)})

    # Print summary
    click.echo(json.dumps(results, indent=2))

    # Print pass/fail overview
    click.echo("\n--- Summary ---")
    for r in results:
        if "error" in r:
            click.echo(f"  {r['file']}: ERROR - {r['error']}")
            continue
        checks = r.get("checks", {})
        failures = [k for k, v in checks.items() if v == "FAIL" and not k.endswith("_detail")]
        if failures:
            click.echo(f"  {r['file']}: FAIL ({', '.join(failures)})")
        else:
            click.echo(f"  {r['file']}: PASS")


if __name__ == "__main__":
    main()
