---
name: cross-section-metpy
description: "Cut a vertical cross-section through gridded fields along a line between two points with MetPy (metpy.interpolate.cross_section): every level, member and time along the path, the distance along it in km, optionally the wind along and across the section, and the terrain blanked with surface pressure. Use for vertical slices such as 'a cross-section of humidity from Mombasa to Lake Victoria', jets, fronts or monsoon flow; plot the result with plot's cross_section recipe."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/cross_section_metpy.py *)
metadata:
  catalog-group: transforms
---

# cross-section-metpy

Interpolates gridded fields to evenly spaced points on the great circle between two points, with MetPy, and writes the section as a Zarr along an `index` dim.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/cross_section_metpy.py -i GRID.zarr [-i MORE.zarr] \
  --start LAT,LON --end LAT,LON [--steps 100] [--interp linear|nearest] \
  [-v VAR ...] [--wind-components] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — gridded Zarr (latitude/longitude dims), usually on pressure levels. Repeat to add surface pressure (`sp`) or winds from another store.
- `--start`, `--end` — the ends of the section as `LAT,LON` (decimal degrees, south and west negative). Both must lie inside the grid.
- `--steps` — number of points along the section. Default: 100.
- `--interp` — `linear` (default) or `nearest` interpolation from the grid to the points.
- `--variable`, `-v` — cut only these variables (repeatable). Default: every variable on the grid.
- `--wind-components` — add `wind_tangential` (along the section, positive from start to end) and `wind_normal` (across it, positive to the left of travel) from `u` and `v` (`metpy.calc.cross_section_components`).
- `--output`, `-o` — output Zarr.

### Output
- Latitude and longitude dims become one `index` dim (0 … steps−1); `latitude`, `longitude` and `distance` (km from `--start`) are coordinates along it. Levels, members, times and other dims are kept.
- Variables keep their names, units and attrs; with `--wind-components` the `u`/`v` used for them are replaced by the two components unless named with `-v`.
- With `sp` on the grid and a pressure dim, levels below the ground along the path are NaN, so the terrain shows as a blank band. `sp` itself is dropped unless named with `-v`.
- Dataset attrs gain `metpy_version`, `cross_section_start` and `cross_section_end`.

### Plotting
Use the `plot` skill's `cross_section` recipe with `distance` (or `longitude` for a west-east section) on the x-axis and the pressure dim on a reversed y-axis.

### Notes
- The path is a great circle, so a long section is not a straight line on a lat/lon map.
- Points between grid cells are interpolated; on a 1.5° grid, choose `--steps` near the section length in grid cells to avoid implying detail that isn't there.
- A grid stored as 0–360 longitude takes negative `--start`/`--end` longitudes as well.
- Works on single-level fields too (a transect of rainfall, SST, …): the output then has only `index` and the input's other dims.

### Errors
- `--start` or `--end` outside the grid → exit 2 with the grid's extent.
- Input without lat/lon dims → exit 2.
- `--wind-components` without exactly one `u` and one `v` → exit 2.
- `-v` naming a variable that is not on the grid → exit 2.

### Examples
```bash
# ERA5 temperature, humidity and section-relative wind from Mombasa to Kisumu, terrain blanked
uv run ${CLAUDE_SKILL_DIR}/scripts/cross_section_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --start -4,39.7 --end 0.5,34 -v t -v q --wind-components -o xs_mombasa_kisumu.zarr

# West-east section of a forecast along the equator
uv run ${CLAUDE_SKILL_DIR}/scripts/cross_section_metpy.py -i fc_levels.zarr \
  --start 0,30 --end 0,50 --steps 40 -o xs_equator.zarr
```
