---
name: kmsa-wrf-fetch
description: "Read one KMSA (Kenya Meteorological Department) WRF forecast run, the 7- to 8-day run or the 3-day daily run, at 3-hourly steps on a ~0.036° Kenya grid, as a weather-skills forecast Zarr: total precipitation and 2 m temperature. Reads the private daily mirror in gs://sheerwater-datalake/kmsa-wrf with Google Cloud service-account credentials. Use when a task needs the KMSA WRF forecast."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads private GCS gs://sheerwater-datalake/kmsa-wrf via gcsfs, with NEURAL_GCM_SERVICE_CREDENTIALS (the raw service-account key JSON), GOOGLE_APPLICATION_CREDENTIALS (a key file path), or Application Default Credentials. Can also read a local copy of the archive.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 1
    note: KMSA WRF runs from August 2023, mirrored daily from the KMSA NWP workstation; some runs are missing or truncated
  openclaw:
    requires:
      env:
        - NEURAL_GCM_SERVICE_CREDENTIALS
    primaryEnv: NEURAL_GCM_SERVICE_CREDENTIALS
    envVars:
      - name: NEURAL_GCM_SERVICE_CREDENTIALS
        description: Full contents of a GCP service-account key JSON with read access to gs://sheerwater-datalake/kmsa-wrf.
      - name: GOOGLE_APPLICATION_CREDENTIALS
        description: Path to a GCP service-account JSON file with that access. Used only if NEURAL_GCM_SERVICE_CREDENTIALS is unset.
---

# kmsa-wrf-fetch

Reads one KMSA WRF run and writes its precipitation and 2 m temperature as a single-init forecast Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD [--product 7day|daily] [--bbox N/W/S/E] [-v tp|t2m ...] [--root GS_OR_DIR] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--product 7day|daily] --probe-latest
```

### Arguments
- `--date` — run start date, as in the archive filename. Required unless probing.
- `--product` — `7day` (default): the long run, 7 days (57 steps) until April 2026, 8 days (65 steps) after. `daily`: the 3-day run (26 steps).
- `--bbox` — `N/W/S/E`. Default: the whole WRF domain (5.6°S–6.2°N, 32.7–43.1°E).
- `--variable`, `-v` — `tp` (total precipitation) or `t2m` (2 m temperature); repeat for both. Default: both.
- `--root` — `gs://` prefix or local folder holding `7_day/` and `Daily_Data/`. Default: `gs://sheerwater-datalake/kmsa-wrf`.
- `--probe-latest` — print the latest run start date for `--product` (or `none`) and exit, no `-o`.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(step, latitude, longitude)`, 3-hourly `step` from 0, scalar `time` = run start: 06Z for `7day`, 03Z for `daily`.
- `tp` is a `mm day-1` rate per 3-hour step, de-accumulated from WRF's running total (`rainc` + `rainnc`); `step = 0` is the first 3 hours. Do not run deaccumulate after this skill.
- `t2m` is instantaneous 2 m temperature in `degree_Celsius`, on the same `step` labels (the value at each step's start).
- stderr gives the run start and how far it reaches.

### Notes
- The bucket is private. Set `NEURAL_GCM_SERVICE_CREDENTIALS` (the service-account key JSON itself) or `GOOGLE_APPLICATION_CREDENTIALS` (a key file path) in the environment on the first call, including `--probe-latest`, unless Application Default Credentials are set up. Never print the value. A local `--root` needs no credentials.
- Runs are not filled. A truncated run (the model stopped early) comes back with fewer steps; check the last `step` before computing weekly totals.
- The skill refuses archive files that are not a raw run: files whose contents start on a different day than their name, files already de-accumulated and split by day (some daily files, March–May 2024), and the July 2023 files in an older format. The error says which.
- For daily or weekly totals, aggregate-temporal then convert-to-totals.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-10-13 --bbox 1/36/-3/38 -v tp -o wrf_7day_nairobi.zarr
```
Writes 3-hourly rain rates around Nairobi from the 2026-10-13 06Z 7-day run.
