# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@dev",
# ]
# ///
"""Resolve a CHC-bundled region (e.g. Tana River basin) to a bbox and optional polygon."""

import json
import sys
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.region import bbox_from_geometry, clean_region_name

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Bundled regions. ``geojson`` is a single-feature FeatureCollection under
# ``data/``. Aliases are passed through :func:`clean_region_name`.
_REGIONS = {
    "tana_river_basin": {
        "name": "Tana River basin",
        "geojson": "tana_river_basin.geojson",
        "level": "basin",
        "iso3": "KEN",
        "country": "Kenya",
        "aliases": ("Tana basin", "Tana River catchment", "Tana catchment"),
    },
}

# Names that look like a bundled region but mean something else.
_AMBIGUOUS = {
    "tana_river": (
        "'Tana River' is ambiguous. For the hydrological basin pass 'Tana River basin'; "
        "for Tana River County use the resolve-region skill with kenya-tana_river."
    ),
}


def _index():
    by_key = {}
    for key, spec in _REGIONS.items():
        by_key[key] = spec
        for alias in (spec["name"], *spec["aliases"]):
            by_key.setdefault(clean_region_name(alias), spec)
    return by_key


def lookup(name):
    """Return the GeoJSON Feature for a bundled region name or alias."""
    cleaned = clean_region_name(name)
    if cleaned in _AMBIGUOUS:
        raise UsageError(_AMBIGUOUS[cleaned])
    spec = _index().get(cleaned)
    if spec is None:
        known = ", ".join(repr(s["name"]) for s in _REGIONS.values())
        raise UsageError(
            f"{name!r} is not a CHC-bundled region (known: {known}). "
            "For countries, admin units, and landmarks use the resolve-region skill."
        )
    with (_DATA_DIR / spec["geojson"]).open(encoding="utf-8") as fh:
        source = json.load(fh)["features"][0]
    geometry = source["geometry"]
    n, w, s, e = bbox_from_geometry(geometry)
    extra = {k: v for k, v in source.get("properties", {}).items() if k != "name"}
    return {
        "type": "Feature",
        "properties": {
            "iso3": spec["iso3"],
            "name": spec["name"],
            "region_name": clean_region_name(spec["name"]),
            "level": spec["level"],
            "country": spec["country"],
            "bbox": [n, w, s, e],
            **extra,
        },
        "geometry": geometry,
    }


@weather_skill(
    name="resolve-chc-region",
    version=_SKILL_VERSION,
    output=False,
)
@weather_skill.argument(
    "name",
    help="CHC-bundled region name or alias (e.g. 'Tana River basin', 'Tana basin')",
)
@weather_skill.argument(
    "--geojson",
    help="Optional path: write the boundary polygon as GeoJSON",
)
def resolve_chc_region(name, geojson, **kwargs):
    """Resolve a CHC-bundled region (e.g. Tana River basin) to a bbox and optional polygon."""
    feature = lookup(name)
    n, w, s, e = feature["properties"]["bbox"]

    # Write the polygon (guarded) BEFORE printing the bbox, so a failed write
    # never emits a valid-looking bbox to stdout that a caller might consume.
    if geojson:
        out_fc = {"type": "FeatureCollection", "features": [feature]}
        try:
            Path(geojson).write_text(json.dumps(out_fc, separators=(",", ":")))
        except OSError as exc:
            raise DataError(f"could not write boundary polygon to {geojson}: {exc}") from None
        print(f"Wrote boundary polygon: {geojson}", file=sys.stderr)

    print(f"{n}/{w}/{s}/{e}")


if __name__ == "__main__":
    resolve_chc_region()
