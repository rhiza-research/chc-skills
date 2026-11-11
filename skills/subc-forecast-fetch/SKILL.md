---
name: subc-forecast-fetch
description: "Fetch one SubX model's real-time ensemble forecast from CHC SubC for an init date as a weather-skills forecast Zarr: every member at every daily lead (32-45 days) of precipitation, temperatures, dewpoint, pressure, winds, radiation, soil moisture, snow, or pressure-level geopotential height and winds. Models: ECCC-GEPS8, EMC-GEFSv12_CPC, ESRL-FIMr1p1, GMAO-GEOS_V2p1_5daily, GMAO-GEOS_V3, NCEP-CFSv2, RSMAS-CCSM4. For precipitation over Africa on the latest weekly run it uses CHC's bias-corrected forecast (CHIRPS3 climatology, 0.05° grid) when that covers the request. Use when a task needs an individual SubC model rather than the multi-model mean, e.g. with subc-climatology-fetch for that model's anomalies."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads an Icechunk mirror on GCS first (gs://africa-forecasting-data; Google credentials from GCLOUD_SERVICE_CREDENTIALS, GOOGLE_APPLICATION_CREDENTIALS, ADC or the gcloud login, anonymous otherwise), then CHC's anonymous FTP server (ftp.chc.ucsb.edu) for anything the mirror lacks. Bias-corrected precipitation comes from the same FTP server.
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
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL --date YYYY-MM-DD [-v VAR ...] [--bbox N/W/S/E] [--source auto|mirror|chc] [--bias-corrected auto|yes|no] [--workers N] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL --probe-latest [VAR]
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL --bbox N/W/S/E --bias-corrected yes --probe-latest
```

### Arguments
- `--model` — `ECCC-GEPS8`, `EMC-GEFSv12_CPC`, `ESRL-FIMr1p1`, `GMAO-GEOS_V2p1_5daily`, `GMAO-GEOS_V3`, `NCEP-CFSv2`, `RSMAS-CCSM4`. One per call. Required.
- `--date` — init date. Required unless probing. Each model has its own init calendar; a date without a file fails and lists that month's inits.
- `--variable`, `-v` — repeat for several. Default: `pr`. Surface: `pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`, `psl`, `uas`, `vas`, `huss`, `cape`, `rlut`, `dlwrf`, `dswrf`, `ulwrf`, `uswrf`, `rad`, `hfls`, `hfss`, `stx`, `sty`, `mrro`, `mrso`, `sic`, `snc`, `snod`, `snwd`, `swe`. Pressure levels: `zg`, `ua`, `va`, `wap`. Not every model publishes every variable; a missing one fails naming the file.
- `--bbox` — `N/W/S/E`. Keeps every grid cell that overlaps the box. Default: global.
- `--source` — `auto` (default): the mirror, then the CHC FTP server for inits, variables or models it lacks (stderr says which was used); `mirror`: mirror only, exit 1 if it lacks the init; `chc`: CHC FTP only. The output is identical either way.
- `--bias-corrected` — `auto` (default): use CHC's bias-corrected daily precipitation instead of the raw model output when it covers the request (see Bias-corrected forecasts below), else the raw forecast with a one-line stderr note saying why; `yes`: bias-corrected or exit 1; `no`: raw only.
- `--workers` — concurrent file reads, one per variable. Default: 2.
- `--probe-latest` — print the newest init for `--model` (and `VAR`, default `pr`) as `YYYY-MM-DD`, or `none`, and exit, no `-o`. stderr gives its age. With `--bias-corrected yes` and a `--bbox`, print the init of the current bias-corrected run instead, or `none` when CHC's files are stale.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(number, step, latitude, longitude)`, plus `level` for pressure-level variables: `(number, step, level, latitude, longitude)`. Scalar `time` = init; `valid_time = time + step`.
- `step` = 0, 1, 2 … days, each the start of a 24 h lead day (`data_interval = 1 day`); each value is that day's mean. The same steps as `subc-climatology-fetch --outlook daily`, so the two subtract directly.
- `number`: the model's member ids (ECCC-GEPS8 0-20 with 0 the control, NCEP-CFSv2 1-4, ...).
- `pr` in `mm day-1` (a rate); `tas`, `tasmax`, `tasmin` in `degree_Celsius`; `psl` in `hPa`; everything else in its source units (`ts`/`tdps` K, `zg` m, winds m s-1, fluxes W m-2). `level` in hPa.
- Attrs: `model`, `initialization_date`, `bias_corrected` (`yes`/`no`), `weather_skills_source = chc-subc-forecast`; bias-corrected output adds `bias_correction` and its `source` is the bias-corrected file.

### Bias-corrected forecasts
CHC publishes daily precipitation bias-corrected to the CHIRPS3-sat climatology, on the CHIRPS 0.05° grid, for NCEP-CFSv2, EMC-GEFSv12_CPC, GMAO-GEOS_V3 and RSMAS-CCSM4 over three regions: EA (25N-13S, 20-52E), WA (20N-0, 20W-30E) and SA (2S-36S, 10-55E). CHC refreshes them once a week with each model's latest run up to a Thursday, overwriting the previous one: there is no archive.

`auto` uses them only when all of these hold, else it reads the raw forecast:
- `-v pr` alone (no other variable), one of those four models, and a `--bbox` inside one region (the smallest region holding it is used);
- `--date` is the run CHC currently holds (CFSv2 and GEFSv12 start on the Thursday; GEOS_V3 and CCSM4 on their last run before it, e.g. 2026-11-02 and 2026-11-01 for the 2026-11-05 issue). Get it with `--bias-corrected yes --probe-latest`;
- that run is current: it started in the 7 days up to the latest Thursday, and CHC's 30-day processed copy starts on that Thursday (GEFSv12, which has no day-0 lead, the Friday). A file left over from an earlier week is treated as stale.

The output has the same shape as the raw forecast (all members, every lead from the init), on the 0.05° grid, so grids differ between bias-corrected and raw output. The whole regional file is downloaded (CHC stores it uncompressed and unchunked): about 2.5 min for CFSv2 or GEOS_V3 over East Africa, about 10 min for GEFSv12 (2 GB). A run cached with `auto` before CHC's weekly update stays raw: rerun with `--bias-corrected yes`.

### Choosing bias-corrected or raw
When a request could use either (pr, one of the four models, an African `--bbox`, the current weekly run) and the user has not said which, ask them before fetching, with these trade-offs:
- Bias-corrected: rainfall amounts match the CHIRPS3 climatology, so totals and thresholds (e.g. "more than 50 mm") are more realistic over East, West and southern Africa, and it suits verification against CHIRPS; finer 0.05° grid. But only the current weekly run exists (no archive), only pr and four models, and the download is slow (about 2.5 min for CFSv2, about 10 min for GEFSv12). CHC does not document the correction method.
- Raw: any date, model, variable and region; fast (seconds from the mirror); the same 1° grid as `subc-climatology-fetch`, so model anomalies work. But it keeps the model's own rainfall biases.

Comparisons across several inits, or against `subc-climatology-fetch` (a raw-model climatology), need raw for every init: bias-corrected exists for one run only, and mixing the two makes the comparison unfair. With no user to ask (a scheduled or unattended run), don't ask: pass `--bias-corrected no` for multi-init or anomaly work, keep `auto` otherwise, and say in the answer which one was used.

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
- `--bias-corrected yes` when the bias-corrected forecast does not cover the request (variable, model, `--bbox`, an older `--date`, or stale files) → exit 1 naming the reason (exit 2 for the variable, model and `--bbox` cases when probing).
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
