---
name: chc-plot-theme
description: "Print the path of the Kenya Meteorological Department (KMSA) plot theme file for plot --theme-file, or a CHC or KMSA rainfall colormap as a JSON object for a plot trace's meta.palette, choosing the CHC window (daily, week, month, season) from a window length or a Zarr's aggregation period. Use when a rainfall map should look like a KMSA Kenya rainfall map or use the CHC precipitation classes."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# chc-plot-theme

Prints the path of the KMSA plot theme file, or one rainfall colormap as a single line of JSON (`name`, `colors`, `bounds`), for the weather-skills `plot` skills.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --theme kmsa
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap NAME [--window-days N | --for PATH.zarr]
```

### Arguments
- `--theme` — `kmsa`: print the path of the KMSA theme file. Pass the path to `plot --theme-file` (also `plot-timeseries`, `plot-verify`). Exactly one of `--theme` and `--colormap` is required.
- `--colormap` — a name from the table below, or the family `chc_precip` / `chc_precip_anom` with `--window-days` or `--for` to pick the window.
- `--window-days` — window length in days; only with a family name: under 2 → `daily`, up to 10 (a week or dekad) → `week`, under 40 → `month`, longer → `season`. stderr names the choice.
- `--for` — Zarr you will plot, only with a family name; the window comes from its data variables' `aggregation_period`. Exits 2 unless exactly one period is found. `--window-days` wins when both are given.

| Theme | Draws |
|---|---|
| `kmsa` | Rainfall totals in the KMSA classes < 1, 2 - 10, 11 - 20, 21 - 50, 51 - 70, 71 - 100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange), one labelled legend box per class; Arial text; black national borders, dark grey county lines. Other fields keep the standard colors. |

| Colormap | Classes |
|---|---|
| `kmsa` | KMSA rainfall-map classes, edges 1, 10, 20, 50, 70, 100 mm, white under and red-orange over |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals, white/beige at the low end through greens and blues, top edge 50 / 200 / 400 / 1000 mm |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies, brown/red (dry) through white (−10 to +10 mm) to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm |

### Output
`--theme`: one path on stdout. `--colormap`: one JSON object on stdout.

### Notes
- The theme and colormaps are for period totals or anomalies in mm, so aggregate and convert to totals before plotting.
- With `--theme-file`, rainfall totals get the KMSA classes with no spec changes. The file also names the palette `kmsa`, so `"meta": {"palette": "kmsa"}` puts it on one trace.
- Put the whole `--colormap` object, not its name, in a trace's `meta.palette`; `plot` has no `kmsa` or `chc_precip_*` palette of its own.
- The lowest KMSA class and the near-zero CHC anomaly class are white, like masked cells; say so in a caption when the map is masked.
- Neither carries dates; put the period in the title.

### Example
KMSA-style Kenya map with county lines and the Tana basin outline:
```bash
THEME=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --theme kmsa)
plot -i totals_kenya.zarr --layer outline:kenya_counties.json --layer outline:tana.json -o kenya_kmsa.png \
  --theme-file "$THEME" \
  --spec '{"layout": {"title": {"text": "Rainfall 21–30 Sep 2026"}},
           "data": [{"uid": "outline1", "line": {"color": "#4d4d4d", "width": 0.8}},
                    {"uid": "outline2", "line": {"width": 1.6}}]}'
```
Draws dekad totals in KMSA classes, with thin grey county lines and a heavier basin outline. Outline layers are traces `outline1`, `outline2`, … in `--layer` order; style them by that `uid`. An outline turns off the Natural Earth admin-1 lines, so they do not double the county lines. The county and basin GeoJSON come from `resolve-chc-region Kenya --counties-geojson kenya_counties.json` and `resolve-chc-region "Tana basin" --geojson tana.json`.

## Details

For a CHC window picked from the data:
```bash
PAL=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_anom --window-days 15)
plot -i anom.zarr -o anom.png --spec "{\"data\": [{\"type\": \"heatmap\", \"meta\": {\"palette\": $PAL}}]}"
```
gives `chc_precip_anom_month` for a 15-day outlook.

The CHC classes are the same palettes as `plot`'s built-in `default_precip` / `default_precip_anom`; this skill lets you choose the window yourself (e.g. a dekad on the week classes) or use the KMSA classes.

`data/kmsa_theme.json` is the KMSA plot theme: a Plotly `template` (fonts), `palettes.kmsa` (one color and legend label per class), `defaults.precip` and `overlays` (base-map line styles). `data/chc_theme.json` holds one `colormaps` entry per name: packed `colors` (under color, one per class, over color) and class `bounds`. KMSA colors are sampled from a published Kenya Meteorological Department rainfall map and match the standard ArcGIS swatches. CHC colors are the weather-skills-plotting nested precipitation palettes (`ppt_*` / `ppt_anom_*`).
