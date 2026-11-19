---
name: landscan-population-fetch
description: "Read LandScan Global ambient population (ORNL, 30 arc-second, ~1 km) for a box, optionally masked to a boundary such as Kenya and summed to a coarser grid, as a weather-skills gridded Zarr: people per cell, people per km² and log10 of that density. Use when a map should show where people live, e.g. overlaying population or density contours on a rainfall, flood or heat map, or counting people in a region. Works anywhere with no setup: Kenya comes from a bundled cut-out, other areas from the hosted global file (only the tiles the box touches)."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads LandScan GeoTIFFs with rasterio. Boxes inside 6°N–6°S, 33–43.5°E (Kenya) read a bundled file offline; other boxes read the hosted global file over HTTPS from storage.googleapis.com (public, no credentials).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  openclaw:
    envVars:
      - name: LANDSCAN_FILE
        description: Optional. A local LandScan Global release (landscan-global-YYYY-assets.zip or the .tif inside it), used instead of the bundled and hosted 2026 files when --source-file is not given.
---

# landscan-population-fetch

Reads the LandScan Global population raster for a box and writes it as a gridded Zarr that `plot` and the other weather skills read like any other field.

Where the data comes from, first match wins:
1. `--source-file`, then `$LANDSCAN_FILE`: a local release (another year, or offline work outside Kenya).
2. The bundled LandScan 2026 cut-out, when `--bbox` lies inside 6°N–6°S, 33–43.5°E (Kenya and its borders). Offline.
3. The hosted LandScan 2026 global file (a Cloud-Optimized GeoTIFF in the public `africa-forecasting-data` bucket), read over HTTPS: only the tiles the box touches are fetched. No credentials.

stderr names the source used (`bundled Kenya cut-out`, `hosted`, or the local file).

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox N/W/S/E [--resolution DEG] [--mask-geojson PATH] [--source-file ZIP_OR_TIF] -o OUT.zarr
```

### Arguments
- `--bbox` — `N/W/S/E`, required. Kenya: `5.4141/33.9098/-4.7053/41.906` (`resolve-chc-region Kenya`).
- `--resolution` — output cell size in degrees, a whole multiple of 30″ (1/120°): `0.05` (CHIRPS), `0.1`, `0.25`, `0.5`, `1.5`. People are summed into each cell. Cell edges sit on multiples of the resolution from 180°W / 90°N, so the box widens outward to whole cells. Default: native 1/120° (Kenya is ~1160 × 960 cells).
- `--mask-geojson` — boundary polygon; cells whose centres fall outside become NaN, at native resolution before `--resolution` sums, so a coarse border cell holds only the people inside. Kenya: `resolve-chc-region Kenya --geojson kenya.geojson`.
- `--source-file` — a LandScan release: the downloaded `landscan-global-YYYY-assets.zip`, the `landscan-global-YYYY.tif` in it (keep the file name: the year is read from it), or an `https://` URL of such a `.tif`. The `-colorized.tif` is a picture, not counts, and is ignored. Default: `$LANDSCAN_FILE`, else the bundled or hosted 2026 file (above).
- `--output`, `-o` — output Zarr.

### Output
Gridded `(time, latitude, longitude)`, latitude south to north, one `time` = 1 January of the release year:
- `population` — people per cell (`1`), float32. LandScan is *ambient* population (where people are over a 24-hour day), not night-time residence.
- `population_density` — people per km² of land in the cell (`km-2`). Sea cells hold no data, so a coastal cell is divided by its land area only.
- `population_density_log10` — log10 of the density (`1`): `1` = 10, `2` = 100, `3` = 1000 people/km². NaN where nobody lives. Use it for colour scales and contours, since density spans 0 to >10⁴.
- NaN over the sea and outside `--mask-geojson`; `0` where LandScan has land but no people.
- Attrs carry the licence (CC BY 4.0), citation and DOI. LandScan must be attributed: put "Population: LandScan Global 2026, ORNL (CC BY 4.0)" on any figure or product that is shared.
- stderr gives the grid size, cell size and total population.

### Overlaying on a plot
Pass the Zarr to `plot` as another input, e.g. `-i rain.zarr=OBS -i pop.zarr=POP`, and add a trace. Density contours over a rainfall map (100 and 1000 people/km²):

```jsonnet
{ type: 'contour', x: '@POP.lon', y: '@POP.lat', z: '@POP.population_density_log10.isel(time=0)',
  contours: { coloring: 'none', start: 2, end: 3, size: 1 },
  line: { color: 'black', width: 1 }, showscale: false, hoverinfo: 'skip',
  name: '100 / 1000 people per km²', showlegend: true },
```

Population as a map of its own: a `heatmap` of `population_density_log10` on its own `coloraxis` with `cmin: 0, cmax: 4` and colorbar `tickvals: [0, 1, 2, 3, 4]`, `ticktext: ['1', '10', '100', '1k', '10k']`. Use `--resolution` near the grid you overlay on: the native 1 km grid is ~1 million cells over Kenya, slow to draw and too fine to read under a 0.05° field. To count people per grid cell of another field, fetch at that field's resolution and box.

### Notes
- Reads one window of the raster. Kenya from the bundled file takes a few seconds. From the hosted file, a read downloads the compressed 512 × 512 tiles (~4.3°) the box touches: ~0.8 MB around Nairobi, ~1.8 MB for all of Kenya, ~21 MB for Africa. A local zip is decompressed on read (~15 s for Kenya).
- Only the 2026 release is bundled and hosted. For another year, download it (free account) from https://landscan.ornl.gov/ and pass `--source-file`.
- Data provenance, licence and how the bundled and hosted files were built: `data/README.md`.

### Errors
- A missing `--source-file`, or a `.tif` / URL not named `landscan-global-YYYY.tif` — exit 2.
- `--resolution` not a whole multiple of 1/120°, a `--bbox` reaching outside the raster, or a `--mask-geojson` that leaves no cells — exit 2.
- A zip without the count raster, an unreadable raster, or a box with only sea — exit 1.
- Hosted file: `... is not available` (moved or not published; pass `--source-file`) or `network error reading ...` (retry; GDAL already retries 3 times) — exit 1.

### Example
With `kenya.geojson` from `resolve-chc-region Kenya --geojson kenya.geojson`:
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox 5.4141/33.9098/-4.7053/41.906 --mask-geojson kenya.geojson --resolution 0.05 -o kenya_pop_005.zarr
```
Writes Kenya's 2026 population on a 0.05° grid (204 × 161 cells, ~58.5 million people).
