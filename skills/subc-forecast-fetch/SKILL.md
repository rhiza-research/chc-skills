---
name: subc-forecast-fetch
description: "Fetch one SubX model's real-time ensemble forecast from CHC SubC for an init date as a weather-skills forecast Zarr: every member at every daily lead (32-45 days) of precipitation, temperatures, dewpoint, pressure, winds, radiation, soil moisture, snow, or pressure-level geopotential height and winds. Models: ECCC-GEPS8, EMC-GEFSv12_CPC, ESRL-FIMr1p1, GMAO-GEOS_V2p1_5daily, GMAO-GEOS_V3, NCEP-CFSv2, RSMAS-CCSM4. Use when a task needs an individual SubC model rather than the multi-model mean, e.g. with subc-climatology-fetch for that model's anomalies."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads an Icechunk mirror on GCS first (gs://africa-forecasting-data; Google credentials from GCLOUD_SERVICE_CREDENTIALS, GOOGLE_APPLICATION_CREDENTIALS, ADC or the gcloud login, anonymous otherwise), then CHC's anonymous FTP server (ftp.chc.ucsb.edu) for anything the mirror lacks.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 0
    note: init dates are irregular per model (CFSv2 daily; others weekly or twice weekly); probe for the latest
---

# subc-forecast-fetch

Reads one SubX model's forecast for one init from the CHC SubC archive and writes it as an ensemble forecast Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL --date YYYY-MM-DD [-v VAR ...] [--bbox N/W/S/E] [--source auto|mirror|chc] [--workers N] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL --probe-latest [VAR]
```

### Arguments
- `--model` — `ECCC-GEPS8`, `EMC-GEFSv12_CPC`, `ESRL-FIMr1p1`, `GMAO-GEOS_V2p1_5daily`, `GMAO-GEOS_V3`, `NCEP-CFSv2`, `RSMAS-CCSM4`. One per call. Required.
- `--date` — init date. Required unless probing. Each model has its own init calendar; a date without a file fails and lists that month's inits.
- `--variable`, `-v` — repeat for several. Default: `pr`. Surface: `pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`, `psl`, `uas`, `vas`, `huss`, `cape`, `rlut`, `dlwrf`, `dswrf`, `ulwrf`, `uswrf`, `rad`, `hfls`, `hfss`, `stx`, `sty`, `mrro`, `mrso`, `sic`, `snc`, `snod`, `snwd`, `swe`. Pressure levels: `zg`, `ua`, `va`, `wap`. Not every model publishes every variable; a missing one fails naming the file.
- `--bbox` — `N/W/S/E`. Keeps every grid cell that overlaps the box. Default: global.
- `--source` — `auto` (default): the mirror, then the CHC FTP server for inits, variables or models it lacks (stderr says which was used); `mirror`: mirror only, exit 1 if it lacks the init; `chc`: CHC FTP only. The output is identical either way.
- `--workers` — concurrent file reads, one per variable. Default: 2.
- `--probe-latest` — print the newest init for `--model` (and `VAR`, default `pr`) as `YYYY-MM-DD`, or `none`, and exit, no `-o`. stderr gives its age.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(number, step, latitude, longitude)`, plus `level` for pressure-level variables: `(number, step, level, latitude, longitude)`. Scalar `time` = init; `valid_time = time + step`.
- `step` = 0, 1, 2 … days, each the start of a 24 h lead day (`data_interval = 1 day`); each value is that day's mean. The same steps as `subc-climatology-fetch --outlook daily`, so the two subtract directly.
- `number`: the model's member ids (ECCC-GEPS8 0-20 with 0 the control, NCEP-CFSv2 1-4, ...).
- `pr` in `mm day-1` (a rate); `tas`, `tasmax`, `tasmin` in `degree_Celsius`; `psl` in `hPa`; everything else in its source units (`ts`/`tdps` K, `zg` m, winds m s-1, fluxes W m-2). `level` in hPa.
- Attrs: `model`, `initialization_date`, `weather_skills_source = chc-subc-forecast`.

### Notes
- Anomalies: fetch the same model's climatology with `subc-climatology-fetch --model M --date <same init>` and `difference` forecast minus climatology; the climatology has no `number` dim, so it broadcasts over members. Aggregate both the same way first (`aggregate-temporal` on `step`, or the climatology's `--outlook Nd`) for weekly anomalies.
- For the multi-model mean outlook, use `subc-mme-fetch`.
- `pr` is a daily rate: run `aggregate-temporal` then `convert-to-totals` for weekly totals.
- Mirror: `gs://africa-forecasting-data/chc-mirror/experimental/SubC/forecasts/{var}.icechunk`, one Icechunk (Zarr v3) repository per variable with one group per model, written by the same conversion as this skill and chunked 1 init x all members x all leads x 30° tiles, so a `--bbox` reads only its tiles. It holds `pr` from 2026-09-01 and is not yet updated automatically; newer inits and other variables come from CHC. Set `SUBC_FORECAST_MIRROR` to another `gs://` prefix or a local folder, or to `none` to skip it.
- Performance from CHC: each variable's file is downloaded whole over FTP (about 4 MB/s), then subset; `--bbox` does not reduce the transfer. Measured 2026-11-09 over Kenya with `-v pr -v tas`: NCEP-CFSv2 about 12 s, ECCC-GEPS8 about 1 min. Files are 30-140 MB per variable (ECCC-GEPS8 and GMAO-GEOS_V3 the largest).

### Errors
- No file for the init → exit 1 listing the inits published that month (or that none are).
- Variable not published for the model → exit 1 naming the missing file.
- Mirror unreachable, unauthorized or missing the init/variable → a one-line stderr note and a CHC read (`--source mirror`: exit 1 instead).
- FTP 421 (too many connections) and temporary 4xx replies are retried with backoff (15 s up to 4 min); the skill then exits 1; wait a few minutes and rerun with `--workers 1`.
- Timeouts and dropped connections are retried the same way, then exit 1 as "unreachable"; any other failure exits 1 as "unreadable" with the underlying error.

### Example
```bash
INIT=$(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model NCEP-CFSv2 --probe-latest pr)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model NCEP-CFSv2 --date "$INIT" -v pr -v tas --bbox 5.5/33.5/-5/42.5 -o cfs_kenya.zarr
```
Writes the latest CFSv2 forecast's daily precipitation and 2 m temperature for all 4 members and 44 lead days over Kenya.

## Details

Server layout and file contents: [references/REFERENCE.md](references/REFERENCE.md).
