---
name: ecmwf-04deg-reforecast-fetch
description: "Fetch ECMWF 0.4 degree extended-range reforecasts (hindcasts: 20 past years x 11 members, 46 lead days, East Africa 25N-15S, 20-55E) of daily precipitation for a forecast date from the private Sheerwater store gs://sheerwater-datalake/ecmwf-0_4/, as a weather-skills ensemble forecast Zarr with one init_time per reforecast run. The model's own climatology for the 0.4 degree forecast of ecmwf-04deg-fetch: use it for m-climates, quantile-mapping downscaling (downscale --qq-model), bias and skill checks against observations, or ranking a forecast among past years. Needs Google Cloud credentials. Licensed ECMWF data: never publish the raw fields."
license: MIT
compatibility: "Requires Python 3.12 and uv. Reads private GCS gs://sheerwater-datalake/ecmwf-0_4 via gcsfs + zarr. Requires Google Cloud credentials: GCLOUD_SERVICE_CREDENTIALS or NEURAL_GCM_SERVICE_CREDENTIALS (the raw service-account key JSON), GOOGLE_APPLICATION_CREDENTIALS (a service-account JSON file path), Application Default Credentials from `gcloud auth application-default login`, or a `gcloud auth login` session with the gcloud CLI on PATH."
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables:
    - tp
  openclaw:
    requires:
      env:
        - GCLOUD_SERVICE_CREDENTIALS
    primaryEnv: GCLOUD_SERVICE_CREDENTIALS
    envVars:
      - name: GCLOUD_SERVICE_CREDENTIALS
        description: Full contents of a GCP service-account key JSON with read access to gs://sheerwater-datalake/ecmwf-0_4. Use this when the runtime can only inject secret values, not files.
      - name: NEURAL_GCM_SERVICE_CREDENTIALS
        description: The same key JSON under the name pbc-fetch and neuralgcm-fetch read. Used only if GCLOUD_SERVICE_CREDENTIALS is unset.
      - name: GOOGLE_APPLICATION_CREDENTIALS
        description: Path to a GCP service-account JSON file that can read gs://sheerwater-datalake/ecmwf-0_4. Used only if neither key JSON variable is set.
---

# ecmwf-04deg-reforecast-fetch

Opens the reforecast store `gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_<YYYY>.zarr`, picks the reforecast set made for the forecast date (the store init closest to its day of the year), turns the accumulated precipitation into daily amounts and writes every hindcast year's run as its own `init_time`.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD [--window-days 0] [--years 2005-2024] [--bbox N/W/S/E] [--max-lead 46D] [-v tp] -o OUT.zarr
# Newest filled reforecast init (no download)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--date` — the forecast date the reforecasts are for (required unless probing). ECMWF runs a reforecast set alongside its real-time forecasts every few days; the store holds them by that date (`init`, every 2 days). The one closest to `--date`'s month and day is used, wrapping round the new year; more than 4 days away exits 2. The store for `--date`'s year is used when there is one, else the newest.
- `--window-days` — also pool every store init within this many days of `--date` (default 0: the closest only). `4` gives about 5 inits × 20 years × 11 members = 1100 runs per cell, the pooling ECMWF uses for its m-climate.
- `--years` — hindcast years to keep: a range `2010-2020` or a list `2005,2010`. Default: all (2005–2024 in the 2025 store). Fewer years means less to download.
- `--bbox` — `N/W/S/E` decimal degrees. Omit for the whole store domain (25N–15S, 20–54.8E).
- `--variable`, `-v` — `tp` only (`pr`, `precip` accepted). Anything else exits 2.
- `--max-lead` — keep the lead days whose 24 h interval ends by this lead (`14D`, `336h`). Default `46D`. Whole days only.
- `--probe-latest [IDENT]` — print the newest filled init of the newest store and exit. `IDENT` is ignored.
- `--output`, `-o` — output Zarr, overwritten.

### Output
- Dims `(init_time, number, step, latitude, longitude)`. Each `init_time` is one reforecast run, dated when it actually started: hindcast year + the init's month and day (`2005-10-09`, …, `2024-10-09`). With `--window-days` the runs of all pooled inits are sorted together.
- Coordinates on `init_time`: `hindcast_year`, `reforecast_init` (the store init, i.e. the real-time forecast date the set belongs to). `valid_time (init_time, step) = init_time + step`.
- 11 members (0 is the control). Latitude descending, 0.4 degree spacing.
- `tp` is a rate in `mm day-1`: `step = N days` is the rain of the 24 h starting at init + N days, `data_interval = 1 day` — the same convention as `ecmwf-04deg-fetch`, so a forecast and its reforecasts line up step for step.
- Attrs: `reforecast_model_cycle` (the store year), `reforecast_inits`, `weather_skills_source = sheerwater-ecmwf-0_4-hindcast:<cycle>:<inits>`, and a `license` attr marking the data as licensed and private.

### Notes
- **Download size.** The store's chunks hold the whole domain for one run (all members and leads), ~9 MB each, so a bbox does not reduce what is read: one init with 20 years is ~180 MB (about 45 s), `--window-days 4` ~900 MB. `--years` does reduce it.
- The domain is East Africa only (25N–15S, 20–54.8E), not global like the forecast.
- An init the store has not been filled for yet is skipped (stderr says which); if none is left, exit 1.
- Hindcast years shift with the model cycle: each real-time year gets its own store (`_2025`, `_2026`, …). A date in a year without a store uses the newest store and says so on stderr.
- 29 February inits fall on 28 February in non-leap hindcast years.
- This is licensed ECMWF data. Keep the Zarr and any raw-field plots private; derived products (quantiles, probabilities, anomalies, downscaled fields) follow your data agreement.

### Errors
- Exit 1 — no credentials or access denied (the message lists the four ways to authenticate; nothing secret is printed); no reforecast store; no filled init near the date; a transport failure (retry later).
- Exit 2 — no `--date`, an unknown `--variable`, a bad `--max-lead` / `--years` / `--window-days`, a `--bbox` holding no cells, or no store init within 4 days of the date.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-10-10 --bbox 5.5/33.5/-5/42.5 --max-lead 28D -o rf_kenya.zarr
```
Writes the 20 reforecast runs (2005-10-09 … 2024-10-09) made with the 2025-10-09 set, 11 members each, daily rain rates for four weeks over Kenya.

## Details

**Weekly m-climate.** `aggregate-temporal --period "7 day"` (it aggregates `step`), then `convert-to-totals`, then `summarize-dim --dim init_time --dim number --method median` (or `mean`, `max`, …) for a weekly climatology of the model at each lead week. Add `--window-days 4` for a larger sample.

**Forecast vs. its reforecasts.** Fetch the forecast with `ecmwf-04deg-fetch` for the same date and `--bbox`, and aggregate both the same way. Then `difference` (forecast minus the reforecast mean from `summarize-dim`) gives the anomaly against the model's own climate, free of model bias.

**Quantile-mapping downscaling.** Use the weekly reforecast totals as the model sample: `downscale --algorithm q-q --qq-model rf_weekly.zarr --qq-reference chirps_weekly_clim.zarr --reference-grid chirps_weekly_clim.zarr`. Pick the same week and the same season in both.

**Bias and skill.** `step-to-time` does not apply here (it expects one init). To compare each run with observations, `select --dim init_time` one year, run `step-to-time`, and compare with `chirps-fetch` or `imerg-fetch` for the same dates, or loop over the years.
