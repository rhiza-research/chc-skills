---
name: planette-reforecast-fetch
description: "Fetch Planette's public ECMWF IFS 46-day reforecasts (10 members, 2006 onward, 1.5°) for the init nearest one calendar day in every year, as a model-climatology Zarr with dims (year, number, step, latitude, longitude). Precipitation, 2 m temperature (mean, max, min), 10 m wind, SST, soil moisture, radiation and more. Use when a task needs the ECMWF model's own climatology, e.g. as `--qq-model` for rank-downscale, or to compare a forecast with the model's usual range."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads Planette's public icechunk repo s3://planette-ifs-46-day/reforecasts (us-east-2) anonymously; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables:
    - precip
    - t2m
---

# planette-reforecast-fetch

Picks, for every reforecast year, the init closest to `--date`'s month and day, and writes those inits' ensembles side by side along a `year` dim.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD [-v VAR ...] [--bbox N/W/S/E] [--years YYYY-YYYY] [--max-gap-days N] [--lead-days N] [--keep-names] -o OUT.zarr
```

### Arguments
- `--date` — the forecast init to build a climatology for. Only its month and day are used.
- `--variable`, `-v` — Planette short name; repeat for several. Default: `pr`. 1.5° grid: `pr`, `conv_pr`, `t2m`, `t2m_max`, `t2m_min`, `t2d`, `ts`, `sst`, `u10m`, `v10m`, `ws10m`, `slp`, `ps`, `smv20`, `smv100`, `dswrf`, `olr`, `cape`, `cdd`, `hdd`, `sfwe`, `snw_dens`, `sdwe`, `siconc`. 1° ocean grid: `sit`, `ssh`, `sss` (fetch these on their own).
- `--bbox` — `N/W/S/E`. Default: global.
- `--years` — reforecast years to try, `YYYY-YYYY` or `YYYY`. Default: `2006-2025`.
- `--max-gap-days` — largest allowed distance between `--date`'s month and day and a year's init. Default: 4. Inits come every other day, so the nearest one is at most 1 day away where the year has data.
- `--lead-days` — keep leads 0 to N−1 days, 1–46. Default: 46.
- `--keep-names` — keep Planette's short names. Default: rename to dynamical.org's 46-day names (below).
- `--output`, `-o` — output Zarr.

### Output
- Dims `(year, number, step, latitude, longitude)`. `number` is 1–10 (perturbed members; the store has no control). `step` is 0–45 days: daily means labelled at the start of the day, like `dynamical-fetch`'s 46-day ECMWF output. `init_time(year)` holds each year's init date.
- Variables are named as in `ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree`: `pr` → `precipitation_surface`, `t2m` → `average_temperature_2m`, `t2m_max` / `t2m_min` → `maximum_temperature_2m` / `minimum_temperature_2m`, `sst` → `sea_surface_temperature`, `ts` → `skin_temperature_surface`, `u10m` / `v10m` → `wind_u_10m` / `wind_v_10m`, `slp` → `pressure_reduced_to_mean_sea_level`, `ps` → `pressure_surface`, `smv20` / `smv100` → `soil_moisture_0_20cm` / `soil_moisture_0_100cm`, `dswrf` → `downward_short_wave_radiation_flux_surface`, `conv_pr` → `precipitation_convective_surface`, `sfwe` → `snowfall_water_equivalent_rate_surface`, `snw_dens` → `snow_density_surface`, `siconc` → `sea_ice_area_fraction`. The others keep their short names.
- Units: precipitation rates in `mm day-1`, temperatures in `degree_Celsius`, pressures in `hPa`; the rest as in the source. `data_interval` is `1 day`.
- Global attrs `weather_skills_source = planette:ifs-46-day-reforecasts`, `reforecast_target_date`, `reforecast_max_gap_days`.

### Notes
- Years with no init within `--max-gap-days` (e.g. late 2025, past the store's end) are skipped and listed on stderr. Fewer than one year exits 1.
- Pair it with `dynamical-fetch --dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree` for the same `--date` and `--bbox`: same grid, same step labels, same variable names. Aggregate both with the same `aggregate-temporal --time-dim step` call.
- Performance: every init is one chunk of the whole globe (about 50 MB per variable), so `--bbox` does not reduce the download. 19 years of one variable take 1–2 minutes.
- Read failures (network, S3) exit 1 with the cause; retry.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-10-22 -v pr -v t2m --bbox 5.5/33.5/-5/42.5 -o planette_kenya.zarr
```
Writes daily precipitation and 2 m temperature over Kenya from the 2006–2024 reforecasts started on 21 October: 19 years × 10 members × 46 days.
