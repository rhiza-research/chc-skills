# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@dev",
# ]
# ///
"""Resolve a Kenya region (Tana River basin, Kenya KNSDI) to a bbox and optional polygon."""

import json
import sys
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.region import bbox_from_geometry, clean_region_name

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Bundled regions. ``geojson`` is a single-feature FeatureCollection under
# ``data/``; ``counties`` (optional) holds one feature per admin-1 unit.
# Aliases are passed through :func:`clean_region_name`.
_REGIONS = {
    "kenya": {
        "name": "Kenya",
        "geojson": "kenya_knsdi.geojson",
        "counties": "kenya_counties_knsdi.geojson",
        "level": "country",
        "iso3": "KEN",
        "country": "Kenya",
        "aliases": ("KEN", "Kenya KNSDI", "Kenya counties"),
    },
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


def _spec(name):
    cleaned = clean_region_name(name)
    if cleaned in _AMBIGUOUS:
        raise UsageError(_AMBIGUOUS[cleaned])
    spec = _index().get(cleaned)
    if spec is None:
        known = ", ".join(repr(s["name"]) for s in _REGIONS.values())
        raise UsageError(
            f"{name!r} is not bundled with resolve-kenya-regions (known: {known}). "
            "For single counties (e.g. kenya-nairobi), other countries, and landmarks "
            "use the resolve-region skill."
        )
    return spec


def lookup(name):
    """Return the GeoJSON Feature for a bundled region name or alias."""
    spec = _spec(name)
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


def counties(name):
    """Return the admin-1 FeatureCollection bundled with a region."""
    spec = _spec(name)
    if not spec.get("counties"):
        raise UsageError(f"{spec['name']} has no bundled county boundaries.")
    with (_DATA_DIR / spec["counties"]).open(encoding="utf-8") as fh:
        return json.load(fh)


def _write(path, fc, what):
    try:
        Path(path).write_text(json.dumps(fc, separators=(",", ":")))
    except OSError as exc:
        raise DataError(f"could not write {what} to {path}: {exc}") from None
    print(f"Wrote {what}: {path}", file=sys.stderr)


@weather_skill(
    name="resolve-kenya-regions",
    version=_SKILL_VERSION,
    output=False,
)
@weather_skill.argument(
    "name",
    help="Kenya region name or alias (e.g. 'Tana River basin', 'Kenya')",
)
@weather_skill.argument(
    "--geojson",
    help="Optional path: write the boundary polygon as GeoJSON",
)
@weather_skill.argument(
    "--counties-geojson",
    help="Optional path: write one polygon per county (Kenya only), e.g. for plot outline layers",
)
def resolve_kenya_regions(name, geojson, counties_geojson=None, **kwargs):
    """Resolve a Kenya region (Tana River basin, Kenya KNSDI) to a bbox and optional polygon."""
    feature = lookup(name)
    n, w, s, e = feature["properties"]["bbox"]

    # Write the polygons (guarded) BEFORE printing the bbox, so a failed write
    # never emits a valid-looking bbox to stdout that a caller might consume.
    if counties_geojson:
        _write(counties_geojson, counties(name), "county boundaries")
    if geojson:
        _write(geojson, {"type": "FeatureCollection", "features": [feature]}, "boundary polygon")

    print(f"{n}/{w}/{s}/{e}")


if __name__ == "__main__":
    resolve_kenya_regions()
