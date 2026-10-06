---
name: chc-plot-theme
description: Theme file with CHC and KMSA rainfall colormaps for the weather-skills plot skills. Use when a map should use the CHC precipitation classes (chc_precip_<window>, chc_precip_anom_<window>) or look like a Kenya Meteorological Department (KMSA) rainfall map (kmsa). Prints the theme path to pass as plot --theme-file, then name the colormap in theme.colormap.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# chc-plot-theme

A plot theme file with the CHC and KMSA rainfall colormaps. The script prints
the file's path; pass it to `plot` (or `plot-timeseries`, `plot-verify`) with
`--theme-file`, then name a colormap from it in `--spec`.

## Usage

```bash
THEME=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py)
plot -i totals.zarr -o map.png --theme-file "$THEME" \
  --spec '{"theme": {"colormap": "kmsa"}, "layout": {"colorbar": {"extend": "max"}}}'
```

A theme file cannot set colorbar options, so set `layout.colorbar.extend` in
`--spec` yourself: `max` for `kmsa`, `both` for the CHC palettes.

To have these colormaps on every plot without `--theme-file`, copy the file to
`~/.config/weather-skills/theme.json`.

## Colormaps

All are for period totals or anomalies in mm: run `aggregate-temporal` and
`convert-to-totals` first.

| Name | Draws | Colorbar extend |
|---|---|---|
| `kmsa` | KMSA rainfall-map classes: < 1, 2–10, 11–20, 21–50, 51–70, 71–100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange) | `max` |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals: white/beige below 5 mm, then greens and blues, up to 50 / 200 / 400 / 1000 mm | `both` |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies: red/brown (dry) through white near 0 to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm | `both` |

Pick the CHC window that matches the data's `aggregation_period` (under 2
days: daily; under 10: week; under 40: month; longer: season). The plot
skill's built-in `default_precip` / `default_precip_anom` are the same
palettes and choose the window automatically, without a theme file.

## Data

`data/chc_theme.json` holds one `colormaps` entry per name, each as packed
`colors` (under color, one color per class, over color) and class `bounds`.

- **KMSA**: colors sampled from a published Kenya Meteorological Department
  rainfall map; they match the standard ArcGIS swatches.
- **CHC**: copied from the weather-skills-plotting nested precipitation
  palettes (`ppt_*` / `ppt_anom_*`), the CHC `ppt_total` and anomaly classes.
