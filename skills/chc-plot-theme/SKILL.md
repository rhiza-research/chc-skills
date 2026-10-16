---
name: chc-plot-theme
description: KMSA plot theme and CHC/KMSA rainfall colormaps for the weather-skills plot skills. Use when a rainfall map should look like a Kenya Meteorological Department (KMSA) map (--theme kmsa, a theme file for plot --theme-file), or should use the CHC precipitation classes (--colormap chc_precip_<window> / chc_precip_anom_<window>, an object for a trace's meta.palette).
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  version: "0.0.1"
  catalog-group: agent-tooling
---

# chc-plot-theme

`--theme kmsa` prints the path of the KMSA plot theme file. Pass that path to
`plot --theme-file` (or `plot-timeseries`, `plot-verify`). Rainfall totals then
draw in the KMSA classes, with KMSA's legend labels, Arial text, black national
borders and dark county lines. You don't need to change the spec.

`--colormap NAME` prints one colormap as a line of JSON (`name`, `colors`,
`bounds`) to use as a trace's `meta.palette`.

## Usage

KMSA rainfall map of period totals (run `aggregate-temporal` and
`convert-to-totals` first):

```bash
plot -i totals.zarr -o map.png \
  --theme-file "$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --theme kmsa)"
```

The theme only changes rainfall totals. Anomalies, temperature and other fields
keep the standard colors. To use the KMSA classes on just one trace, the
theme file also names the palette `kmsa`: set `"meta": {"palette": "kmsa"}`.

CHC colormap on one trace:

```bash
PAL=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_week)
plot -i totals.zarr -o map.png \
  --spec "{\"data\": [{\"type\": \"heatmap\", \"meta\": {\"palette\": $PAL}}]}"
```

Pass the object, not just the name: `plot` has no `chc_precip_*` palette of
its own.

## Themes

| Name | Draws |
|---|---|
| `kmsa` | Rainfall totals in the KMSA classes < 1, 2 - 10, 11 - 20, 21 - 50, 51 - 70, 71 - 100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange), one labelled legend box per class; Arial text; black national borders, dark grey county lines |

## Colormaps

All are for period totals or anomalies in mm.

| Name | Draws |
|---|---|
| `kmsa` | The KMSA classes above, with a white under and red-orange over color |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals: white/beige below 5 mm, then greens and blues, up to 50 / 200 / 400 / 1000 mm |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies: red/brown (dry) through white near 0 to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm |

Pick the CHC window that matches the data's `aggregation_period` (under 2
days: daily; under 10: week; under 40: month; longer: season). The plot
skill's built-in `default_precip` / `default_precip_anom` are the same
palettes and choose the window automatically.

## Data

- `data/kmsa_theme.json` is the KMSA plot theme: `template` (fonts), `palettes.kmsa`, `defaults.precip` and `overlays` (base-map lines).
- `data/chc_theme.json` holds one `colormaps` entry per `--colormap` name, as packed `colors` (under color, one color per class, over color) and class `bounds`.
- **KMSA** colors are sampled from a published Kenya Meteorological Department rainfall map; they match the standard ArcGIS swatches.
- **CHC** colors are copied from the weather-skills-plotting nested precipitation palettes (`ppt_*` / `ppt_anom_*`), the CHC `ppt_total` and anomaly classes.
