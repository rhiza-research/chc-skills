---
name: iod-enso-fetch
description: "Download Bureau of Meteorology (BoM) Indian Ocean Dipole and ENSO products: the current IOD, Niño3.4, Relative Niño3.4, or SOI monitoring graph and its index values, or an ACCESS-S IOD / Niño3.4 outlook plume and its monthly ensemble-mean and category-probability table. Also downloads NOAA CPC's Relative Oceanic Niño Index (RONI), 3-month seasons from 1950. Writes a PNG figure or a time-series Zarr. Use when a task needs BoM's official IOD or ENSO index values, monitoring graph, or ACCESS-S forecast, or a long ENSO / RONI record, e.g. for correlations."
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.bom.gov.au and www.cpc.ncep.noaa.gov; no credentials required. BoM blocks non-browser User-Agents.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: Observation PNGs and text files overwritten in place (weekly IOD / Niño; daily SOI). ACCESS-S forecast plumes and monthly JSON are dated in the outlooks archive (fortnightly, recently weekly).
---

# iod-enso-fetch

Downloads a BoM IOD or ENSO graph as a PNG, or the index values behind it as a Zarr. `--index roni` downloads NOAA's full RONI record (1950 on) as a Zarr in about a second.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index INDEX [--product observation|forecast] [--format figure|data] [--date YYYY-MM-DD] -o OUT.png|OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index INDEX --product forecast --probe-latest
```

### Arguments
- `--index` — `iod` (Dipole Mode Index), `enso` (same as `relative-nino3.4`), `relative-nino3.4`, `nino3.4` (traditional), `soi` (30-day SOI, observation only), or `roni` (NOAA CPC Relative Oceanic Niño Index; observation Zarr only). Required.
- `--product` — `observation` (the current monitoring series) or `forecast` (an ACCESS-S outlook issue). Default: `observation`.
- `--format` — `figure` (PNG) or `data` (Zarr). Default: `data` when `-o` ends in `.zarr`, otherwise `figure`. A mismatched suffix (`data` with `.png`, `figure` with `.zarr`) exits 2.
- `--date` — forecast issue date; uses the latest issue on or before it (stderr notes the snap). Default: latest issue. Forecast only.
- `--probe-latest` — print a date and exit, no `-o`; takes no value. Forecast: the latest ACCESS-S issue. Observation: the graph's `Last-Modified` day (the text file's with `--format data`), or `none`.
- `--output`, `-o` — PNG or Zarr path.

### Output
- Figure: the BoM PNG with `weather_skills_history` in its metadata. Forecast figures are the plume of 99 members (grey), ensemble mean (green), and observations (black).
- Observation data: `(time)` with one variable; `time` is the week-ending (SOI: 30-day-window-ending) date.

  | `--index` | Variable | Units | `data_interval` |
  |---|---|---|---|
  | `iod` | `iod_mode_index` | `degree_Celsius` | `7 day` |
  | `enso`, `relative-nino3.4` | `relative_nino34` | `degree_Celsius` | `7 day` |
  | `nino3.4` | `nino34` | `degree_Celsius` | `7 day` |
  | `soi` | `soi` | `1` | `1 day` |
- RONI data: `(time)` with `roni` in `degree_Celsius`, one value per overlapping 3-month season (DJF, JFM, ... NDJ). `time` is the season's middle month (DJF 1950 → 1950-01-01) and `time_bounds` spans the three months; there is no `data_interval`.
- Forecast data: `(time)` at month starts with `time_bounds`, scalar `init_time` (issue date), the index variable as the ensemble mean, and `prob_below` / `prob_neutral` / `prob_above` in `percent` of members beyond the event threshold (IOD ±0.4 °C, Niño ±0.8 °C).

### Notes
- Observations are current only: BoM overwrites the same files, so `--date` with `--product observation` exits 2. The full history is in each file.
- ACCESS-S issues start 2018-08-11. Relative Niño3.4 forecasts start with the 2025-07-01 issue; `enso` uses traditional Niño3.4 before that and `relative-nino3.4` exits 2.
- The 99 plume members exist only in the PNG; the Zarr holds the ensemble mean and category percentages.
- The current month's mean can be NaN in the forecast table.
- `--index roni` with `--product forecast`, `--format figure`, or a non-`.zarr` `-o` exits 2. BoM's `relative-nino3.4` is weekly but starts 2008; use `roni` for longer records.
- Neighbouring RONI values share two months, so they are strongly autocorrelated; account for that when testing a correlation's significance.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index iod --product forecast --format data -o iod_fc.zarr
```
Writes the latest ACCESS-S monthly IOD ensemble mean and below / neutral / above percentages.

## Details

More examples:
```bash
# Current Relative Niño3.4 graph, then its weekly values
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index enso -o enso.png
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index enso -o enso.zarr
# NOAA RONI, 1950 to the latest season
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index roni -o roni.zarr
# IOD plume from a past issue
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --index iod --product forecast --date 2024-06-08 -o iod_20240608.png
```

Source URLs per index: [references/REFERENCE.md](references/REFERENCE.md).
