---
name: tc-besttrack-fetch
description: "Fetch official tropical cyclone tracks with tropycal: best tracks (HURDAT2, IBTrACS), the storms active now with their official NHC/JTWC forecasts, and operational a-deck model tracks (for example ECMWF's own ensemble tracks, EE01-EE50), written in the tc-track trajectory layout. Use when a task names an official storm (Freddy 2023, Idai, Milton), needs the observed track or intensity to compare with model output, needs the storms active now, or needs ECMWF's or NHC's own forecast tracks; then use tc-match, tc-verify and plot-tc-tracks --reference."
license: MIT
compatibility: Requires Python 3.12 and uv. Downloads HURDAT2 from nhc.noaa.gov and IBTrACS from ncei.noaa.gov (cached for 7 days).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
---

# tc-besttrack-fetch

Fetches official cyclone tracks with [tropycal](https://tropycal.github.io/tropycal/) and writes them in the same `(track, obs)` layout as `tc-track`. Every TC skill reads both kinds of file.

## Usage

```bash
# One storm, by name and season or by id
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --storm Freddy --year 2023 -o freddy.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --storm-id AL142024 -o milton.zarr

# Every storm active in a window, in a basin or passing a box
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --start 2024-12-01 --end 2024-12-31 --bbox 0/30/-35/80 -o dec2024.zarr

# The storms active now, with their latest official forecast
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --active -o active.zarr

# Operational model tracks from the a-deck (here ECMWF's own 51-member ensemble)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --storm Milton --year 2024 --models ecmwf-ens --init 2024-10-07T00 -o milton_ecmwf.zarr
```

### Arguments
Pass exactly one of these modes:
- `--storm NAME --year YYYY`: one storm by name. Names are reused, so the year is required.
- `--storm-id ID`: one storm by its official id, e.g. `AL142024` or `SH182019`. The id prefix picks the basin.
- `--start YYYY-MM-DD --end YYYY-MM-DD`: every storm active in the window, with its full track. Needs `--basin` or `--bbox`. With `--bbox N/W/S/E`, only storms that pass the box during the window are kept.
- `--active`: storms active now, from NHC (and JTWC when reachable) with each one's latest official forecast. Other basins come from IBTrACS ACTIVE, whose provisional tracks can be a few days old.

Other options:
- `--basin`: `auto` (default), `north_atlantic`, `east_pacific`, `west_pacific`, `north_indian`, `south_indian`, `south_pacific` or `south_atlantic`. With `auto`, a name is searched basin by basin.
- `--source`:
  - `auto` (default): HURDAT2 for the North Atlantic and East/Central Pacific, falling back to IBTrACS when HURDAT2 doesn't have the storm yet (HURDAT2 is updated once a year); IBTrACS for the other basins.
  - `hurdat` or `ibtracs`: force one source.
- `--ibtracs-mode`: `jtwc` (default; US agency values, 1-minute winds, consistent with the Saffir–Simpson classes) or `wmo` (the RSMCs' values, mostly 10-minute winds).
- `--models IDS --init YYYY-MM-DDTHH`: write a-deck model tracks for one storm and one init instead of the best track. IDS is a comma list of ATCF ids (`EMX`, `OFCL`, `AVNO`, …) or groups:

  | Group | Ids |
  |---|---|
  | `ecmwf-ens` | `EC00`, `EE01`–`EE50` |
  | `ecmwf-mean` | `EEMN` |
  | `ecmwf` | `ECMO`, `EMX` |
  | `gefs-ens` | `AC00`, `AP01`–`AP30` |
  | `gefs-mean` | `AEMN` |
  | `gfs` | `AVNO` |
  | `official` | `OFCL` |

  Ensemble groups get `number` (control 0), so `tc-strike-probability` and `plot-tc-tracks` treat them like a tc-track ensemble.
- `--refresh`: download the best-track files again.
- `--output`, `-o`: output Zarr.

### Output
The tc-track trajectory layout (`featureType=trajectory`) on `(track, obs)`:

| Name | Kind | Meaning |
|---|---|---|
| `track` | coord (track) | `SH182019` for a best track; `AL142024:EE07:2024100700` for a model track (storm:model:init) |
| `storm`, `storm_name` | coords (track) | official id and name |
| `kind` | coord (track) | `best_track`, `official_forecast` or `adeck:<ID>` |
| `forecast_reference_time` | coord (track) | the init, for forecast tracks |
| `number` | coord (track) | ensemble member, for `ecmwf-ens` / `gefs-ens` |
| `time`, `latitude`, `longitude` | coords (track, obs) | fix time and centre (lon −180–180) |
| `msl` | Pa | central pressure |
| `wind_max` | m s-1 | maximum sustained wind (the official estimate; averaging period in `tc_wind_averaging`) |
| `category` | 1 | tc-track's intensity class: 0 TD (<34 kt), 1 TS, 2 Cat 1, 3 Cat 2, 4 Cat 3+ (≥96 kt) |
| `storm_type` | text | official type per fix: TD, TS, HU, TY, EX, SS, LO, DB, … |

Missing pressure or wind is NaN. Attrs give the source (`tc_source`, `tc_source_info`) and the storm id, name and year, and `tc_notes` records anything that fell back.

### Notes
- **Wind is not like model wind.** Best-track winds are 1- or 10-minute sustained peak estimates. Model `wind_max` is the grid-scale wind, which reads low on coarse grids. Compare classes with that in mind. `tc-match --blend adjust` anchors model intensity on the observed value at the init.
- **a-deck coverage.** Model tracks exist for NHC basins (back to 1954) and JTWC basins (from 2019). ECMWF ensemble tracks (`EE01`–`EE50`) appear in recent NHC seasons, but not every storm or init has every model; a missing init exits 2 and lists the inits within a day.
- **JTWC realtime** is often unreachable (expired certificates, blocked downloads). `--active` then notes it and uses IBTrACS ACTIVE for the JTWC basins.
- **Cache.** Files are kept in `~/.cache/chc-skills/tc-besttrack/` (or `$TC_BESTTRACK_CACHE`) for 7 days. A basin's IBTrACS file is 10–80 MB; reading it takes about 10 s.

### Errors
- Exits 2 for:
  - an unknown storm, listing the seasons where the name occurs;
  - an ambiguous name, listing the ids;
  - a missing `--year`, or two modes given;
  - `--models` without `--init` or without one storm;
  - an init that the models don't have.
- Exits 1 when a download fails, or when no tracks are found.

## Details
tropycal reads the cached files through its local-file options (`atlantic_url`, `pacific_url`, `ibtracs_url`). Its progress output goes to stderr, so stdout stays clean.
