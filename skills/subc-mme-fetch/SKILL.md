---
name: subc-mme-fetch
description: Fetch one CHC SubC multi-model-ensemble (MME) outlook — 7d, 15d, or 30d — for selected climate variables (pr/tas/tasmax/tasmin/tdps/ts mean + anomaly) from the public global archive into a weather-skills forecast Zarr. Use when a task needs a single SubC MME outlook map for clipping, comparison, or plotting; use --probe-latest with a variable to find the latest published init. SubC publishes irregularly: the latest init can be weeks old with its window already over (the probe warns on stderr), so check it covers the period you need. Precip is a window sum in mm; every field carries aggregation_period = the outlook length. --bbox keeps every 1° cell that overlaps the box.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches the public GCS mirror first (gs://sheerwater-public-datalake/chc-mirror), then falls back to data.chc.ucsb.edu. No credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  version: "0.0.5"
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 0
    note: CHC SubC experimental global MME archive; coverage depends on published init dates per outlook
---

# subc-mme-fetch

Downloads **one** CHC SubC **global** multi-model-ensemble outlook for one
initialization date and writes a weather-skills forecast envelope Zarr.

Outlooks are separate forecasts — pick exactly one with `--outlook`:

| `--outlook` | Archive folder | Window |
| --- | --- | --- |
| `7d` | `07_day` | init → init+7d |
| `15d` | `15_day` | init → init+15d |
| `30d` | `30_day` | init → init+30d |

Source layout (GCS mirror of the CHC SubC archive):

```
gs://sheerwater-public-datalake/chc-mirror/experimental/SubC/{07_day|15_day|30_day}/global/archive/
  mme_mean_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
  mme_anom_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
```

HTTPS equivalent:

```
https://storage.googleapis.com/sheerwater-public-datalake/chc-mirror/experimental/SubC/...
```

If a file is missing on the mirror, fetch falls back to
`https://data.chc.ucsb.edu/experimental/SubC/`. `--probe-latest` lists the
GCS mirror (JSON API), not the origin HTML index.

Each NetCDF is a single 2D window field. This skill fetches mean + anomaly for
the selected climate variables at **one** outlook only (not all three leads).

## When to use

- Need a SubC MME mean and/or anomaly map for one outlook length on the global
  1° grid.
- Downstream clipping, aggregation, comparison, or plotting.

Prefer other fetchers when you need daily steps, per-model members, or a
non-SubC source.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD --outlook 7d|15d|30d \
    [--bbox N/W/S/E] [-v VAR ...] [--workers N] -o <path.zarr>

uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --outlook 7d|15d|30d \
    --probe-latest [VAR]
```

### Arguments

- `--date` — archive init date `YYYY-MM-DD` (required unless probing). Matches
  the archive filename date / `window_start`.
- `--outlook` — required. One of `7d`, `15d`, `30d`. Selects which SubC
  outlook forecast to fetch (three separate products; one per call).
- `--bbox` — optional spatial subset `N/W/S/E`. Padded by half a grid cell
  (0.5°), so every 1° cell that overlaps the box is kept, including cells
  whose centre lies just outside (e.g. the 42°E column for Kenya's
  41.9°E edge). Mask with the polygon afterwards (`clip-region --geojson`).
- `--variable`, `-v` — climate variables (repeatable). Allowed: `pr`, `tas`,
  `tasmax`, `tasmin`, `tdps`, `ts`. Default: all six. For each selected
  variable both mean and anomaly are fetched.
- `--workers` — concurrent remote opens (default 4).
- `--output`, `-o` — output Zarr path (not needed with `--probe-latest`).
- `--probe-latest [VAR]` — print the latest available init `YYYY-MM-DD` for
  this `--outlook` on stdout and exit. stderr says how many days old that
  init is and, when init + outlook is before today, warns that its window has
  already ended (e.g. latest 15d init 2026-09-16 is valid to 2026-10-01).
  **Pass a climate variable** (e.g.
  `--probe-latest ts`) unless you truly need the latest date common to
  **all** six variables — probing every variable is slower and stricter.
  Optional `VAR` must be one of the climate variables above. Does not
  download fields.

### Output

Classic single-lead forecast envelope:

- scalar `time` — the archive **init** (`--date`), as for every other
  forecast fetcher
- `step` — one value, `0 days`: the window starts at the init. The window
  length (`7`, `15`, or `30` days) is the variable's `aggregation_period`, so
  the window is `time + step` … `time + step + aggregation_period`
  (e.g. `--date 2025-12-01 --outlook 7d` covers 2025-12-01 … 2025-12-08)
- attrs `initialization_date` and `outlook_valid_date` (init + outlook days,
  the end of the window)
- `latitude`, `longitude` — 1° grid (or bbox subset)
- data variables: bare name for the MME mean (`pr`, `tas`, …) and
  `{var}_anomaly` for the anomaly (`pr_anomaly`, `tas_anomaly`, …)

Precipitation means are **window sums** in `mm` (not rates), with
`cell_methods: time: sum`. Temperature-family fields are window means
(`time: mean`; source units typically `K`; converted to standard display
units when classified). Every variable carries `aggregation_period` = the
outlook length (`7 day`, `15 day`, `30 day`), so `plot` and
`chc-plot-theme --for` size precipitation palettes to the window. Do not run
`convert-to-totals` on `pr`: it is already a total.

Each variable's `long_name` is a plain quantity (`Precipitation`,
`Precipitation anomaly`, `Surface temperature anomaly`, …) for colorbar
labels; the outlook and init are in its `comment` attr.

Stamped with `weather_skills_source=chc-subc-mme` and `outlook=7d|15d|30d`.

### Provenance

The output carries a `weather_skills_history` provenance chain; inspect it
with the `provenance` skill.

## Examples

```bash
# Latest init for the 7-day ts outlook (prefer a variable on probe-latest)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --outlook 7d --probe-latest ts

# Fetch that 7-day outlook for ts + pr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 --outlook 7d \
    --bbox 20/30/-20/120 -v ts -v pr -o /tmp/subc_7d.zarr

# Separate 30-day outlook fetch
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 --outlook 30d \
    -v ts -o /tmp/subc_30d.zarr
```
