---
name: grid-stations-metpy
description: "Turn station observations (TAHMO, GHCN-Daily, OpenAQ, any station Zarr) into a regular lat/lon grid with MetPy's objective analysis: Barnes, Cressman, natural neighbour, linear, nearest, cubic or radial basis functions, one time step at a time, with distances in km. The grid comes from --bbox/--resolution or is copied from another dataset (--like) so stations and a forecast or satellite field can be compared cell by cell. Use for 'make a map from the station data' or to grid observations for verification."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/grid_stations_metpy.py *)
metadata:
  catalog-group: transforms
---

# grid-stations-metpy

Interpolates every station variable to grid points with `metpy.interpolate.interpolate_to_points`, separately for each time step (and member or other dim), using only the stations that report.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/grid_stations_metpy.py -i STATIONS.zarr \
  (--resolution DEG [--bbox N/W/S/E] | --like GRID.zarr) [--method barnes] \
  [--search-radius KM] [--min-neighbors 3] [--gamma 1.0] [--max-distance KM] [-v VAR ...] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — station Zarr: a `point_id` or `station_id` dim with `latitude`/`longitude` coordinates along it (the layout of `tahmo-fetch`, `ghcn-daily-fetch`, `point-value`).
- `--resolution` — grid spacing in degrees. Cells sit on multiples of it, so grids from different runs line up.
- `--bbox` — grid extent `N/W/S/E`. Default: the stations' extent plus one cell.
- `--like` — copy the latitude/longitude grid of this gridded Zarr instead (replaces `--bbox`/`--resolution`). Use it to compare with a forecast or satellite field cell by cell.
- `--method` — `barnes` (default), `cressman`, `natural_neighbor`, `linear`, `nearest`, `cubic` or `rbf`.
- `--search-radius` — influence radius in km for `barnes`/`cressman`. Default: MetPy's (5 × the mean station spacing).
- `--min-neighbors` — stations needed within the radius for `barnes`/`cressman`; fewer leaves the cell NaN. Default: 3.
- `--gamma` — Barnes smoothing. Default: 1.0. Lower values fit the stations more closely but turn into blocks around each station (MetPy applies Barnes in one pass).
- `--max-distance` — blank cells farther than this (km) from every station reporting at that time.
- `--variable`, `-v` — grid only these variables (repeatable). Default: every variable along the station dim.
- `--output`, `-o` — output Zarr.

### Choosing a method
| Method | Fills | Character |
|---|---|---|
| `barnes` | everywhere within the radius | smooth weighted average; the classic objective analysis for temperature and pressure |
| `cressman` | within the radius | like Barnes with a sharper cut-off |
| `natural_neighbor` | inside the stations' hull only | smooth, honours stations exactly, leaves gaps outside: a good default for rainfall |
| `linear` / `cubic` | inside the hull | triangulation; exact at stations |
| `nearest` | everywhere | each cell takes its nearest station (blocks) |
| `rbf` | everywhere | smooth radial basis functions; can overshoot between sparse stations |

### Output
Gridded `(…, latitude, longitude)`: the station dim is replaced by the grid and every other dim (`time`, members) is kept. Variables keep their units and attrs; each `comment` names the method and station count. Dataset attrs gain `metpy_version`.

### Notes
- Distances use a local azimuthal-equidistant projection centred on the grid, so `--search-radius` and `--max-distance` are true km.
- A time step with fewer than 3 reporting stations (or fewer than `--min-neighbors` for Barnes/Cressman) is NaN.
- Gridded station data is only as good as the network. GHCN-Daily is sparse over East Africa (one of 17 Kenyan stations reported in mid-April 2026); TAHMO is far denser there. Use `--max-distance` so empty regions stay empty rather than being filled from distant stations.
- Rainfall varies over short distances: daily station rainfall gridded at 0.1° is an interpolation, not a measurement. Weekly or dekadal totals grid better.

### Errors
- Input without a `point_id`/`station_id` dim or station latitude/longitude → exit 2.
- Neither `--resolution` nor `--like`, or `--like` together with `--bbox`/`--resolution` → exit 2.
- A `--bbox` smaller than one `--resolution` step → exit 2.
- `-v` naming a variable not along the station dim → exit 2.

### Examples
```bash
# GHCN daily maximum temperature over the Netherlands on a 0.1° grid, Barnes analysis
uv run ${CLAUDE_SKILL_DIR}/scripts/grid_stations_metpy.py -i ghcn_nl.zarr \
  --bbox 53.6/3.2/50.7/7.3 --resolution 0.1 --max-distance 60 -o tmax_nl_grid.zarr

# TAHMO weekly rainfall on the CHIRPS grid, for a cell-by-cell comparison
uv run ${CLAUDE_SKILL_DIR}/scripts/grid_stations_metpy.py -i tahmo_kenya_weekly.zarr \
  --like chirps_kenya_weekly.zarr --method natural_neighbor -o tahmo_on_chirps.zarr
```
