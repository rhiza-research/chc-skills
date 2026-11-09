---
name: subc-climatology-fetch
description: "Fetch one SubX model's lead-dependent hindcast climatology (1999-2016) from CHC SubC for an init date as a weather-skills forecast Zarr: the ensemble mean or pooled 5-95th percentiles of precipitation, near-surface air, max, min, dewpoint, or surface temperature, per lead day or over the 7/15/30-day window. Models: ECCC-GEPS8, EMC-GEFSv12_CPC, ESRL-FIMr1p1, GMAO-GEOS_V2p1_5daily, GMAO-GEOS_V3, NCEP-CFSv2, RSMAS-CCSM4. Use when a task needs a model's own climatology, e.g. to turn that model's forecast into anomalies or tercile/extreme categories."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads data.chc.ucsb.edu directly over HTTPS range requests (no mirror yet). No credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 0
    note: static climatology; every calendar day (including 02-29) has a file, so any init date works
---

# subc-climatology-fetch

Reads one SubX model's hindcast climatology for the init month-day and writes it as a forecast Zarr with the same leads as that model's forecast.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD --model MODEL [--outlook daily|Nd] [--window-start DAYS] [--stat mean|quantiles] [-v VAR ...] [--bbox N/W/S/E] [--workers N] -o OUT.zarr
```

### Arguments
- `--date` — the forecast init date the climatology is for. Its month-day picks the file; the full date becomes the init `time`, so the output lines up with a forecast from that init. Required.
- `--model` — `ECCC-GEPS8`, `EMC-GEFSv12_CPC`, `ESRL-FIMr1p1`, `GMAO-GEOS_V2p1_5daily`, `GMAO-GEOS_V3`, `NCEP-CFSv2`, `RSMAS-CCSM4`. One per call. Required.
- `--outlook` — `daily` (default): one field per lead day. `Nd` for any N (`7d`, `10d`, `14d`, `30d`, …): one window of N lead days, the precip total or temperature mean. `7d`, `15d` and `30d` from the init match `subc-mme-fetch --outlook`.
- `--window-start` — lead day the `Nd` window starts on, in days after the init. Default: 0 (the init day). E.g. `--outlook 14d --window-start 14` is weeks 3–4. Needs a window `--outlook`.
- `--stat` — `mean` (default): the ensemble mean. `quantiles`: the 5th, 20th, 33.3rd, 66.7th, 80th and 95th percentiles pooled over every hindcast and member, on a `quantile` dim (`0.05` … `0.95`). Quantiles exist only for `--outlook daily` and for `7d`, `15d`, `30d` from the init.
- `--variable`, `-v` — `pr` (precipitation), `tas` (near-surface air temperature), `tasmax`, `tasmin`, `tdps` (dewpoint), `ts` (surface temperature); repeat for several. Default: every variable the model has (table below).
- `--bbox` — `N/W/S/E`. Keeps every grid cell that overlaps the box. Default: global.
- `--workers` — concurrent file reads, one per variable. Default: 2.
- `--output`, `-o` — output Zarr.

| Model | Variables |
| --- | --- |
| `ECCC-GEPS8` | pr tas tasmax tasmin tdps ts |
| `EMC-GEFSv12_CPC` | pr tas |
| `ESRL-FIMr1p1` | pr tas tasmax tasmin tdps ts |
| `GMAO-GEOS_V2p1_5daily` | pr tas tasmax tasmin tdps ts |
| `GMAO-GEOS_V3` | pr tas (0.5° grid; every other model is 1°) |
| `NCEP-CFSv2` | pr tas ts |
| `RSMAS-CCSM4` | pr tas tasmax tasmin ts |

The number of lead days is the model's hindcast length, e.g. 36 for GMAO-GEOS_V3, 39 for ECCC-GEPS8, 44 for NCEP-CFSv2 and 45 for RSMAS-CCSM4.

### Output
- Dims `([quantile,] step, latitude, longitude)`, scalar `time` = `--date`.
- `daily`: `step` = 0, 1, 2 … days, each the start of a 24 h lead day (`data_interval = 1 day`). `pr` is a rate in `mm day-1`.
- `Nd`: one `step` = the window start (`--window-start`, default `0 days`) and `aggregation_period = N day`. `pr` is the window total in `mm` (`cell_methods: time: sum`); temperatures are window means.
- Window source: `7d`/`15d`/`30d` from the init read CHC's precomputed window fields. Every other window is the sum (pr) or mean (temperatures) of the daily ensemble means over its lead days, which gives the same numbers CHC's 7/15/30-day means have; a cell missing any lead day in the window is NaN. The `window_source` attr says which.
- `tas`, `tasmax`, `tasmin` in `degree_Celsius`; `ts` and `tdps` in `K`.
- Variables keep their short names (`pr`, `tas`, …) for both stats.
- Attrs: `model`, `calendar_day`, `climatology_years = 1999-2016`, `climatology_window_days = 10`, `outlook`, `window_source`, `statistic`, `initialization_date`, `weather_skills_source = chc-subc-climatology`.

### Notes
- Each climatology pools that model's hindcasts initialized within ±10 days of the calendar day, 1999-2016, so it has the model's own drift and bias at each lead. Subtract it from the same model's forecast (`difference`) for an anomaly; compare against `--stat quantiles` for tercile or extreme categories.
- The 7d/15d/30d windows match `subc-mme-fetch`'s outlook windows, but that skill's anomalies are of the multi-model mean against its own climatology.
- Percentiles do not add up: the 95th percentile of a 10-day total is not the sum of ten daily 95th percentiles. That is why quantiles of other windows are refused rather than computed.
- For a series of consecutive windows (every week of the forecast), fetch `--outlook daily` and run `aggregate-temporal` on `step`, then `convert-to-totals` for precip.
- Performance: the server sends about 0.5-1 MB/s. Only the requested fields are read: opening a file costs about 30 s, a daily field (also what a computed window reads) about 20 s more, and a precomputed 7/15/30-day field under a second. `--bbox` does not reduce the transfer (each field is one HDF5 chunk); GMAO-GEOS_V3 fields are four times larger.
- `pr` window totals are already totals; do not convert them to totals again.

### Errors
- Variable not published for the model → exit 2 listing the model's variables.
- `--stat quantiles` with a window other than `7d`/`15d`/`30d` from the init, `--window-start` without a window `--outlook`, or an `--outlook` that is not `daily`/`Nd` → exit 2.
- Window reaching past the model's last lead day → exit 1 giving the model's lead range.
- File missing on the server (HTTP 404) → exit 1 naming the URL.
- HTTP 429: data.chc.ucsb.edu rate-limits bursts for several minutes. The skill retries with backoff (15 s up to 4 min) and then exits 1; wait a few minutes and rerun with `--workers 1`.
- Network failures and 5xx are retried the same way, then exit 1 as "unreachable".

### Examples
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-11-09 --model NCEP-CFSv2 -v pr --bbox 5.5/33.5/-5/42.5 -o cfs_pr_clim_kenya.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-11-09 --model ECCC-GEPS8 -v pr --outlook 7d --stat quantiles -o geps8_pr7d_quantiles.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-11-09 --model NCEP-CFSv2 -v pr -v tas --outlook 14d --window-start 14 --bbox 5.5/33.5/-5/42.5 -o cfs_wk34_clim_kenya.zarr
```
The first writes CFSv2's daily-by-lead precipitation climatology for 9 November inits over Kenya. The second writes ECCC-GEPS8's 7-day precipitation total percentiles for the same inits, globally. The third writes CFSv2's weeks 3–4 precipitation total and mean temperature over Kenya.

## Details

Server layout and file contents: [references/REFERENCE.md](references/REFERENCE.md).
