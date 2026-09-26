---
name: resolve-chc-region
description: Resolve a CHC-bundled hydrological region — currently only the Tana River basin (Kenya) — to a lat/lon bbox and optionally its boundary polygon GeoJSON. Use this, not resolve-region, whenever a task names the Tana River basin / Tana basin / Tana catchment (resolve-region has no basins and its Nominatim fallback returns an unrelated basin), e.g. to clip or mask CHIRPS or forecasts to the basin with clip-region or plot. Countries, counties (including Tana River County), named regions, and landmarks go to resolve-region; bare "Tana River" is ambiguous, so ask.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# resolve-chc-region

Look up a bounding box and boundary polygon for a region bundled with the
CHC skills. Today that is one hydrological basin, the **Tana River basin**.
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
| Tana River **County** (the administrative unit) | `resolve-region kenya-tana_river` |
| A country, multi-country region, county / state, or landmark | `resolve-region` |
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
# BBOX is 0.4879/36.5667/-3.0297/40.5667
# then e.g. clip-region --bbox "$BBOX" --geojson /tmp/tana.json ...
```

### Arguments

- `name` (positional) — region name or alias, case-insensitive:
  `Tana River basin`, `Tana basin`, `Tana River catchment`, `Tana catchment`.
- `--geojson` — optional path; writes the boundary polygon (in addition to
  printing the bbox).

### Output

- stdout: one line, `N/W/S/E` in decimal degrees.
- stderr: `Wrote boundary polygon: PATH` when `--geojson` is given.
- `--geojson PATH`: `FeatureCollection` with one Polygon feature. Properties:
  `iso3`, `name`, `region_name`, `level` (`basin`), `country`, `bbox`
  (`N, W, S, E`), `source`, `area_km2`, `citation`.
- Unknown or ambiguous names exit 2 with an explanation on stderr.

## Regions

| Name | Aliases | bbox (N/W/S/E) | Area |
|---|---|---|---|
| Tana River basin (Kenya) | Tana basin, Tana River catchment, Tana catchment | 0.4879/36.5667/-3.0297/40.5667 | ~95,250 km² |

## Data

**Tana River basin.** `data/tana_river_basin.geojson`: dissolve of the 33
HydroBASINS v1c (standard) Africa level-7 sub-basins sharing `MAIN_BAS`
1070008470, from the Aberdares and Mt Kenya to the coast at Kipini.
Coordinates rounded to 4 decimals (~11 m). The edges follow the HydroSHEDS
DEM, so this is **not** the FEWS NET / EWX `Tana_River_Basin` outline and
may differ from it, mostly in the flat lower basin. Replace the file if the
official outline is adopted.

HydroBASINS is free for scientific and non-commercial use with attribution:
Lehner, B., Grill, G. (2013). Global river hydrography and network routing:
baseline data and new approaches to study the world's large river systems.
*Hydrological Processes*, 27(15): 2171–2186. <https://www.hydrosheds.org>
