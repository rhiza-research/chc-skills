---
name: tc-track
description: "Detect and track tropical cyclones (hurricanes, typhoons) in gridded forecasts, ensembles, reanalyses or climate-model output, from sea-level pressure plus optional winds, geopotential and orography. A pure-Python port of the TempestExtremes DetectNodes/StitchNodes method with the Zarzycki and Ullrich (2017) criteria, adapted automatically to the grid spacing and time step. Works on ECMWF IFS-ENS, S2S, GEFS, ERA5, CMIP6 and others. Use when a task asks where storms are or will go, for storm counts, or before tc-strike-probability, tc-phase-space or plot-tc-tracks."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_track.py *)
metadata:
  catalog-group: transforms
---

# tc-track

Finds tropical-cyclone centres in every map and links them into tracks, one set per ensemble member.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_track.py -i FIELDS.zarr [-i MORE.zarr ...] [--preset auto|zu2017|coarse-daily] \
  [--no-warm-core] [criterion overrides] -o TRACKS.zarr
```

### Arguments
- `--input`, `-i`: gridded Zarr(s) holding the fields below. Repeat the flag for fields kept in separate stores. Grids that differ are interpolated onto the pressure grid.
- `--preset`: how the criteria are set. Default: `auto`.
  - `auto` derives them from the input (see Details).
  - `zu2017` uses the published Zarzycki and Ullrich (2017) values, meant for 6-hourly data on 0.25–1° grids.
  - `coarse-daily` is meant for 1.5° daily S2S data and is experimental.
- `--no-warm-core`: skip the warm-core test even when geopotential is present.
- Criterion overrides. Each replaces the preset's value, and every value used is written to the output attrs as `tc_<name>`:
  - `--merge-dist`: degrees. Of candidates closer together than this, only the deepest is kept.
  - `--psl-delta`: Pa. The pressure rise that closes the contour around a low.
  - `--psl-dist`: degrees. The pressure contour must close within this distance.
  - `--warm-core-delta`: m. The fall in 300–500 hPa thickness that closes the warm core.
  - `--warm-core-dist`: degrees. The warm-core contour must close within this distance.
  - `--warm-core-offset`: degrees. The thickness maximum may sit this far from the low.
  - `--wind-dist`: degrees. Radius for the maximum wind recorded at each centre.
  - `--range`: degrees. The farthest a centre may move between two linked times.
  - `--max-gap`: hours of missing detections allowed inside a track.
  - `--min-time`: hours. The shortest track kept, from first to last point.
  - `--wind-min`, `--wind-hours`: a track must reach `--wind-min` m s-1 for `--wind-hours` hours.
  - `--max-lat`, `--lat-hours`: a track must stay within `--max-lat` degrees of the equator for `--lat-hours` hours.
  - `--max-zs`, `--zs-hours`: a track must stay below `--max-zs` m surface height for `--zs-hours` hours.
  - Hours are counted as points: 60 h means 10 points at 6-hourly steps and 3 points at daily steps.
- `--output`, `-o`: output Zarr.

### Fields it looks for
| Field | Names recognised | Needed for |
|---|---|---|
| Sea-level pressure (required) | `msl`, `psl`, `pressure_reduced_to_mean_sea_level`, `mean_sea_level_pressure`, `prmsl`, `slp`, `mslp` | candidate lows and the closed contour |
| 10 m wind | `u10`/`v10`, `uas`/`vas`, `wind_u_10m`/`wind_v_10m`, `10m_u_component_of_wind`/`…v…`, or a speed: `si10`, `sfcWind`, `wind_speed_10m` | wind filter, `wind_max` |
| 850 hPa wind (when there is no 10 m wind) | `u`/`v`, `ua`/`va`, `u_component_of_wind` on a level dim, or `u850`/`v850`, `wind_u_850hpa` | as above; thresholds are divided by 0.8 |
| Geopotential (height) at 300 and 500 hPa, or 250 and 850 hPa | `gh`, `zg`, `z`, `geopotential_height`, `geopotential` on a level dim (`level`, `plev`, `pressure_level`, …), or `z300`/`gh_500`/`geopotential_height_500hpa` | warm-core test |
| Surface height | `zs`, `orog`, `geopotential_at_surface`, `surface_geopotential` | high-ground filter, `zs` |
| Relative vorticity | `vo`, `vorticity`, `relative_vorticity` (e.g. from `derive`) | `vort_max` (recorded, not a criterion) |

Units are converted with pint, so these all work: pressure in Pa or hPa; geopotential in m2 s-2 or as height in m; pressure levels in hPa or Pa (CMIP `plev`). Longitudes may be 0–360 or −180–180, and latitude may run either way. The time axis may be a `time` dim, or an init `time` plus a `step` dim (forecasts), optionally with `number`/`member`/`realization` members. Model calendars (`noleap`, `360_day`) are kept. A missing optional field turns off its criterion; stderr and the `tc_notes` attr say which.

### Output
A CF `featureType=trajectory` Zarr on `(track, obs)`, padded with NaN after each track ends:

| Name | Kind | Meaning |
|---|---|---|
| `track` | coord (track) | id: `m003-002` is member 3's second track; `001`, `002`, … without members |
| `number` | coord (track) | ensemble member, when the input has members |
| `forecast_reference_time` | coord (track), or a global attr for one init | the forecast init |
| `time`, `latitude`, `longitude` | coords (track, obs) | valid time and centre position (lon in −180–180) |
| `msl` | Pa | central pressure |
| `wind_max` | m s-1 | strongest wind within `--wind-dist` |
| `ts_strength` | 1 | 1 where `wind_max` reaches the tropical-storm threshold for this grid (Walsh et al. 2007) |
| `category` | 1 | intensity class from `wind_max` in knots: 0 tropical depression (<34 kt), 1 tropical storm (34–63), 2 Cat 1 (64–82), 3 Cat 2 (83–95), 4 Cat 3+ (≥96); CF `flag_values`/`flag_meanings`. 850 hPa winds are scaled by 0.8 first. Grid-scale model winds under-resolve the peak, so coarse data reads low |
| `zs` | m | surface height at the centre, when given |
| `vort_max` | s-1 | strongest cyclonic vorticity within `--wind-dist`, when given |

Global attrs record every criterion (`tc_<name>`), the preset, the grid spacing and time step, the wind level, notes, references and `ensemble_members` (the full member list, so members without a storm still count in `tc-strike-probability`). No tracks gives `track` of size 0 and `found 0 tracks` on stderr.

### The shared track layout
Every TC skill reads and writes this trajectory layout, so model tracks (this skill), official tracks (`tc-besttrack-fetch`) and their products mix freely:

- dims `(track, obs)`, padded with NaN; `featureType=trajectory`;
- coords `time`, `latitude`, `longitude` on `(track, obs)`; `track` ids; optional `number` and `forecast_reference_time` per track;
- variables `msl` (Pa), `wind_max` (m s-1), `category` (codes 0–4 on the wind in knots, edges 34/64/83/96 kt).

Optional additions written by the other skills:

| Name | Dims | From | Meaning |
|---|---|---|---|
| `kind` | track | tc-besttrack-fetch | `best_track`, `official_forecast` or `adeck:<ID>` |
| `storm`, `storm_name` | track | tc-besttrack-fetch, tc-match | official id and name |
| `storm_type` | track, obs | tc-besttrack-fetch | official system type (TD, TS, HU, EX, …) |
| `match_distance_km`, `match_gap_hours` | track | tc-match | how the model track was matched |
| `segment` | track, obs | tc-match --blend | `observed` or `forecast` |
| `B`, `neg_vtl`, `neg_vtu`, `tropical` | track, obs | tc-phase-space | Hart phase space |
| `phase` | track, obs | tc-phase-space, tc-besttrack-fetch | `pre-genesis`, `tropical`, `extratropical`, `remnant` or `non-tropical` |

Standalone scripts cannot import each other, so the category edges and the great-circle helpers are copied into each skill; keep them identical.

### Notes
- **Domain edge.** A closed contour must fit inside the input. Leave a margin of at least `--psl-dist` (about 6°) around the region of interest. Lows whose contour reaches the edge are dropped.
- **Recommended inputs:**
  - ECMWF IFS-ENS 0.25° from `dynamical-fetch`, with `-v msl u10 v10 gh` (6-hourly)
  - ERA5 from `arco-era5-fetch`, with `-v mean_sea_level_pressure -v 10m_u_component_of_wind -v 10m_v_component_of_wind -v geopotential_at_surface`. Add `-v geopotential` only for short windows, since each hour reads all levels.
  - S2S from `ecmwf-fetch -v msl u10 v10`. Its pressure levels are control-only, so the warm-core test is off for members.
  - CMIP6 from `cmip6-fetch` with a 6-hourly table (`6hrPlevPt` `psl`, `uas`, `vas`, `zg`) where the model provides one.
- **Resolution.** At 1.5° and daily steps, storms are under-resolved: pressure minima are shallow and winds weak. Treat those tracks as guidance on genesis and broad paths, not intensity.
- **Cost.** About 30 ms per map for a 60 × 80 grid, plus one closed-contour test per candidate low. A 51-member, 60-step regional ensemble takes a few minutes.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_track.py -i ifs_ens_msl_wind_gh.zarr -o tracks.zarr
```
This tracks every IFS-ENS member over the fetched box, with criteria set for 0.25°/6-hourly data, and prints the criteria it chose.

## Details

### Detection (DetectNodes)
For each map:
1. **Candidates.** Every local minimum of sea-level pressure (8 neighbours) is a candidate. Of candidates within `--merge-dist` of each other (great-circle distance), only the deepest is kept.
2. **Closed contour.** A breadth-first flood fill runs from the minimum through cells below minimum + `--psl-delta`. The contour is closed if the fill never reaches a cell beyond `--psl-dist` or the edge of the domain. Missing cells act as walls.
3. **Warm core.** The same test runs on 300−500 hPa thickness, turned upside down: thickness must fall by `--warm-core-delta` (6 gpm, i.e. 58.8 m2 s-2) within `--warm-core-dist` of its maximum. That maximum is searched within `--warm-core-offset` of the low. If there is no 300 hPa level, the 250−850 hPa thickness is used instead.
4. **Recorded values.** For each centre, the central pressure, the strongest wind and cyclonic vorticity within `--wind-dist`, and the surface height.

### Stitching (StitchNodes)
- Candidates are taken earliest first, deepest first. Each unused candidate starts a path.
- The path joins the nearest unused candidate within `--range` at the next time. If there is none, the next times are tried, as long as the time skipped is no more than `--max-gap` hours.
- A path is kept when it lasts at least `--min-time` and meets the wind, latitude and surface-height point counts.

### What `auto` chooses
Δx is the grid spacing (the larger of the lat and lon steps) and Δt is the time step.

| Criterion | `zu2017` | `auto` |
|---|---|---|
| `psl-dist` | 5.5° | max(5.5°, 3Δx) |
| `merge-dist` | 6° | max(6°, 2Δx) |
| `warm-core-dist`, `-offset` | 6.5°, 1° | max(6.5°, 3Δx), max(1°, Δx) |
| `wind-dist` | 2° | max(2°, 1.5Δx) |
| `range` | 8° per step | 8°·Δt/6 h (at least 2°) for Δt ≤ 6 h; 8° + 0.25°/h·(Δt − 6 h) above that |
| `min-time` | 54 h | 54 h for Δt ≤ 6 h; above that, the largest multiple of Δt not over 54 h, and at least 2Δt |
| `wind-min` | 10 m s-1 | 10 m s-1 × W(Δx) / W(0.25°), with W from Walsh et al. 2007 |
| `psl-delta` | 200 Pa | 200 Pa; 100 Pa when the pressure is a period mean (`cell_methods` says mean). This 100 Pa value is a heuristic, not from the papers. |

A time step over 24 h exits 2. Typical choices:

| Input | Δx, Δt | psl-dist | range | min-time | wind-min (10 m) | TS threshold |
|---|---|---|---|---|---|---|
| ERA5 | 0.25°, 1 h | 5.5° | 2° | 54 h | 10.0 | 17.0 |
| IFS-ENS, GEFS | 0.25°, 6 h | 5.5° | 8° | 54 h | 10.0 | 17.0 |
| CMIP6 HighResMIP-type | 0.5°, 6 h | 5.5° | 8° | 54 h | 9.5 | 16.2 |
| CMIP6 typical | 1°, 6 h | 5.5° | 8° | 54 h | 8.6 | 14.7 |
| S2S | 1.5°, 24 h | 5.5° | 12.5° | 48 h | 8.0 | 13.6 |

### References
- Ullrich and Zarzycki 2017, GMD 10:1069 (TempestExtremes)
- Ullrich et al. 2021, GMD 14:5023 (TempestExtremes v2.1)
- Zarzycki and Ullrich 2017, GRL 44:1141 (TC criteria)
- Walsh et al. 2007, J. Climate 20:2307 (resolution-dependent wind thresholds)
- Bourdin et al. 2022, GMD 15:6759 (four trackers compared on ERA5)

### Errors
- Exits 2 when there is no sea-level pressure, a field has no units, a field has extra dims, there are fewer than two times, or the time step exceeds 24 h.
