---
name: chc-plot-theme
description: "Print a CHC or Kenya Meteorological Department (KMSA) rainfall colormap as a JSON object to place in a plot spec, choosing the CHC window (daily, week, month, season) from a window length or a Zarr's aggregation period. Use when a rainfall totals or anomaly map should use the CHC precipitation classes or look like a KMSA Kenya rainfall map."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# chc-plot-theme

Prints one rainfall colormap as a single line of JSON (`name`, `colors`, `bounds`) for the weather-skills `plot` spec.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap NAME [--window-days N | --for PATH.zarr]
```

### Arguments
- `--colormap` — a name from the table below, or the family `chc_precip` / `chc_precip_anom` with `--window-days` or `--for` to pick the window. Required.
- `--window-days` — window length in days; only with a family name: under 2 → `daily`, up to 10 (a week or dekad) → `week`, under 40 → `month`, longer → `season`. stderr names the choice.
- `--for` — Zarr you will plot, only with a family name; the window comes from its data variables' `aggregation_period`. Exits 2 unless exactly one period is found. `--window-days` wins when both are given.

| Name | Classes | `extend` |
|---|---|---|
| `kmsa` | KMSA rainfall-map classes, edges 1, 10, 20, 50, 70, 100 mm (white, pale green, green, light blue, blue, orange, red-orange) | `max` |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals, white/beige at the low end through greens and blues, top edge 50 / 200 / 400 / 1000 mm | `max` |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies, brown/red (dry) through white (−10 to +10 mm) to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm | `both` |

### Output
One JSON object on stdout.

### Notes
- Put the whole object, not its name, in `--spec` as `theme.colormap`; `plot` has no `kmsa` or `chc_precip_*` colormap of its own.
- Set `layout.colorbar.extend` yourself: the `extend` column above.
- The colormaps are for period totals or anomalies in mm, so aggregate and convert to totals before plotting.
- The lowest KMSA class and the near-zero CHC anomaly class are white, like masked cells; say so in a caption when the map is masked.
- The colormap carries no dates; put the period in the title.

### Example
KMSA-style Kenya map with county lines and the Tana basin outline:
```bash
CMAP=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap kmsa)
plot --layer heatmap:totals_kenya.zarr --layer outline:kenya_counties.json --layer outline:tana.json -o kenya_kmsa.png \
  --spec "{\"title\": \"Rainfall 21–30 Sep 2026\", \"theme\": {\"colormap\": $CMAP}, \"layout\": {\"colorbar\": {\"extend\": \"max\"}},
           \"geo\": {\"overlays\": {\"admin1\": false}},
           \"layers\": [{}, {\"line\": {\"color\": \"0.3\", \"linewidth\": 0.5}}, {\"line\": {\"linewidth\": 1.6}}]}"
```
Draws dekad totals in KMSA classes, with thin grey county lines, a heavier basin outline, and the Natural Earth admin-1 lines turned off so they do not double the county lines. The county and basin GeoJSON come from `resolve-chc-region Kenya --counties-geojson kenya_counties.json` and `resolve-chc-region "Tana basin" --geojson tana.json`.

## Details

For a CHC window picked from the data:
```bash
CMAP=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_anom --window-days 15)
```
gives `chc_precip_anom_month` for a 15-day outlook.

The CHC classes are the same palettes as `plot`'s built-in `default_precip` / `default_precip_anom`; this skill lets you choose the window yourself (e.g. a dekad on the week classes) or use the KMSA classes.

`data/chc_theme.json` holds one `colormaps` entry per name: packed `colors` (under color, one per class, over color) and class `bounds`. KMSA colors are sampled from a published Kenya Meteorological Department rainfall map and match the standard ArcGIS swatches. CHC colors are the weather-skills-plotting nested precipitation palettes (`ppt_*` / `ppt_anom_*`).
