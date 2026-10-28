# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/weather-skills/weather-skills-core@dev",
# ]
# ///
"""Resolve a bundled CHC region to a lat/lon bounding box and optional boundary GeoJSON."""

import json
import sys
from pathlib import Path

from weather_skills_core import DataError, UsageError, provenance, weather_skill
from weather_skills_core.region import bbox_from_geometry, clean_region_name

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

DATA = Path(__file__).resolve().parent.parent / "data"

# Region key (data/<key>.geojson) -> accepted names, as clean_region_name spells them.
REGIONS = {
    "east_africa": ("east_africa",),
    "kenya": ("kenya", "ken", "kenya_knsdi", "kenya_counties"),
    "kenya_ond_region": (
        "kenya_ond_region",
        "kenya_ond",
        "kenya_ond_box",
        "kenya_ond_briefing_box",
        "ond_kenya",
        "central_eastern_kenya",
        "central-eastern_kenya",
        "central_and_eastern_kenya",
        "ce_kenya",
    ),
    "tana_river_basin": (
        "tana_river_basin",
        "tana_basin",
        "tana_river_catchment",
        "tana_catchment",
    ),
}
ALIASES = {alias: key for key, aliases in REGIONS.items() for alias in aliases}


def load(name):
    """Return ``(key, FeatureCollection)`` for a region name; the feature gets a ``bbox``."""
    cleaned = clean_region_name(name)
    if cleaned == "tana_river":
        raise UsageError(
            "'Tana River' is ambiguous. For the hydrological basin pass 'Tana River basin'; "
            "for Tana River County use the resolve-region skill with kenya-tana_river."
        )
    if cleaned not in ALIASES:
        raise UsageError(
            f"{name!r} is not bundled with resolve-chc-region (known: 'Kenya', "
            "'Tana River basin', 'Kenya OND region', 'East Africa'). For single counties (e.g. kenya-nairobi), "
            "other countries, and landmarks use the resolve-region skill."
        )
    key = ALIASES[cleaned]
    fc = json.loads((DATA / f"{key}.geojson").read_text())
    feature = fc["features"][0]
    feature["properties"]["bbox"] = list(bbox_from_geometry(feature["geometry"]))
    return key, fc


def write(path, text, what, history):
    """Write ``text`` to ``path`` and stamp it with ``history``."""
    try:
        Path(path).write_text(text)
    except OSError as exc:
        raise DataError(f"could not write {what} to {path}: {exc}") from None
    provenance.stamp_figure(Path(path), history)
    print(f"Wrote {what}: {path}", file=sys.stderr)


@weather_skill(name="resolve-chc-region", version=_SKILL_VERSION, output=False)
@weather_skill.argument(
    "name",
    help="Region name or alias (e.g. 'Kenya', 'Tana River basin', 'Kenya OND region', 'East Africa')",
)
@weather_skill.argument("--geojson", help="Optional path: write the boundary polygon as GeoJSON")
@weather_skill.argument(
    "--counties-geojson",
    help="Optional path: write one polygon per county (Kenya only), e.g. for plot outline layers",
)
def resolve_chc_region(name, geojson, counties_geojson, **kwargs):
    """Resolve a bundled CHC region to a lat/lon bounding box and optional boundary GeoJSON."""
    for flag, path in (("--geojson", geojson), ("--counties-geojson", counties_geojson)):
        if path and not provenance.can_replace_output(Path(path)):
            raise UsageError(
                f"{flag} {path} already exists and was not written by a weather skill, "
                "so it may be your own data; pass a new path"
            )
    key, fc = load(name)
    history = [
        provenance.build_entry(
            "resolve-chc-region",
            _SKILL_VERSION,
            {"name": name},
            None,
            revision=provenance.resolve_skill_revision(__file__),
        )
    ]

    # Write files before printing, so a failed write never leaves a bbox on stdout.
    if counties_geojson:
        counties = DATA / f"{key}_counties.geojson"
        if not counties.exists():
            raise UsageError(f"{name!r} has no county boundaries; only Kenya does.")
        write(counties_geojson, counties.read_text(), "county boundaries", history)
    if geojson:
        write(geojson, json.dumps(fc, separators=(",", ":")), "boundary polygon", history)

    n, w, s, e = fc["features"][0]["properties"]["bbox"]
    print(f"{n}/{w}/{s}/{e}")


if __name__ == "__main__":
    resolve_chc_region()
