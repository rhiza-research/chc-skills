---
name: chc-plot-theme
description: CHC and KMSA rainfall colormaps for the weather-skills plot skills, plus the full KMSA-style Kenya map recipe. Use when a map should use the CHC precipitation classes (chc_precip_<window>, chc_precip_anom_<window>) or look like a Kenya Meteorological Department (KMSA) rainfall map (kmsa). Prints one colormap as a JSON object; put the object (not the name) in plot --spec as theme.colormap. Let it pick the CHC window with --colormap chc_precip|chc_precip_anom plus --for <zarr> (reads aggregation_period) or --window-days N; a 10-day dekad gets the week classes. Colorbar extend: max for totals, both for anomalies.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# chc-plot-theme

The CHC and KMSA rainfall colormaps. `--colormap NAME` prints that colormap
as one line of JSON (`name`, `colors`, `bounds`). Put that object in `--spec`
as `theme.colormap` for `plot` (or `plot-timeseries`, `plot-verify`).

## Usage

```bash
# CHC totals: let the skill pick the window from the Zarr you will plot
CMAP=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip --for totals.zarr)
plot -i totals.zarr -o map.png \
  --spec "{\"theme\": {\"colormap\": $CMAP}, \"layout\": {\"colorbar\": {\"extend\": \"max\"}}}"

# CHC anomalies for a 15-day window (e.g. a SubC 15d outlook)
CMAP=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_anom --window-days 15)
# ... "layout": {"colorbar": {"extend": "both"}}
```

Pass the object, not just the name: `plot` has no `kmsa` or `chc_precip_*`
colormap of its own. Set `layout.colorbar.extend` in `--spec` yourself:
`max` for totals (`kmsa`, `chc_precip_*`; rain cannot go below 0), `both` for
anomalies (`chc_precip_anom_*`).

### Arguments

- `--colormap` (required) — a full name from the table below, or the family
  `chc_precip` / `chc_precip_anom` together with `--window-days` or `--for`.
- `--window-days N` — totals window length in days; picks the CHC window:
  under 2 days `daily`; up to and including 10 days (a week or a dekad)
  `week`; under 40 days `month`; longer `season`. stderr names the choice.
- `--for PATH.zarr` — read the window from the data variables'
  `aggregation_period` attr (stamped by `aggregate-temporal`, and by
  `subc-mme-fetch` for its outlook windows). Errors if the Zarr has none or
  several; pass `--window-days` then.

## KMSA-style Kenya rainfall map

Totals over Kenya with the KNSDI county lines and the Tana basin outline
(polygons from `resolve-chc-region`), in KMSA classes:

```bash
BBOX=$(resolve-chc-region Kenya --geojson /tmp/kenya.json --counties-geojson /tmp/kenya_counties.json)
resolve-chc-region "Tana basin" --geojson /tmp/tana.json
# totals.zarr: fetch over $BBOX, then aggregate-temporal and convert-to-totals
CMAP=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap kmsa)
plot --layer heatmap:totals.zarr --layer outline:/tmp/kenya_counties.json --layer outline:/tmp/tana.json -o kenya_kmsa.png \
  --spec "{\"theme\": {\"colormap\": $CMAP}, \"layout\": {\"colorbar\": {\"extend\": \"max\"}},
           \"geo\": {\"overlays\": {\"admin1\": false}},
           \"layers\": [{}, {\"line\": {\"color\": \"0.3\", \"linewidth\": 0.5}}, {\"line\": {\"linewidth\": 1.6}}]}"
```

- Turn off `admin1` so the Natural Earth admin-1 lines do not double the
  KNSDI county lines.
- Thin grey county lines (`0.3`, 0.5 pt) and a heavier basin outline (1.6 pt)
  keep the data readable.
- `extend: max`: the top class (> 100 mm) is open-ended; there is nothing
  below 0.
- Add a period to the title yourself (e.g. `"title": "Rainfall 21–30 Sep 2026"`):
  the colormap carries no dates.
- KMSA's lowest class (< 1 mm) is white, the same as masked or missing cells;
  say so in a caption if the map has a mask.

## Colormaps

All are for period totals or anomalies in mm: run `aggregate-temporal` and
`convert-to-totals` first.

| Name | Draws | Colorbar extend |
|---|---|---|
| `kmsa` | KMSA rainfall-map classes: < 1, 2–10, 11–20, 21–50, 51–70, 71–100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange) | `max` |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals: white/beige below 5 mm, then greens and blues, up to 50 / 200 / 400 / 1000 mm | `max` |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies: red/brown (dry) through white near 0 to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm | `both` |

The CHC totals and anomaly classes are the same palettes as `plot`'s
built-in `default_precip` / `default_precip_anom`, so use this skill when you
want to choose the window yourself (e.g. a dekad on the week classes) or need
the KMSA classes. The CHC anomaly palettes are white from −10 to +10 mm, the
same as masked or missing cells.

## Data

`data/chc_theme.json` holds one `colormaps` entry per name, each as packed
`colors` (under color, one color per class, over color) and class `bounds`.

- **KMSA**: colors sampled from a published Kenya Meteorological Department
  rainfall map; they match the standard ArcGIS swatches.
- **CHC**: copied from the weather-skills-plotting nested precipitation
  palettes (`ppt_*` / `ppt_anom_*`), the CHC `ppt_total` and anomaly classes.
