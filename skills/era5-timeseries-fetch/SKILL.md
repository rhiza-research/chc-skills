---
name: era5-timeseries-fetch
description: "Fetch ERA5 hourly single-level fields (2 m temperature, precipitation, dewpoint, wind, pressure, radiation, ...) over a small area or one point for any set of time windows from 1940 on: one range, several windows, the same window in every year of a range, or a list of analog years. Reads ECMWF's geo-chunked ARCO store, so decades over a small area take minutes. Needs an ECMWF Data Store API key. Use for climatologies (e.g. rank-downscale `--qq-reference` via lead-climatology), analog years, station-style time series, and trend checks. For large areas over short periods, use forecasting-skills' arco-era5-fetch."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads ECMWF's ARCO service (arco.datastores.ecmwf.int, beta, fair-use rate limits) with an ECMWF Data Store API key.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables:
    - t2m
    - tp
  openclaw:
    requires:
      env:
        - ECMWF_DATASTORES_KEY
    primaryEnv: ECMWF_DATASTORES_KEY
---

# era5-timeseries-fetch

Reads hourly ERA5 for the hours of every requested window over a bbox or one grid cell, and writes them on one `time` axis.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py -v VAR [-v VAR ...] (--bbox N/W/S/E | --point LAT,LON) \
  (--start-time YYYY-MM-DD --end-time YYYY-MM-DD | --window YYYY-MM-DD/YYYY-MM-DD ...) [--years LIST] [--hours LIST] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--variable`, `-v` — ERA5 short name; repeat for several. Required. `t2m`, `d2m`, `skt`, `sst`, `tp`, `u10`, `v10`, `u100`, `v100`, `fg10`, `msl`, `sp`, `tcc`, `blh`, `cbh`, `ssrd`, `strd`, `fdir`, `slhf`, `sshf`.
- `--bbox` — `N/W/S/E`. Pass this or `--point`.
- `--point` — `LAT,LON`: the nearest 0.25° cell, kept as a 1×1 grid.
- `--start-time`, `--end-time` — one inclusive date range.
- `--window` — an inclusive date range `START/END`; repeat for several. Combines with `--start-time`/`--end-time`.
- `--years` — repeat every window in each of these years, keeping its month, day and length: ranges and lists, e.g. `2006-2024` or `1997,2015,2023`. The window's own year is ignored; a window crossing the new year (`1997-12-15/1998-01-15`) keeps crossing it. A Feb 29 start becomes Feb 28 outside leap years.
- `--hours` — keep only these UTC hours, e.g. `0,6,12,18`. Default: all 24.
- `--probe-latest` — print the store's last day (`YYYY-MM-DD`) and exit.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(time, latitude, longitude)`, hourly, latitude ascending, longitude in `[-180, 180)`. Times from all windows are sorted and joined on one axis, gaps included; overlapping windows are read once.
- `t2m`, `d2m`, `skt`, `sst` in `degree_Celsius`; `msl`, `sp` in `hPa`; `tp` as a `mm day-1` rate; the rest in ERA5 units (`m s-1`, `J m-2` per hour, `m`, `1`).
- Hour-accumulated and hour-maximum fields (`tp`, `ssrd`, `strd`, `fdir`, `slhf`, `sshf`, `fg10`) are labelled at the start of their hour, so a day's 00–23 UTC rows cover that day. Instantaneous fields keep their hour.
- `data_interval` `1 hour`. Global attrs `weather_skills_source = ecmwf-arco-era5-geo`, `era5_windows` (every window read).

### Notes
- Credentials: `ECMWF_DATASTORES_KEY`, else `CDSAPI_KEY`, else the `key:` line of `~/.cdsapirc`. No key exits 2. A rejected key (HTTP 401/403, also when the ERA5 licence is not accepted on the CDS website) exits 1; the key is never printed.
- Hours outside the store (before 1940, or after `--probe-latest`, about a week behind real time) are dropped with a warning; none left exits 1.
- HTTP 429 means ECMWF's fair-use limit; wait and retry. Other transient read errors are retried twice.
- Performance: the store is chunked as ~7.7 years × 4 × 4 cells, so cost grows with the area and the number of ~7.7-year blocks touched, barely with the number of windows. 19 Oct–Dec windows of two variables over Kenya (43 × 37 cells) take about 2.5 minutes; one grid cell over decades takes seconds. A continental bbox reads many chunks for every block; use arco-era5-fetch for large areas over short periods.
- Daily or weekly values: run `aggregate-temporal --period daily`; for a forecast's lead structure, follow with `lead-climatology`.

### Examples
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --start-time 2006-10-22 --end-time 2006-12-06 --years 2006-2024 \
  --bbox 5.5/33.5/-5/42.5 -v t2m -v tp -o era5_kenya_oct_dec.zarr
```
Writes hourly 2 m temperature and precipitation over Kenya for 22 October – 6 December of every year 2006–2024.

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --window 1997-12-15/1998-01-15 --years 1997,2015,2023 \
  --hours 0,12 --point -1.29,36.82 -v t2m -o nairobi_el_nino_years.zarr
```
Writes 00 and 12 UTC 2 m temperature at Nairobi for mid-December to mid-January in three El Niño years.
