---
name: cropland-fetch
description: "Read the Global Synergy Cropland Map (Lu et al. 2020; cropland around 2010, 15 arc-second, ~500 m) for a box, optionally masked to a boundary such as Kenya and aggregated to a coarser grid, as a weather-skills gridded Zarr: the fraction of land under cropland and the cropland area (km²) per cell. Use when a map should show where crops are grown, e.g. overlaying cropland contours or shading on a rainfall, drought or onset map, or totalling cropland in a region. Works anywhere with no setup: Kenya comes from a bundled cut-out, other areas from the hosted global file (only the tiles the box touches)."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads GeoTIFFs with rasterio. Boxes inside 6°N–6°S, 33–43.5°E (Kenya) read a bundled file offline; other boxes read the hosted global file over HTTPS from storage.googleapis.com (public, no credentials).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  openclaw:
    envVars:
      - name: CROPLAND_FILE
        description: Optional. A local copy of Global-cropland-percentage-map.tif, used instead of the bundled and hosted files when --source-file is not given.
---

# cropland-fetch

Reads the Global Synergy Cropland Map for a box and writes it as a gridded Zarr that `plot` and the other weather skills read like any other field.

The map fuses several satellite cropland maps with national and subnational crop-area statistics (the SASAM method) into one estimate of cropland around 2010, the share of each ~500 m cell that is cropland. It is a static map, not a seasonal or yearly product: use it for where cropping happens, not what was planted in a given season.

Where the data comes from, first match wins:
1. `--source-file`, then `$CROPLAND_FILE`: a local copy of `Global-cropland-percentage-map.tif` (offline work outside Kenya).
2. The bundled cut-out, when `--bbox` lies inside 6°N–6°S, 33–43.5°E (Kenya and its borders). Offline.
3. The hosted global file (a Cloud-Optimized GeoTIFF in the public `africa-forecasting-data` bucket), read over HTTPS: only the tiles the box touches are fetched. No credentials.

stderr names the source used (`bundled Kenya cut-out`, `hosted`, or the local file).

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py (--bbox N/W/S/E [--resolution DEG] | --reference-grid GRID.zarr [--bbox N/W/S/E]) [--mask-geojson PATH] [--area-geojson PATH [--min-fraction F]] [--source-file TIF_OR_URL] -o OUT.zarr
```

### Arguments
- `--bbox` — `N/W/S/E`. Required unless `--reference-grid` is given; with it, keeps the reference cells whose centres lie inside. Kenya: `5.4141/33.9098/-4.7053/41.906` (`resolve-chc-region Kenya`). The map covers 56°S–71°N, 173°W–179°E.
- `--resolution` — output cell size in degrees, a whole multiple of 15″ (1/240°): `0.05` (CHIRPS), `0.1`, `0.25`, `0.5`, `1.5`. The fraction is averaged over the land in each cell and the area summed. Cell edges sit on multiples of the resolution from 180°W / 90°N, so the box widens outward to whole cells. Default: native 1/240° (Kenya is ~2450 × 1930 cells).
- `--reference-grid` — a gridded Zarr (e.g. the precipitation you will clip or compare); the output takes its exact lat/lon cells, whatever their spacing or offset (ERA5's 0.25° cells centred on whole multiples, a model's 1.5°). Each ~500 m map cell goes to the reference cell holding its centre. Not with `--resolution`.
- `--mask-geojson` — boundary polygon; cells whose centres fall outside become NaN, at native resolution before the output grid aggregates, so a coarse border cell counts only the cropland inside. Kenya: `resolve-chc-region Kenya --geojson kenya.geojson`.
- `--area-geojson` — also write the output cells with `cropland_fraction >= --min-fraction` as one polygon GeoJSON (a `FeatureCollection`), for `clip-region --geojson`. See below.
- `--min-fraction` — the cropland fraction a cell needs to be in `--area-geojson`, `0`–`1`. Default `0.3` (at least 30% of the cell's land is cropland).
- `--source-file` — a copy of `Global-cropland-percentage-map.tif` (from Harvard Dataverse, doi:10.7910/DVN/ZWSFAA), a path or an `https://` URL. Default: `$CROPLAND_FILE`, else the bundled or hosted file (above).
- `--output`, `-o` — output Zarr.

### Output
Gridded `(time, latitude, longitude)`, latitude south to north, one `time` = 2010-01-01 (the map's reference year):
- `cropland_fraction` — share of the cell's land under cropland, `0`–`1` (units `1`). The source file is named a "percentage map" but stores fractions: `0.5` is 50%. Over coarse cells it is the land-area-weighted mean, so a coastal cell is not diluted by sea.
- `cropland_area` — km² of cropland in the cell (`km2`), summed. Use it to total cropland in a region (sum over the cells) or per grid cell of another field.
- NaN over the sea and outside `--mask-geojson`; `0` on land with no cropland (inland water such as Lake Victoria is `0`, not NaN).
- Attrs carry the licence (CC0), citation and DOIs. The data is public domain; citing the paper on shared figures is good practice: "Cropland: Global Synergy Cropland Map 2010 (Lu et al. 2020)".
- stderr gives the grid size, cell size and total cropland area.

### Clipping a field to agricultural areas
To keep precipitation (or any gridded field) only where crops are grown, make the agricultural area a polygon on that field's own grid, then clip with `clip-region`:

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --reference-grid rain.zarr --min-fraction 0.3 --area-geojson cropland_30.geojson -o cropland_on_rain.zarr
# then: clip-region -i rain.zarr --geojson cropland_30.geojson -o rain_cropland.zarr
```

- The polygon runs along the reference grid's cell edges, shrunk by 1% of a cell, so `clip-region` keeps exactly the rain cells that are at least 30% cropland and none of their neighbours. Without `--reference-grid` the polygon follows this skill's own grid, and `clip-region` would also keep every rain cell it partly overlaps.
- Add `--mask-geojson` (e.g. Kenya, or one county) to limit the area to a region.
- The threshold is the user's choice: `0.3` keeps the main farming areas, `0.1` most land with any cropping, `0.5` and up only the most intensively cropped cells. Say which was used.
- `--keep-outside` on `clip-region` keeps the full grid with NaN outside, for a map that shows where the area is.
- To weight by cropland instead of cutting at a threshold (a cropland-weighted average rain), use `cropland_area` from `cropland_on_rain.zarr`, which sits on the same cells as the rain.
- `clip-region` keeps whole cells, so for cropland totals (km² in a county) use this skill's `--mask-geojson`, not `clip-region` on a coarse grid: a clipped coarse grid counts boundary cells in full.

### Overlaying on a plot
Pass the Zarr to `plot` as another input, e.g. `-i rain.zarr=OBS -i crop.zarr=CROP`, and add a trace. Outlines of mostly-cropped land (over 30% and 60% cropland) over a rainfall map:

```jsonnet
{ type: 'contour', x: '@CROP.lon', y: '@CROP.lat', z: '@CROP.cropland_fraction.isel(time=0)',
  contours: { coloring: 'none', start: 0.3, end: 0.6, size: 0.3 },
  line: { color: 'darkgreen', width: 1 }, showscale: false, hoverinfo: 'skip',
  name: 'cropland > 30% / 60%', showlegend: true },
```

Cropland as a map of its own: a `heatmap` of `cropland_fraction` on its own `coloraxis` with `cmin: 0, cmax: 1` and colorbar `tickvals: [0, 0.25, 0.5, 0.75, 1]`, `ticktext: ['0%', '25%', '50%', '75%', '100%']`. Use `--resolution` near the grid you overlay on: the native ~500 m grid is ~5 million cells over Kenya, slow to draw and too fine to read under a 0.05° field.

### Notes
- Reads one window of the raster. Kenya from the bundled file takes ~10–25 s at native resolution, most of it the `--mask-geojson` test on ~5 million cells. From the hosted file a read downloads the compressed 512 × 512 tiles (~2.1°) the box touches: ~3.7 MB for Kenya, ~94 MB for Africa.
- The companion `Global-cropland-confidence.tif` (agreement among the input maps) is not read.
- Data provenance, licence and how the bundled and hosted files were built: `data/README.md`.

### Errors
- A missing `--source-file` — exit 2.
- `--resolution` not a whole multiple of 1/240°, both `--resolution` and `--reference-grid`, neither `--bbox` nor `--reference-grid`, a box reaching outside the map (56°S–71°N, 173°W–179°E), a `--reference-grid` with fewer than 2 × 2 cells in `--bbox`, a `--min-fraction` outside (0, 1], or a `--mask-geojson` that leaves no cells — exit 2.
- `--area-geojson` with no cell reaching `--min-fraction` (the message gives the highest fraction) — exit 1.
- An unreadable raster, one not on a regular lat/lon grid, or a box with only sea — exit 1.
- Hosted file: `... is not available` (moved or not published; pass `--source-file`) or `network error reading ...` (retry; GDAL already retries 3 times) — exit 1.

### Example
With `kenya.geojson` from `resolve-chc-region Kenya --geojson kenya.geojson`:
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox 5.4141/33.9098/-4.7053/41.906 --mask-geojson kenya.geojson --resolution 0.05 -o kenya_cropland_005.zarr
```
Writes Kenya's cropland on a 0.05° grid (204 × 161 cells, ~71,400 km² of cropland).
