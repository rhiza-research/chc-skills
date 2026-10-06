---
name: resolve-kenya-regions
description: Resolve a Kenya region — the Tana River basin and Kenya's official KNSDI national and county boundaries — to a lat/lon bbox and optionally its boundary polygon GeoJSON. Use this, not resolve-region, whenever a task names the Tana River basin / Tana basin / Tana catchment (resolve-region has no basins and its Nominatim fallback returns an unrelated basin), or needs the Kenya boundary or Kenya county lines for a Kenya map (--counties-geojson for plot outline layers). Other countries, single counties (including Tana River County), named regions, and landmarks go to resolve-region; bare "Tana River" is ambiguous, so ask.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# resolve-kenya-regions

Look up a bounding box and boundary polygon for the Kenya regions bundled
here: the **Tana River basin** and **Kenya** (official KNSDI national
outline, plus all 47 county boundaries).
The output matches weather-skills `resolve-region`: stdout is an `N/W/S/E`
bbox for `--bbox` flags, and `--geojson` writes a single-feature
`FeatureCollection` for `clip-region --geojson` or `plot`'s
`geo.mask_geojson`.

## When to use this vs resolve-region

Use this skill **only** for the regions it bundles (see Regions below). For
everything else, weather-skills `resolve-region` is the right tool.

| The user asks for… | Use |
|---|---|
| Tana River **basin** / Tana basin / Tana catchment | `resolve-kenya-regions` (this skill) |
| The Kenya boundary, or Kenya county lines on a map | `resolve-kenya-regions Kenya` (this skill) |
| Tana River **County** (the administrative unit) | `resolve-region kenya-tana_river` |
| Another country, multi-country region, one county / state by name, or landmark | `resolve-region` |
| Any other river basin or catchment | Neither — ask the user for a boundary file and pass it to `clip-region --geojson` |

Never send a basin name to `resolve-region`. It has no basins, so the name
falls through to Nominatim, which silently returns whatever OSM ranks first:
`resolve-region "Tana River basin"` gives the Upper Agno River Basin in the
Philippines.

Both skills print the same `N/W/S/E` bbox and write the same single-feature
`FeatureCollection` with `--geojson`, so downstream steps (`clip-region`,
`plot`, fetchers' `--bbox`) do not care which one produced it.

Typical use: basin-masked CHIRPS / CHIRPS-GEFS / forecast values — fetch
over the bbox, then clip with the polygon.

### Tana River vs Tana River County

Bare "Tana River" exits 2: it could be the basin or **Tana River County**
(geoBoundaries ADM1 `kenya-tana_river`), a much smaller administrative unit
on the lower river. Pass `Tana River basin` for the basin, or
`resolve-region kenya-tana_river` for the county. Ask the user when the
intent is unclear.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py <NAME> [--geojson PATH]
```

```bash
BBOX=$(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py "Tana River basin" --geojson /tmp/tana.json)
# BBOX is 0.48/36.5972/-3.0402/41.5605
# then e.g. clip-region --bbox "$BBOX" --geojson /tmp/tana.json ...
```

Kenya map with KNSDI county lines and the Tana basin (KMSA rainfall colors):

```bash
BBOX=$(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py Kenya --geojson /tmp/kenya.json --counties-geojson /tmp/kenya_counties.json)
uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py "Tana basin" --geojson /tmp/tana.json
# plot --layer heatmap:totals.zarr --layer outline:/tmp/kenya_counties.json --layer outline:/tmp/tana.json \
#   --spec '{"theme": {"colormap": "kmsa"}, "geo": {"overlays": {"admin1": false}},
#            "layers": [{}, {"line": {"color": "0.3", "linewidth": 0.5}}, {"line": {"linewidth": 1.6}}]}'
```

Turn off `admin1` so the Natural Earth admin-1 lines do not double the KNSDI county lines.

### Arguments

- `name` (positional) — region name or alias, case-insensitive:
  `Tana River basin`, `Tana basin`, `Tana River catchment`, `Tana catchment`;
  `Kenya`, `KEN`, `Kenya KNSDI`, `Kenya counties`.
- `--geojson` — optional path; writes the boundary polygon (in addition to
  printing the bbox).
- `--counties-geojson` — optional path (Kenya only); writes a
  `FeatureCollection` with one polygon per county (47 features, properties
  `county_index` and `source`; the source file had no county names). Use it
  as a `plot --layer outline:` file to draw county lines.

### Output

- stdout: one line, `N/W/S/E` in decimal degrees.
- stderr: `Wrote boundary polygon: PATH` when `--geojson` is given.
- `--geojson PATH`: `FeatureCollection` with one Polygon (Kenya: MultiPolygon, for islands) feature. Properties:
  `iso3`, `name`, `region_name`, `level` (`basin`), `country`, `bbox`
  (`N, W, S, E`), `source`, `area_km2`, `citation`.
- Unknown or ambiguous names exit 2 with an explanation on stderr.

## Regions

| Name | Aliases | bbox (N/W/S/E) | Area |
|---|---|---|---|
| Tana River basin (Kenya) | Tana basin, Tana River catchment, Tana catchment | 0.48/36.5972/-3.0402/41.5605 | ~126,000 km² |
| Kenya (KNSDI) | KEN, Kenya KNSDI, Kenya counties | 5.4141/33.9098/-4.7053/41.906 | ~591,500 km² |

## Data

**Tana River basin.** `data/tana_river_basin.geojson`: converted from the
supplied `Tana Basin.shp` (single polygon, WGS84 lon/lat; no `.prj` or
attribute table came with it). Coordinates rounded to 4 decimals (~11 m).
It reaches farther east (to ~41.56° E) than the earlier HydroBASINS
dissolve, which covered ~95,250 km².

**Kenya.** `data/kenya_knsdi.geojson` (national outline, the union of the
counties) and `data/kenya_counties_knsdi.geojson` (47 county polygons):
converted from the supplied Kenya National Spatial Data Infrastructure
(KNSDI) `Kenya_Counties_KNSDI.shp` (WGS84 lon/lat, no attribute table, so
no county names). Simplified with a shared-edge-preserving coverage
simplification at 0.001° (~100 m) and rounded to 4 decimals, so neighboring
counties still meet exactly. The outline drops the ~2,300 sliver holes
that gaps between source counties leave in the union (Kenya has no enclaves). Area is computed from the unsimplified source.
