---
name: sounding-fetch
description: "Fetch observed radiosonde (weather balloon) soundings from NOAA's global IGRA2 archive, read with Siphon, for stations by WMO number or all stations in a bbox and a date range: temperature, dewpoint, wind and geopotential height on a common pressure grid, plus the launch-site pressure, temperature and dewpoint. About two days behind real time; records back to the 1940s. No credentials. The output feeds sounding-indices-metpy and plot-skewt-metpy directly, e.g. 'show the observed Nairobi sounding' or 'compare observed CAPE with the forecast'."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads NOAA NCEI's IGRA2 archive (no credentials).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py *)
metadata:
  catalog-group: fetchers
---

# sounding-fetch

Downloads every launch of the chosen stations in the date range from the [Integrated Global Radiosonde Archive (IGRA2)](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive) with Siphon (`IGRAUpperAir`), puts each launch on a common pressure grid with MetPy (`log_interpolate_1d`), and writes a station Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time YYYY-MM-DD --end-time YYYY-MM-DD \
  (--station WMO_OR_IGRA2_ID ... | --bbox N/W/S/E) [--levels 10|mandatory|P1,P2,...] -o OUT.zarr
```

### Arguments
- `--start-time`, `--end-time` — inclusive UTC date range.
- `--station` — 5-digit WMO number (`63741` Nairobi/Dagoretti, `06260` De Bilt) or 11-character IGRA2 id (`KEM00063741`); repeatable.
- `--bbox` — every IGRA2 station inside `N/W/S/E` whose record overlaps the dates. With `--station` too, both sets are fetched.
- `--levels` — the pressure grid: a step in hPa from 1050 to 100 (default `10`), `mandatory` (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100), or a comma list.
- `--output`, `-o` — output Zarr.

### Output
Dims `(station_id, time, level)`, CF `featureType=timeSeriesProfile`; `time` is the nominal launch time (UTC); `latitude`, `longitude`, `elevation`, `name` along `station_id`; `level` in hPa. Dataset attrs include `siphon_version` and `metpy_version`.

| Variable | Dims | Units |
|---|---|---|
| `t`, `dewpoint` | station, time, level | degree_Celsius |
| `u`, `v` | station, time, level | m s-1 |
| `gh` | station, time, level | m (reported at standard levels, interpolated between) |
| `sp` | station, time | hPa, at the launch site |
| `t2m`, `d2m` | station, time | degree_Celsius, at the launch site |

The names match what `sounding-indices-metpy`, `plot-skewt-metpy` and `vertical-interpolate-metpy` look for: they take this Zarr as it is, drop the levels below the launch site and start the parcel at the surface.

### Notes
- **Coverage changes over time.** IGRA2's station list gives each station's years; a station whose record ended before the dates is skipped with a note. In Kenya only Nairobi/Dagoretti (63741) reports in recent years, roughly daily at 00 UTC, and its IGRA2 record stops in October 2025. Lodwar ended in 2020, most others in the 1980s–1990s. Neighbouring countries and Europe/US are far denser (De Bilt launches daily through today).
- **Lag**: IGRA2 is about two days behind real time.
- **The grid**: each launch's reported levels (standard plus significant levels) are interpolated in log-pressure, variable by variable, with no extrapolation. Below the launch site and above the balloon's burst level are NaN. The default 10 hPa grid keeps the structure of a typical launch (40–100 reported levels); `mandatory` gives the exact standard levels only.
- **Comparing with IGRA2's own indices**: IGRA2 publishes derived values too. Its LCL and lifted index match `sounding-indices-metpy` closely, but its precipitable water covers only surface to 500 hPa (ours the whole column, ~4 mm more at Nairobi), and its CAPE uses a different method (often ~1.5–2× MetPy's). Compare like with like.
- Downloads are one station at a time (each reads that station's archive file, ~5–15 s).

### Errors
- Neither `--station` nor `--bbox` → exit 2.
- A station not in the IGRA2 list → exit 2.
- No launches in the window (stopped or irregular stations) → exit 1; widen the window or pick another station.
- IGRA2 unreachable after 3 tries → exit 1; retry later.

### Examples
```bash
# Nairobi soundings during the 2025 long rains, then their instability indices and one Skew-T
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time 2025-04-10 --end-time 2025-04-16 \
  --station 63741 -o nairobi_soundings.zarr

# Every station with launches in East Africa for one week of 2025
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time 2025-06-01 --end-time 2025-06-07 \
  --bbox 15/22/-12/52 --levels mandatory -o east_africa_soundings.zarr
```
