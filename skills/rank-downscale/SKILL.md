---
name: rank-downscale
description: "Downscale and bias-correct a forecast onto an observed product's finer grid by rank mapping: rank every forecast value among the model's own climatology (reforecasts) on the model grid, carry the ranks to the fine grid with optional smoothing, and give each fine cell the observed climatology value of that rank. Works for any variable and any window (weekly, dekadal, 14 day, ...) and any observed product (CHIRPS, IMERG, ERA5, ...). Use when a task needs a forecast in observed-product terms at the observation's resolution, e.g. ECMWF weekly precipitation on the CHIRPS 0.05° grid."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/rank_downscale.py *)
metadata:
  catalog-group: transforms
---

# rank-downscale

Rank mapping onto an observed climatology, as in the CHC ECMWF-onto-CHIRPS downscaling: the forecast's position in the model climatology becomes the same position in the observed climatology.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/rank_downscale.py -i FORECAST.zarr -o OUT.zarr --qq-model MODEL_CLIM.zarr --qq-reference OBS_CLIM.zarr \
  (--reference-grid OBS_CLIM.zarr | --factor N | --target-resolution DEG) [-v NAME] [--closing-size N] [--rank-sigma CELLS] [--field-sigma CELLS] [--keep-partial]
```

### Arguments
- `--input`, `-i` — forecast Zarr with lat/lon, usually `(number, step, latitude, longitude)` after `aggregate-temporal --time-dim step`.
- `--output`, `-o` — output Zarr.
- `--qq-model` — required. The model's own climatology sample for the same variable and windows, on (or interpolated to) the input grid, e.g. `planette-reforecast-fetch` output aggregated like the input: `(year, number, step, latitude, longitude)`.
- `--qq-reference` — required. The observed climatology sample covering every output grid point, e.g. `lead-climatology` output aggregated like the input: `(year, step, latitude, longitude)`.
- `--reference-grid` — Zarr whose lat/lon become the output grid, kept to the input's extent plus half an input cell. Usually the same file as `--qq-reference`.
- `--factor`, `-f` — integer ≥ 1: split each input cell into `factor` steps.
- `--target-resolution` — output spacing in degrees.
- `--variable`, `-v` — process only this variable. Default: all.
- `--closing-size` — grey-closing window, in output cells, on the ranks: fills small low-rank holes from their surroundings. Default: 0 (off).
- `--rank-sigma` — gaussian sigma, in output cells, smoothing the ranks across input-cell edges. Default: 0 (off).
- `--field-sigma` — gaussian sigma, in output cells, smoothing the final field. Default: 0 (off).
- `--keep-partial` — keep forecast windows that `aggregate-temporal` stamped as incomplete (`aggregation_coverage` < 1, e.g. the 4-day 7th week of a 46-day forecast). Default: drop them, listed on stderr.

Pass exactly one of `--reference-grid`, `--factor`, `--target-resolution`.

### Output
- The input on the output grid; `number`, `step`, the init `time` and attrs are kept. Without `--field-sigma` every value is one of the observed sample's values.
- Output cells where the reference is NaN in every sample (sea for CHIRPS, outside its domain) are NaN.

### Notes
- The forecast and both samples must use the same variable name and units: rename with `edit-coords --rename` (e.g. ERA5 `t2m` → `average_temperature_2m`) and convert with `unit-convert` first. A mismatch exits 2 and says which.
- `step` is matched value by value: every forecast step must be in both samples, else exit 2. Aggregate all three with the same `aggregate-temporal --period ... --time-dim step` call: weekly, `dekadal`, `14 day`, `30 day` all work.
- The model sample pools years and members per cell (e.g. 19 years × 10 members = 190 values); the reference pools its years. The two may differ in size.
- No extrapolation: a forecast above (or below) every model value gets the wettest (or driest, warmest, coldest) observed year. With a warming trend, a 2026 temperature forecast is often above every 2006–2024 reforecast in week 1, so its anomaly is capped.
- A target coarser than the input exits 2.
- For plain interpolation, or quantile mapping with interpolated values, use forecasting-skills' `downscale`.

### Example
Weekly ECMWF precipitation over Kenya, downscaled onto CHIRPS (production smoothing: `--rank-sigma 1.5 --field-sigma 0.4`):

```bash
BBOX=5.5/33.5/-5/42.5
# forecast and its model climatology (forecasting-skills dynamical-fetch; this repo's planette-reforecast-fetch)
uv run dynamical-fetch/scripts/fetch.py --dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree --date 2026-10-22 -v precipitation_surface --bbox $BBOX -o fc.zarr
uv run planette-reforecast-fetch/scripts/fetch.py --date 2026-10-22 -v pr --bbox $BBOX -o rf.zarr
# observed climatology: each year's window (here CHIRPS via dynamical-fetch, one call per year), laid on the forecast's leads
for y in $(seq 2006 2024); do
  uv run dynamical-fetch/scripts/fetch.py --dataset ucsb-chc-chirps-analysis-final --start-time $y-10-22 --end-time $y-12-06 --bbox $BBOX -v precip -o chirps_$y.zarr
done
uv run lead-climatology/scripts/lead_climatology.py $(for y in $(seq 2006 2024); do echo -i chirps_$y.zarr; done) --like fc.zarr --years 2006-2024 -o chirps_leads.zarr
# same weekly windows for all three
for f in fc rf chirps_leads; do uv run aggregate-temporal/scripts/aggregate.py -i $f.zarr -o ${f}_weekly.zarr --period weekly --time-dim step; done
uv run ${CLAUDE_SKILL_DIR}/scripts/rank_downscale.py -i fc_weekly.zarr -o fc_chirps.zarr --qq-model rf_weekly.zarr \
  --qq-reference chirps_leads_weekly.zarr --reference-grid chirps_leads_weekly.zarr --rank-sigma 1.5 --field-sigma 0.4
```
Writes all 101 members' weekly precipitation rates for weeks 1–6 on the CHIRPS 0.05° grid; the 4-day 7th week is dropped. For 2 m temperature onto ERA5, fetch the observations with `era5-timeseries-fetch --years 2006-2024 -v t2m`, run `aggregate-temporal --period daily`, then `lead-climatology` and `edit-coords --rename t2m=average_temperature_2m`.

## Details

**Rank mapping.** Each input value gets its mid-rank quantile `q` in its own input cell's `--qq-model` sample: years and members pooled, `step` matched. A value tied with many model values (dry days) takes the middle of the tie block. Every output cell takes the `q` of its nearest input cell; `--closing-size` and `--rank-sigma` then smooth `q` across input-cell edges, with NaN cells taking no part. The output value is the sorted `--qq-reference` value at rank `round(q·(n−1))`, where `n` is the number of reference values in that output cell. `--field-sigma` finally smooths the field, ignoring NaN.

**Why rank on the model grid.** The model sample is never interpolated before ranking, so every rank compares a forecast with the model's own values in the same cell. Only the ranks, which are smooth and bounded in [0, 1], are carried to the fine grid. The fine-scale pattern comes from the observed climatology.
