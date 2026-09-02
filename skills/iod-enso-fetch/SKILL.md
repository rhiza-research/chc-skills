---
name: iod-enso-fetch
description: Fetch Bureau of Meteorology (BoM) Indian Ocean Dipole (IOD) and ENSO graphs — current observation timeseries (weekly IOD / DMI, Relative Niño3.4, traditional Niño3.4, 30-day SOI) from https://www.bom.gov.au/climate/enso/, or ACCESS-S forecast plumes (IOD and Niño3.4) from the outlooks archive at https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs. Use --product forecast --date for a historical issue, omit --date for the latest. Does not produce gridded data or compute an index.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.bom.gov.au; no credentials required. BoM blocks non-browser User-Agents.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  version: "0.0.1"
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: Observation PNGs overwritten in place (weekly IOD / Niño; daily SOI). ACCESS-S forecast plumes are dated in the outlooks archive (fortnightly, recently weekly).
---

# iod-enso-fetch

Downloads a Bureau of Meteorology IOD or ENSO **figure** and writes it to
`--output`. There is no Zarr input.

`--product observation` (default) is the **current** monitoring timeseries
(historical values are already on the graph; BoM overwrites the same file).

`--product forecast` is an ACCESS-S ensemble plume from the Southern
Hemisphere outlooks archive. Omit `--date` for the latest issue, or pass
`--date YYYY-MM-DD` for a historical issue (snaps to the latest archive
date on or before that day).

To **compute** a Dipole Mode Index from a temperature-anomaly Zarr, use
`iod-mode-index` instead.

## Observation graphs

Source: https://www.bom.gov.au/climate/enso/

| `--index` | Graph | URL |
| --- | --- | --- |
| `iod` | Weekly IOD / Dipole Mode Index | `https://www.bom.gov.au/clim_data/IDCK000072/iod1.png` |
| `enso` | Operational Relative Niño3.4 | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.png` |
| `relative-nino3.4` | Same as `enso` | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.png` |
| `nino3.4` | Traditional Niño3.4 | `https://www.bom.gov.au/clim_data/IDCK000072/nino3_4.png` |
| `soi` | 30-day Southern Oscillation Index | `https://www.bom.gov.au/clim_data/IDCKGSM000/soi30.png` |

BoM does **not** publish dated observation PNG snapshots. `--date` with
`--product observation` is an error. `--probe-latest` prints the graph's
`Last-Modified` day (or `none`).

## Forecast plumes

Source: https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs

Archive index: `https://www.bom.gov.au/climate/ocean/outlooks/archive/archive_index.json`

```
https://www.bom.gov.au/climate/ocean/outlooks/archive/YYYYMMDD/plumes/sstOutlooks.<region>.hr.png
```

| `--index` | Plume file |
| --- | --- |
| `iod` | `sstOutlooks.iod.hr.png` |
| `enso` | `sstOutlooks.rnino34.hr.png` on/after 2025-07-01; `sstOutlooks.nino34.hr.png` before |
| `relative-nino3.4` | `sstOutlooks.rnino34.hr.png` (2025-07-01 onward only) |
| `nino3.4` | `sstOutlooks.nino34.hr.png` (traditional, all dates) |
| `soi` | not available (observation only) |

ACCESS-S issues start 2018-08-11. Grey lines are the 99-member ensemble;
green is the mean; black is observations.

`--probe-latest` with `--product forecast` prints the latest issue
`YYYY-MM-DD`.

## When to use

- Current IOD / ENSO observation graph from BoM.
- Latest or historical ACCESS-S IOD / Niño3.4 forecast plume.

## When not to use

- Analyzable index values — figure only. Weekly IOD numbers:
  `https://www.bom.gov.au/clim_data/IDCK000072/iod_1.txt`.
- Computed DMI from an anomaly Zarr — `iod-mode-index`.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    --index iod|enso|nino3.4|relative-nino3.4|soi \
    [--product observation|forecast] [--date YYYY-MM-DD] \
    -o <out.png>
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    --index iod --product forecast --probe-latest
```

### Arguments

- `--index` — required. See tables above.
- `--product` — `observation` (default) or `forecast`.
- `--date` — forecast issue `YYYY-MM-DD`. Default: latest archive issue.
- `--probe-latest` — print a `YYYY-MM-DD` (or `none`) and exit. No `-o`.
- `--output`, `-o` — PNG output path.

### Output

A PNG at `--output`. The decorator stamps `weather_skills_history` into the PNG
metadata.

## Examples

```bash
# Current IOD / ENSO observation timeseries
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod -o /tmp/iod.png
uv run skills/iod-enso-fetch/scripts/fetch.py --index enso -o /tmp/enso.png

# Latest ACCESS-S IOD and Relative Niño3.4 forecast plumes
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast -o /tmp/iod_fc.png
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index enso --product forecast -o /tmp/enso_fc.png

# Historical forecast issue (snaps on-or-before)
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --date 2024-06-08 -o /tmp/iod_20240608.png

# Latest published ACCESS-S issue date
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --probe-latest
```
