---
name: resolve-chc-region
description: Resolve a CHC region — the Tana River basin (a supplied ~126,000 km² outline, larger than HydroBASINS, so basin totals differ from other basin products), Kenya's official KNSDI national and county boundaries, the Kenya OND briefing box (central/eastern Kenya, 5°S–5°N, 36.5–42°E), and East Africa clipped at 15°S — to a lat/lon bbox and optionally its boundary polygon GeoJSON. Use this, not resolve-region, whenever a task names the Tana River basin / Tana basin / Tana catchment (resolve-region has no basins and its Nominatim fallback returns an unrelated basin), or needs the Kenya boundary or Kenya county lines for a Kenya map (--counties-geojson for plot outline layers), or names the Kenya OND region / Kenya OND (briefing) box / CE Kenya, or East Africa for a CHC briefing map (resolve-region's East Africa is the full UN Eastern Africa grouping, down to Madagascar and Zimbabwe). Other countries, single counties (including Tana River County), named regions, and landmarks go to resolve-region; bare "Tana River" is ambiguous, so ask.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# resolve-chc-region

Look up a bounding box and boundary polygon for the CHC regions bundled
here: the **Tana River basin**, **Kenya** (official KNSDI national
outline, plus all 47 county boundaries), the **Kenya OND region**
briefing box, and **East Africa** clipped at 15°S.
The output matches weather-skills `resolve-region`: stdout is an `N/W/S/E`
bbox for `--bbox` flags, and `--geojson` writes a single-feature
`FeatureCollection` for `clip-region --geojson` or `plot`'s
`geo.mask_geojson`.

## When to use this vs resolve-region

Use this skill **only** for the regions it bundles (see Regions below). For
everything else, weather-skills `resolve-region` is the right tool.

| The user asks for… | Use |
|---|---|
| Tana River **basin** / Tana basin / Tana catchment | `resolve-chc-region` (this skill) |
| The Kenya boundary, or Kenya county lines on a map | `resolve-chc-region Kenya` (this skill) |
| Kenya OND region / Kenya OND briefing box / CE Kenya | `resolve-chc-region "Kenya OND region"` (this skill) |
| East Africa for a CHC briefing (stops at 15°S) | `resolve-chc-region "East Africa"` (this skill) |
| Eastern Africa, the full UN grouping | `resolve-region "Eastern Africa"` |
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

For a Kenya map with KNSDI county lines and the Tana basin outline, write
both polygons and pass them as `plot --layer outline:` files; the full KMSA
rainfall map recipe (colors, layer styles, `admin1` off) is in
`chc-plot-theme`:

```bash
BBOX=$(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py Kenya --geojson /tmp/kenya.json --counties-geojson /tmp/kenya_counties.json)
uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py "Tana basin" --geojson /tmp/tana.json
```

### Arguments

- `name` (positional) — region name or alias, case-insensitive:
  `Tana River basin`, `Tana basin`, `Tana River catchment`, `Tana catchment`;
  `Kenya`, `KEN`, `Kenya KNSDI`, `Kenya counties`;
  `Kenya OND region`, `Kenya OND`, `Kenya OND box`, `Kenya OND briefing box`,
  `OND Kenya`, `Central-Eastern Kenya`,
  `Central and Eastern Kenya`, `CE Kenya`;
  `East Africa`.
- `--geojson` — optional path; writes the boundary polygon (in addition to
  printing the bbox).
- `--counties-geojson` — optional path (Kenya only); writes a
  `FeatureCollection` with one polygon per county (47 features, properties
  `county_index` and `source`; the source file had no county names). Use it
  as a `plot --layer outline:` file to draw county lines.

### Output

- stdout: one line, `N/W/S/E` in decimal degrees.
- stderr: `Wrote boundary polygon: PATH` when `--geojson` is given.
- `--geojson PATH`: `FeatureCollection` with one Polygon (Kenya, East Africa: MultiPolygon) feature. Properties:
  `iso3`, `name`, `region_name`, `level` (`basin`, `country`, `custom`, or `region`), `country`, `bbox`
  (`N, W, S, E`), `source`, `area_km2`, `citation`.
- Unknown or ambiguous names exit 2 with an explanation on stderr.

## Regions

| Name | Aliases | bbox (N/W/S/E) | Area |
|---|---|---|---|
| Tana River basin (Kenya) | Tana basin, Tana River catchment, Tana catchment | 0.48/36.5972/-3.0402/41.5605 | ~126,000 km² |
| Kenya (KNSDI) | KEN, Kenya KNSDI, Kenya counties | 5.4141/33.9098/-4.7053/41.906 | ~591,500 km² |
| Kenya OND region | Kenya OND, Kenya OND box, Kenya OND briefing box, OND Kenya, Central-Eastern Kenya, Central and Eastern Kenya, CE Kenya | 5.0/36.5/-5.0/42.0 | rectangle |
| East Africa | — | 17.9983/21.9035/-15.0/51.1339 | Eastern Africa south to 15°S |

## Data

Each region is `data/<key>.geojson`, a single-feature FeatureCollection whose
properties (name, level, country, source, ...) are what `--geojson` writes;
the script only adds `bbox`. To add a region, drop in its file and list its
names in `REGIONS` in `scripts/resolve.py`.

**Tana River basin.** `data/tana_river_basin.geojson`: converted from
`Tana Basin.shp`, supplied to this project together with the KNSDI Kenya
county files (single polygon, WGS84 lon/lat). It came with no `.prj`,
attribute table, or metadata, so the agency that delineated it is not
recorded here; the feature's `source` property says "User-supplied". It is
a broader delineation than the standard hydrological products: ~126,000 km²,
reaching ~41.56° E along the Kenya–Somalia border, against ~95,250 km² for
the HydroBASINS v1c Africa level-7 dissolve (`MAIN_BAS` 1070008470) this
skill bundled before. Basin-mean or basin-total rainfall therefore differs from numbers
computed on those outlines; say which outline you used when comparing.
Coordinates rounded to 4 decimals (~11 m).

**Kenya.** `data/kenya.geojson` (national outline, the union of the
counties) and `data/kenya_counties.geojson` (47 county polygons):
converted from the supplied Kenya National Spatial Data Infrastructure
(KNSDI) `Kenya_Counties_KNSDI.shp` (WGS84 lon/lat, no attribute table, so
no county names). Simplified with a shared-edge-preserving coverage
simplification at 0.001° (~100 m) and rounded to 4 decimals, so neighboring
counties still meet exactly. The outline drops the ~2,300 sliver holes
that gaps between source counties leave in the union (Kenya has no enclaves). Area is computed from the unsimplified source.

**Kenya OND region.** `data/kenya_ond_region.geojson`: the rectangle
5°S–5°N, 36.5–42°E (central and eastern Kenya, east of the Rift) used as
the analog / verification box in CHC Kenya OND briefings. It is a plain
box, not a county union; `level` is `custom`.

**East Africa.** `data/east_africa.geojson`: the Natural Earth 110m
Eastern Africa subregion (the countries weather-skills-core bundles)
clipped at 15°S, a Greater Horn view that cuts off the parts of
Madagascar, Mozambique, Malawi, Zambia, and Zimbabwe south of that line. Coordinates rounded to 4
decimals; `level` is `region`.
