---
name: iod-enso-fetch
description: Fetch Bureau of Meteorology (BoM) Indian Ocean Dipole (IOD) and ENSO products — current observation timeseries (weekly IOD / DMI, Relative Niño3.4, traditional Niño3.4, 30-day SOI) from https://www.bom.gov.au/climate/enso/, or ACCESS-S outlooks from https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs. Default --format figure writes the monitoring graph or forecast plume PNG. --format data (or -o *.zarr) writes a weather-skills Zarr of the official index values: clim_data text for observations, or ACCESS-S monthly ensemble-mean and category frequencies from the outlooks archive JSON (not the 99-member plume). Use --product forecast --date for a historical issue, omit --date for the latest. Does not produce gridded fields or compute an index from a grid.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.bom.gov.au; no credentials required. BoM blocks non-browser User-Agents.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  version: "0.0.1"
  catalog-group: fetchers
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: Observation PNGs and text files overwritten in place (weekly IOD / Niño; daily SOI). ACCESS-S forecast plumes and monthly JSON are dated in the outlooks archive (fortnightly, recently weekly).
---

# iod-enso-fetch

Downloads a Bureau of Meteorology IOD or ENSO **figure** (PNG) or the matching
**official index values** (Zarr). There is no Zarr input.

`--format figure` (default, or any `-o` that is not `.zarr`) writes the graph
BoM publishes on the monitoring / outlooks pages.

`--format data` (or `-o` ending in `.zarr`) writes a 1-D weather-skills
standard dataset of the numbers behind that graph.

`--product observation` (default) is the **current** monitoring timeseries
(historical values are already in the file; BoM overwrites the same path).

`--product forecast` is an ACCESS-S product from the Southern Hemisphere
outlooks archive. Omit `--date` for the latest issue, or pass
`--date YYYY-MM-DD` for a historical issue (snaps to the latest archive
date on or before that day).

To **compute** a Dipole Mode Index from a temperature-anomaly Zarr, use
`iod-mode-index` instead.

## Observation graphs and text

Source: https://www.bom.gov.au/climate/enso/

| `--index` | Graph | Text series |
| --- | --- | --- |
| `iod` | `https://www.bom.gov.au/clim_data/IDCK000072/iod1.png` | `iod_1.txt` |
| `enso` | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.png` | `rnino_3.4.txt` |
| `relative-nino3.4` | Same as `enso` | Same as `enso` |
| `nino3.4` | `https://www.bom.gov.au/clim_data/IDCK000072/nino3_4.png` | `nino_3.4.txt` |
| `soi` | `https://www.bom.gov.au/clim_data/IDCKGSM000/soi30.png` | `soi.txt` |

Text rows are `week_start,week_end,value` (SOI is a rolling 30-day window
updated daily). The Zarr `time` coordinate is the window-end date.

BoM does **not** publish dated observation snapshots. `--date` with
`--product observation` is an error. `--probe-latest` prints the file's
`Last-Modified` day (or `none`).

## Forecast plumes and monthly tables

Source: https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs

Archive index: `https://www.bom.gov.au/climate/ocean/outlooks/archive/archive_index.json`

```
https://www.bom.gov.au/climate/ocean/outlooks/archive/YYYYMMDD/plumes/sstOutlooks.<region>.hr.png
https://www.bom.gov.au/climate/ocean/outlooks/archive/YYYYMMDD/plumes/sstOutlooks.<region>.json
```

| `--index` | Plume / JSON stem |
| --- | --- |
| `iod` | `sstOutlooks.iod` |
| `enso` | `sstOutlooks.rnino34` on/after 2025-07-01; `sstOutlooks.nino34` before |
| `relative-nino3.4` | `sstOutlooks.rnino34` (2025-07-01 onward only) |
| `nino3.4` | `sstOutlooks.nino34` (traditional, all dates) |
| `soi` | not available (observation only) |

ACCESS-S issues start 2018-08-11. The PNG plume shows 99 ensemble members
(grey), the mean (green), and observations (black). BoM does **not**
publish those 99 member series as numbers.

The JSON is the table behind the outlooks page: monthly **ensemble mean**
plus **percent of members** below / neutral / above the event threshold
(IOD ±0.4 °C, Niño ±0.8 °C). The current month can be NaN when the issue
is after the 11th (month-to-date observation on the graph instead).

`--probe-latest` with `--product forecast` prints the latest issue
`YYYY-MM-DD`.

## When to use

- Current IOD / ENSO observation graph from BoM.
- Official weekly IOD / Niño or daily 30-day SOI **values** as a Zarr.
- Latest or historical ACCESS-S IOD / Niño3.4 forecast plume.
- ACCESS-S monthly ensemble-mean and category probabilities as a Zarr.

## When not to use

- 99-member ACCESS-S plume values — those exist only as the PNG.
- Computed DMI from an anomaly Zarr — `iod-mode-index`.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    --index iod|enso|nino3.4|relative-nino3.4|soi \
    [--product observation|forecast] [--format figure|data] \
    [--date YYYY-MM-DD] -o <out.png|out.zarr>
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    --index iod --product forecast --probe-latest
```

### Arguments

- `--index` — required. See tables above.
- `--product` — `observation` (default) or `forecast`.
- `--format` — `figure` (PNG) or `data` (Zarr). Default: `data` when `-o`
  ends in `.zarr`, otherwise `figure`.
- `--date` — forecast issue `YYYY-MM-DD`. Default: latest archive issue.
- `--probe-latest` — print a `YYYY-MM-DD` (or `none`) and exit. No `-o`.
- `--output`, `-o` — PNG or Zarr output path.

### Output

**figure** — a PNG at `--output`. The decorator stamps `weather_skills_history`
into the PNG metadata.

**data (observation)** — a Zarr with `time` and one index variable:

| `--index` | Variable | Units | Native spacing |
| --- | --- | --- | --- |
| `iod` | `iod_mode_index` | `degree_Celsius` | `7 day` |
| `enso` / `relative-nino3.4` | `relative_nino34` | `degree_Celsius` | `7 day` |
| `nino3.4` | `nino34` | `degree_Celsius` | `7 day` |
| `soi` | `soi` | `1` | `1 day` (30-day rolling window) |

**data (forecast)** — a Zarr with `time` (month start), scalar `init_time`
(issue date), the same index variable (ensemble mean), and `prob_below` /
`prob_neutral` / `prob_above` (`percent`). Month lengths vary, so native
geometry is CF `time_bounds` rather than a fixed `data_interval`.

## Examples

```bash
# Current IOD / ENSO observation timeseries figure
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod -o /tmp/iod.png
uv run skills/iod-enso-fetch/scripts/fetch.py --index enso -o /tmp/enso.png

# Official weekly IOD / Relative Niño3.4 values
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod --format data -o /tmp/iod.zarr
uv run skills/iod-enso-fetch/scripts/fetch.py --index enso -o /tmp/enso.zarr

# Latest ACCESS-S IOD and Relative Niño3.4 forecast plumes
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast -o /tmp/iod_fc.png
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index enso --product forecast -o /tmp/enso_fc.png

# ACCESS-S monthly ensemble mean + category probabilities
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --format data -o /tmp/iod_fc.zarr

# Historical forecast issue (snaps on-or-before)
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --date 2024-06-08 -o /tmp/iod_20240608.png

# Latest published ACCESS-S issue date
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --probe-latest
```
