---
name: plot-stations-metpy
description: "Draw a classic station-model weather map with MetPy's symbols for one time: at each station the temperature (upper left), dewpoint (lower left), coded sea-level pressure (upper right), a wind barb in knots (a ring when calm), cloud cover in the station circle and the present-weather symbol, over country borders. Works with metar-fetch output (all elements) and with any station Zarr (GHCN, TAHMO: the elements it has). Use for a surface-observation chart, e.g. 'show the airport observations over Kenya at noon'."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_stations_metpy.py *)
metadata:
  catalog-group: figure
---

# plot-stations-metpy

Plots one time of a station Zarr as a station-model map: the standard layout and symbols of `metpy.plots` (`sky_cover`, `current_weather`, MetPy's weather-symbol font).

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_stations_metpy.py -i STATIONS.zarr [--time YYYY-MM-DDTHH:MM] \
  [--bbox N/W/S/E] [--min-spacing KM] [--labels] [--var ROLE=NAME ...] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i` — station Zarr with a `station_id` (or `point_id`) dim, `latitude`/`longitude` along it, and usually `time`: `metar-fetch`, `ghcn-daily-fetch`, `tahmo-fetch`, `point-value` output.
- `--time` — the time to plot (nearest). Default: the latest time any station reported.
- `--bbox` — map extent `N/W/S/E`; stations outside are left out. Default: the stations plus a margin.
- `--min-spacing` — drop stations closer than this many km to a kept one, keeping those with the most data (`metpy.calc.reduce_point_density`). Default 0 (all). 25–50 km keeps a country map readable (Nairobi's JKIA and Wilson are 10 km apart).
- `--labels` — write each station's id under it.
- `--var ROLE=NAME` — use variable `NAME` for a role when the automatic match fails: `temperature`, `dewpoint`, `pressure`, `u`, `v`, `speed`, `direction`, `cloud_cover`, `weather_code`.
- `--title` — figure title. Default: the time.
- `--output`, `-o` — output PNG.

### What is drawn
| Position | Element | Found as |
|---|---|---|
| upper left (red) | temperature, °C | `air_temperature` standard name, or `t2m`, `tavg`, `tmax`, … |
| lower left (green) | dewpoint, °C | `dew_point_temperature`, `d2m`, … |
| upper right | pressure: last three digits of tenths of hPa (`123` = 1012.3) | `qnh`, `msl`, sea-level pressure standard names |
| barb | wind in knots (half feather 5, feather 10, pennant 50 kt); a ring around the station when calm | `u`/`v`, or `wind_speed` + `wind_from_direction` |
| circle | cloud cover in oktas | `cloud_cover` (from `metar-fetch`) |
| left | present weather symbol (rain, showers, thunderstorm, fog, …) | `present_weather_code` (from `metar-fetch`) |

Elements the data lacks are left out (GHCN `tmax` gives just a number next to a dot). Units are converted for display from each variable's `units` attr. The footnote lists what is shown.

### Notes
- A dataset with other dims (ensemble `number`, …) must be reduced first (`select`, `summarize-dim`).
- Borders come from the Natural Earth countries bundled with weather-skills-core: coarse but dependency-free.
- The text and symbols are drawn as plain Matplotlib annotations in MetPy's layout and font: MetPy 1.7's `StationPlot` text does not draw on Matplotlib 3.11.

### Errors
- No station dim, or extra dims → exit 2 (reduce them first).
- No recognised variables, or no station with data at that time inside the map → exit 2.
- A variable without units, or units that don't convert → exit 2.

### Examples
```bash
# Kenyan airports at noon on 16 April 2026, thinned to 25 km, with ids
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_stations_metpy.py -i kenya_metar.zarr \
  --time 2026-04-16T12:00 --min-spacing 25 --labels -o kenya_stations_12z.png

# Latest TAHMO observations over Kenya (temperature and wind only)
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_stations_metpy.py -i tahmo_kenya.zarr --min-spacing 40 -o tahmo_map.png
```
