---
name: resolve-chc-region
description: Resolve a CHC-bundled hydrological region — currently the Tana River basin (Kenya) — to a lat/lon bbox and optionally its boundary polygon GeoJSON. Use when a task names the Tana River basin / Tana basin / Tana catchment, e.g. to clip or mask CHIRPS or forecasts to the basin with clip-region or plot. Bare "Tana River" is ambiguous with Tana River County; countries, counties, and landmarks go to resolve-region instead.
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

## When to use

- The user names the Tana River basin, Tana basin, or Tana catchment.
- A pipeline needs basin-masked CHIRPS / CHIRPS-GEFS / forecast values
  (fetch over the bbox, then clip with the polygon).

Countries, Kenyan counties, named multi-country regions, and landmarks are
**not** here — use `resolve-region`.

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
