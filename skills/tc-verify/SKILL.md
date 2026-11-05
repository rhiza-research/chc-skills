---
name: tc-verify
description: "Verify model cyclone tracks against official best tracks from tc-besttrack-fetch. Track mode gives position, along-track and cross-track error, and central pressure and wind error, per point and summarised by lead time (member mean, ensemble mean track, control, spread). Detection mode gives the hit rate and false-alarm ratio of tc-track over a period. Use to answer how good a forecast or reanalysis track was, or to tune tc-track and tc-phase-space settings against IBTrACS."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_verify.py *)
metadata:
  catalog-group: transforms
---

# tc-verify

Compares model tracks with the official record.

## Usage

```bash
# Errors of matched forecast (or reanalysis) tracks
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_verify.py --tracks MATCHED.zarr --reference BEST.zarr -o VERIFY.zarr

# Detection over a period: run tc-track on ERA5 for a season, fetch every storm in that window
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_verify.py --tracks ERA5_TRACKS.zarr --reference SEASON.zarr \
  --mode detection [--bbox N/W/S/E] [--min-wind-kt 34] -o DETECT.zarr
```

### Arguments
- `--tracks`:
  - track mode: `tc-match` output, whose `storm` says which official storm each track is. Plain `tc-track` output also works when `--reference` holds one storm.
  - detection mode: plain `tc-track` output.
- `--reference`: `tc-besttrack-fetch` output. Only its `best_track` tracks are used.
- `--mode`: `track` (default) or `detection`.
- `--bbox`: detection mode only counts official storms with fixes inside N/W/S/E. Use it for a regional model or reanalysis domain.
- `--min-wind-kt`: detection mode only counts storms that reach this wind (default 34 kt, tropical-storm strength). Weaker depressions are often not in the model at all.
- `--min-overlap`, `--match-hours`, `--max-km`: the matching settings for detection mode, as in `tc-match`.
- `--output`, `-o`: output Zarr.

### Output (track mode)
The tracks, plus these variables on `(track, obs)`:

| Variable | Units | Meaning |
|---|---|---|
| `lead_hours` | hours | since the init (for a reanalysis: since the track's first fix) |
| `position_error_km` | km | great-circle distance to the official centre at the same time |
| `along_track_error_km` | km | positive ahead of the official storm (too fast), negative behind |
| `cross_track_error_km` | km | positive to the right of the official motion |
| `msl_error` | Pa | model minus official central pressure |
| `wind_error` | m s-1 | model minus official maximum wind |

And a summary on `(storm_id, lead)`:
- `n_tracks`;
- the member means of each error (`mean_position_error_km`, `mean_along_track_error_km`, `mean_cross_track_error_km`, `mean_msl_error`, `mean_wind_error`);
- `ensemble_mean_position_error_km`: the error of the ensemble mean position, taken where at least 20% of the members have the storm;
- `control_position_error_km`;
- `spread_km`: the mean distance of the members to the ensemble mean.

The official track is interpolated to the model times. Model points outside its span, and `observed` points of a `tc-match --blend` track, are NaN.

### Output (detection mode)
- `hit` and `match_distance_km` on `(storm_id, member)`;
- `hit_fraction` per official storm;
- `track_storm` per model track (`''` is a false alarm);
- attrs `tc_verify_hit_rate`, `tc_verify_false_alarm_ratio`, and the numbers of storms and tracks.

The official storms counted are those with fixes during the model tracks' time span (and in `--bbox`) that reach `--min-wind-kt`. stderr lists the storms that were missed.

### Notes
- **Intensity errors are mostly resolution.** Grid-scale model winds read low and pressures high on 0.25° and coarser grids. ERA5 for Idai (2019) gives about −13 m s-1 and +900 Pa, with a 47 km mean position error. `tc-match --blend adjust` removes the bias near the init only.
- **Tuning.** Detection mode over an ERA5 season, against IBTrACS for the same window, gives the hit rate and false-alarm ratio for a `tc-track` preset or `tc-phase-space --tropical-only` setting. Compare runs to choose thresholds.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_verify.py --tracks idai_matched.zarr --reference idai_best.zarr -o idai_verify.zarr
```

### Errors
- Exits 2 when:
  - an input is not a track file;
  - track mode gets tracks without `storm` and several reference storms;
  - a matched storm is missing from the reference.
- Exits 1 when `--tracks` is empty.
