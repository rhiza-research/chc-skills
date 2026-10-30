---
name: chc-plot-theme
description: "Print the Kenya Meteorological Department (KMSA) rainfall classes and the CHC precipitation classes as a Jsonnet library of palettes for the plot skill (import it and draw with ws.classBar(chc.kmsa, 'mm')), or one palette as JSON, choosing the CHC window (daily, week, month, season) from a window length or a Zarr's aggregation period. Use when a rainfall map should use the KMSA colormap, look like a KMSA Kenya rainfall map or a classic CHC map, or use the CHC precipitation classes."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  catalog-group: agent-tooling
---

# chc-plot-theme

Prints the KMSA and CHC rainfall palettes as a `.libsonnet` file for the weather-skills `plot` skill, or one palette as a single line of JSON. Each palette is `{colors, bounds}` (KMSA also has `labels`), the form `plot`'s `ws.classBar` and `ws.stepped` take.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py -o chc_palettes.libsonnet
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap NAME [--window-days N | --for PATH.zarr]
```

### Arguments
- *(no `--colormap`)* — print every palette as a Jsonnet library. Save it as `chc_palettes.libsonnet` in your working directory, pass that directory to `plot` with `-J .`, and import it in the spec: `local chc = import 'chc_palettes.libsonnet';`, then `ws.classBar(chc.kmsa, 'mm')`.
- `--colormap` — print only this palette as JSON: `kmsa`, `chc_precip_daily` / `_week` / `_month` / `_season`, `chc_precip_anom_*` (same windows), `chc_ppt_total`, `chc_ppt_poa`, `chc_spi`, or the family `chc_precip` / `chc_precip_anom` with `--window-days` or `--for` to pick the window. JSON is Jsonnet, so the object can go straight into a spec (`ws.classBar($PAL, 'mm')`) or be saved as a `.json` file and imported. Classes are listed under Details.
- `--window-days` — window length in days; only with a family name: under 2 → `daily`, up to 10 (a week or dekad) → `week`, under 40 → `month`, longer → `season`. stderr names the choice.
- `--for` — Zarr you will plot, only with a family name; the window comes from its data variables' `aggregation_period`. Exits 2 unless exactly one period is found. `--window-days` wins when both are given.
- `-o` — write to this file instead of stdout.

### Output
The `.libsonnet` file, or one JSON object, on stdout or at `-o`. The library's keys are the `--colormap` names.

### Notes
- The palettes are for period totals or anomalies in mm, so aggregate and convert to totals before plotting.
- `ws.classBar(chc.kmsa, 'mm')` draws the KMSA legend: one equal box per class, labelled `< 1` … `> 100`.
- `plot` has no `kmsa` or `chc_*` palette of its own: import this library, or paste the `--colormap` object.
- The lowest KMSA class and the near-zero CHC anomaly class are white, like masked cells; say so in a caption when the map is masked.
- Neither carries dates; put the period in the title.
- How specs, `@` references, inputs and `ws.libsonnet` work is in the `plot` skill's SKILL.md; its `recipes/map.jsonnet` is the plain map this example builds on.

### Example
KMSA-style Kenya map of dekad totals with county lines and the Tana basin outline. Save as `kenya_kmsa.jsonnet`:
```jsonnet
local ws = import 'ws.libsonnet';
local chc = import 'chc_palettes.libsonnet';
local bbox = std.extVar('inputs').COUNTIES.bbox;
local outline(name, color, width) = {
  type: 'scatter', mode: 'lines', x: '@%s.x' % name, y: '@%s.y' % name,
  line: { color: color, width: width }, hoverinfo: 'skip', showlegend: false,
};
{
  data: [
    { type: 'heatmap', x: '@OBS.lon', y: '@OBS.lat', z: '@OBS.precip.isel(time=0)', coloraxis: 'coloraxis' },
    outline('COUNTIES', '#4d4d4d', 0.8),
    outline('TANA', 'black', 1.6),
  ] + ws.basemap(bbox, layers=['lakes', 'borders', 'coastline']),
  layout: ws.map(bbox) + ws.classBar(chc.kmsa, 'mm') + {
    font: { family: 'Arial, Helvetica, sans-serif', color: 'black' },
    title: { text: 'Rainfall 21–30 Sep 2026' },
  },
}
```
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py -o chc_palettes.libsonnet
plot -i totals_kenya.zarr=OBS -i kenya_counties.json=COUNTIES -i tana.json=TANA \
  -J . --spec kenya_kmsa.jsonnet -o kenya_kmsa.png
```
Draws the totals in KMSA classes with Arial text, thin grey county lines, a heavier basin outline, and black national borders. `layers` leaves the base map's admin-1 lines out, so they do not double the county lines. The GeoJSON comes from resolve-chc-region: `--counties-geojson kenya_counties.json` for Kenya, `--geojson tana.json` for the Tana basin.

## Details

| Palette | Classes |
|---|---|
| `kmsa` | KMSA rainfall-map classes < 1, 2 - 10, 11 - 20, 21 - 50, 51 - 70, 71 - 100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange), one label per class. The top bound, 1000, only closes the > 100 class; larger totals take its color. |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals, white/beige at the low end through greens and blues, top edge 50 / 200 / 400 / 1000 mm, under and over colors |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies, brown/red (dry) through white (−10 to +10 mm) to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm |
| `chc_ppt_total` | Classic CHC totals, fixed whatever the period: 0, 2, 5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2500 mm |
| `chc_ppt_poa` | Classic CHC percent of normal, 30 – 300 % |
| `chc_spi` | Classic CHC SPI classes, −2.5 to 2.5 |

For a CHC window picked from the data, paste the object into the spec:
```bash
PAL=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_anom --window-days 15)
plot -i anom.zarr=ANOM -o anom.png --spec "local ws = import 'ws.libsonnet';
local bbox = std.extVar('inputs').ANOM.bbox;
{ data: [{ type: 'heatmap', x: '@ANOM.lon', y: '@ANOM.lat', z: '@ANOM.precip.isel(time=0)', coloraxis: 'coloraxis' }] + ws.basemap(bbox),
  layout: ws.map(bbox) + ws.classBar($PAL, 'mm') }"
```
gives `chc_precip_anom_month` for a 15-day outlook.

The CHC classes match `plot`'s `ws.palettes.ppt_*` / `ppt_anom_*`; this skill keeps a copy so CHC maps keep these classes if `plot`'s change, lets you choose the window yourself (e.g. a dekad on the week classes), and adds the KMSA classes.

`data/chc_palettes.json` holds every palette under its `--colormap` name. KMSA colors are sampled from a published Kenya Meteorological Department rainfall map and match the standard ArcGIS swatches.
