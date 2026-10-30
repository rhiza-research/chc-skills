---
name: icon-eps-fetch
description: "Fetch the DWD ICON-EPS global ensemble forecast (40 members, out to 7.5 days) from DWD open data for the latest run, regridded from ICON's triangular grid onto a regular lat/lon grid, as a weather-skills Zarr: precipitation, 2 m temperature and dewpoint, humidity, 10 m winds and gusts, cloud cover, surface pressure. Use for a one-week ensemble forecast to set beside ECMWF, GEFS or AIFS. No credentials needed; only the last ~24 h of runs exist."
license: MIT
compatibility: Requires Python 3.12 and uv. Requires the eccodes library for cfgrib (bundled on Windows; `eccodeslib` is installed on macOS/Linux). Downloads global GRIB2 files over HTTPS from opendata.dwd.de; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables:
    - tp
    - t2m
    - d2m
    - r2
    - u10
    - v10
    - fg10
    - tcc
    - sp
---

# icon-eps-fetch

Downloads one ICON-EPS global run from https://opendata.dwd.de/weather/nwp/icon-eps/grib/, regrids every member from the triangular grid onto a regular lat/lon grid, and writes a weather-skills standard dataset Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--date YYYY-MM-DD] [--run 0|6|12|18] [--bbox N/W/S/E] [-v VAR ...] -o OUT.zarr
# Newest init date of the run (no download)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--date` — init date. Default: the newest init of `--run` on the server. DWD keeps only ~24 h of runs, so this is today's or yesterday's date.
- `--run` — init hour UTC: `0`, `6`, `12` or `18`. Default `0`. 00/12 runs reach 180 h, 06/18 runs 120 h.
- `--bbox` — `N/W/S/E` decimal degrees. Omit for the globe. DWD serves global files only, so a bbox saves memory, not download time.
- `--variable`, `-v` — one field per flag; repeat for several. Default `tp`. `tp`, `t2m`, `d2m`, `r2` (2 m relative humidity), `u10`, `v10`, `fg10` (DWD `vmax_10m`, maximum 10 m gust), `tcc` (total cloud cover), `sp`. DWD folder names (`tot_prec`, `t_2m`, `td_2m`, `relhum_2m`, `u_10m`, `v_10m`, `vmax_10m`, `clct`, `ps`) are accepted. An unknown name exits 2 and lists `Available (most used first):`.
- `--max-lead` — last lead to keep (pandas timedelta: `168h`, `7D`). Default `168h`, one week.
- `--step-interval` — keep the published leads that are multiples of this (pandas timedelta). Default `24h`. Use `6h` for a sub-daily cycle of instantaneous fields.
- `--target-resolution` — output grid spacing in degrees. Default `0.25` (the native grid is ~20 km).
- `--regrid` — `linear` (default; barycentric over the triangles between cell centres) or `nearest` (each output point takes the nearest cell's value).
- `--workers` — parallel downloads. Default `4`; lower it if DWD throttles.
- `--probe-latest [IDENT]` — print the newest init date of `--run` on stdout and exit; stderr names the run. `IDENT` is ignored.
- `--output`, `-o` — output Zarr, overwritten.

### Output
- Dims `(number, step, latitude, longitude)`: 40 members numbered 1–40 (no control), latitude ascending, longitude `[-180, 180)`. Scalar `time` is the init.
- `tp` is a rate in `mm day-1` over `[step, next step)`, labelled at the interval start (`step=0` is 0–24 h with the default interval). The last lead has no interval, so it is dropped, for every field when `tp` is in the request.
- `t2m`, `d2m` are `degree_Celsius`; `sp` is `hPa`; `r2`, `tcc` are `%`; winds `m s-1`.
- `data_interval` is stamped for even leads; uneven leads (6-hourly to 120 h, then 12-hourly) get `step_bounds`. `weather_skills_source = dwd-icon-eps:<HH>/<YYYYMMDDHH>`; `license = CC BY 4.0; source: Deutscher Wetterdienst`.

### Notes
- ICON-EPS is published only on ICON's triangular (icosahedral, R02B07) grid: 1,310,720 cells whose centres come from the run's `clat`/`clon` files. The skill regrids every field so the output works with every gridded skill. Linear interpolation smooths slightly and does not exactly conserve totals; `--regrid nearest` keeps native values. Output points outside the triangulation (the poles) take the nearest cell.
- Leads thin out with lead time: hourly to 48 h, 3-hourly to 72 h, 6-hourly to 120 h, then 12-hourly. Requested leads the server lacks are skipped and named on stderr.
- Each lead of each field is one global file, ~30–60 MB compressed. A week of `tp` at the default interval is 8 files (~400 MB); `--step-interval 6h` to 168 h is 25 files per field.
- Credit DWD when publishing figures or data (CC BY 4.0).

### Errors
- Exit 1 — the init is not on the server (older than ~24 h, or not yet published; omit `--date` or use `--probe-latest`); a run still uploading has too few leads for the request; a transport failure after retries (retry later or lower `--workers`).
- Exit 2 — unknown `--variable`, a malformed `--max-lead` / `--step-interval`, a non-positive `--target-resolution`, or a `--bbox` that holds no output points.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox 5.5/33.5/-5/42.5 -o icon_kenya.zarr
```
Writes daily ICON-EPS precip rates for the coming week over Kenya from the newest 00 UTC run, all 40 members at 0.25°.

## Details

**Weekly totals and maps.** The output is a standard forecast Zarr, so the usual chain applies: `aggregate-temporal --period "7 day"`, then `convert-to-totals`, then `summarize-dim --dim number --method mean` (or `exceedance-probability` on the totals with members kept), then `plot` or `plot-compare-forecasts`. For a KMSA-style Kenya map use `chc-plot-theme --theme kmsa` and the outline from `resolve-chc-region`.
