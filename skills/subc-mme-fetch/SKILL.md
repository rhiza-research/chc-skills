---
name: subc-mme-fetch
description: Fetch the CHC SubC multi-model-ensemble (MME) subseasonal forecast from the public global archive for one init date — stitching 7-, 15-, and 30-day mean and anomaly NetCDFs for pr/tas/tasmax/tasmin/tdps/ts into a single weather-skills forecast Zarr. Use when a task needs SubC MME fields (means or anomalies) for clipping, comparison, or plotting.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from data.chc.ucsb.edu/experimental/SubC; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 0
    note: CHC SubC experimental global MME archive; coverage depends on published init dates
---

# subc-mme-fetch

Downloads the CHC SubC **global** multi-model-ensemble archive for one
initialization date and writes a weather-skills forecast envelope Zarr.

Source layout:

```
https://data.chc.ucsb.edu/experimental/SubC/{07_day|15_day|30_day}/global/archive/
  mme_mean_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
  mme_anom_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
```

Each NetCDF is a single 2D window field. This skill stitches **3 leads × 6
variables × mean+anomaly = 36 files** (fewer if `-v` restricts variables) into
one dataset.

## When to use

- Need SubC MME means and/or anomalies for an init date on the global 1° grid.
- Downstream clipping, aggregation, comparison, or plotting.

Prefer other fetchers when you need daily steps, per-model members, or a
non-SubC source.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD \
    [--bbox N/W/S/E] [-v VAR ...] [--workers N] -o <path.zarr>
```

### Arguments

- `--date` — init date `YYYY-MM-DD` (required). Matches the archive filename
  date / `window_start`.
- `--bbox` — optional spatial subset `N/W/S/E`.
- `--variable`, `-v` — restrict to named variables (repeatable). Allowed:
  `pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`. Default: all six. For each
  selected variable both mean and anomaly are fetched.
- `--workers` — concurrent remote opens (default 4).
- `--output`, `-o` — output Zarr path.

### Output

Classic forecast envelope:

- scalar `time` — init
- `step` — `[7, 15, 30]` days (`timedelta64`) — forecast **lead** (time since
  init). Each SubC MME field is itself a mean/sum over the calendar window from
  init through that lead (`window_start`…`window_end` in the source NetCDF), so
  the lead equals the source `window_days`, but `step` is the lead coordinate.
- `latitude`, `longitude` — 1° grid (or bbox subset)
- data variables: bare name for the MME mean (`pr`, `tas`, …) and
  `{var}_anomaly` for the anomaly (`pr_anomaly`, `tas_anomaly`, …)

Precipitation means are **window sums** in `mm` (not rates). Temperature-family
fields are window means (source units typically `K`; converted to standard
display units when classified).

Stamped with `weather_skills_source=chc-subc-mme`.

### Provenance

The decorator stamps `weather_skills_history` on write. Inspect with the
`provenance` skill when available.

## Example

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 \
    --bbox 20/30/-20/120 -v ts -v pr -o /tmp/subc.zarr
```
