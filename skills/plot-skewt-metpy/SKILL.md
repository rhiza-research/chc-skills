---
name: plot-skewt-metpy
description: "Draw a Skew-T log-p diagram with MetPy for one place and time from a pressure-level forecast, reanalysis or balloon sounding: temperature and dewpoint (every ensemble member, mean bold), the surface parcel with CAPE and CIN shaded, the LCL, wind barbs, dry/moist adiabats and mixing lines, a hodograph, and a box with CAPE, CIN, LCL, lifted index and precipitable water. Use when someone wants to see the vertical profile or sounding at a location, e.g. 'show the forecast sounding for Nairobi tomorrow afternoon'."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py *)
metadata:
  catalog-group: figure
---

# plot-skewt-metpy

Picks one profile (one grid cell or station, one time) from a pressure-level Zarr and draws it on MetPy's Skew-T, with a hodograph when winds and heights are there.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i LEVELS.zarr [-i SURFACE.zarr] \
  (--lat LAT --lon LON | --station ID) [--isel DIM=INDEX ...] [--var ROLE=NAME ...] \
  [--no-hodograph] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i` — Zarr with temperature and a moisture field (dewpoint, `q` or `r`) on pressure levels. Repeat to add surface fields (`sp`, `t2m`, `d2m`) or winds (`u`, `v`) and heights (`gh` or `z`) from another store. Same inputs and matching as `sounding-indices-metpy`.
- `--lat`, `--lon` — the grid cell nearest this point. Required for gridded input.
- `--station` — the station id (`point_id` or `station_id`) to plot, for station profiles.
- `--isel DIM=INDEX` — pick one position along any other dim with several values, repeatable: `--isel step=2`, `--isel time=0`. Every dim except pressure and ensemble members must end up with one value.
- `--var ROLE=NAME` — use `NAME` for a role when matching fails or is ambiguous (roles as in `sounding-indices-metpy`).
- `--no-hodograph` — leave out the hodograph.
- `--title` — figure title. Default: the point and the init/valid time.
- `--output`, `-o` — output PNG.

### What is drawn
- Temperature (red) and dewpoint (green). With an ensemble dim (`number`), each member is a thin line and the ensemble mean is bold; the parcel, shading, LCL and index box use the mean profile.
- The surface parcel (dashed), CAPE shaded red and CIN blue, the LCL marked.
- Wind barbs in knots on the right (ensemble mean), from `u` and `v` on levels.
- Hodograph of the 0–10 km wind (knots), coloured by height above ground, when heights are present.
- Box: SBCAPE, SBCIN, LCL, lifted index and precipitable water of the plotted profile.

### Notes
- **Over high ground add `sp` (and `t2m`, `d2m`)**: levels below ground are then hidden and the parcel starts at the real surface (Nairobi ~830 hPa). Without them the lowest levels are drawn as if they were air.
- Wind barbs and the hodograph use only levels above ground; heights are taken above the lowest of them.
- For one member pass `--isel number=N`.
- The numbers in the box match `sounding-indices-metpy` for the same profile; use that skill for maps or ensemble statistics of the indices.
- A small "MetPy x.y" note sits bottom-left; core's provenance mark sits bottom-right.

### Errors
- Gridded input without `--lat`/`--lon` → exit 2.
- Another dim still has several values (e.g. `step`) → exit 2 with the `--isel` to add.
- No pressure dim, temperature or moisture on levels → exit 2.
- Fewer than 3 levels with temperature and dewpoint at the point → exit 2.

### Examples
```bash
# ERA5 sounding for Nairobi, 15 April 2026 12 UTC (time index 1), with surface fields
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --lat -1.29 --lon 36.82 --isel time=1 -o skewt_nairobi.png

# Ensemble forecast sounding at Lodwar, lead index 3, every member drawn
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i fc_levels.zarr -i fc_surface.zarr \
  --lat 3.12 --lon 35.6 --isel step=3 -o skewt_lodwar.png
```
