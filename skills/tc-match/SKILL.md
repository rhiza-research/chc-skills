---
name: tc-match
description: "Pick, for each ensemble member, the tc-track cyclone that is an official storm from tc-besttrack-fetch (Freddy, Idai, Milton, the storms active now), and optionally put the observed best-track history in front of each forecast, either as is or with the forecast shifted onto the observed position and intensity at the init (as the operational interpolated aids do). Use after tc-track when a task is about one named storm: its forecast track, strike probability, rainfall or verification."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_match.py *)
metadata:
  catalog-group: transforms
---

# tc-match

Keeps only the model tracks that are official storms. This is the ECMWF-style view, "only the storms that are listed", built from our own detections.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_match.py --tracks TRACKS.zarr --reference BEST.zarr \
  [--storm ID|NAME] [--min-overlap 12] [--match-hours 48] [--max-km 400] [--max-gap 36] [--max-speed 20] \
  [--blend none|append|adjust] [--adjust-hours 24] [--intensity-adjust-hours 48] -o MATCHED.zarr
```

### Arguments
- `--tracks`: `tc-track` output, from an ensemble, a single forecast, a reanalysis or several inits.
- `--reference`: `tc-besttrack-fetch` output. Its `best_track` tracks are the official storms; official-forecast and a-deck tracks are ignored.
- `--storm`: keep one official storm, by id (`WP302026`) or name (`IN-FA`).
- `--min-overlap`: hours a model track must share with the official track (default 12).
- `--match-hours`: the match distance is averaged over the first this-many hours of overlap (default 48). Only early overlap is used because forecast error grows with lead time.
- `--max-km`: the largest mean distance accepted (default 400 km).
- `--max-gap`: in real time the official record usually ends before the forecast starts (IBTrACS ACTIVE lags by a day or more). When the official track ends at most this many hours before a model track starts (default 36; 0 turns it off), it is extrapolated along its last 12 h of motion and compared with the model track's first fix.
- `--max-speed`: m s-1 (default 20; 0 turns it off). A matched track is cut at its first step faster than this equatorward of 30°, and the points after it are removed (counted in `points_cut`). Tropical cyclones rarely move faster than about 15 m s-1 there. Faster steps are the stitcher hopping to another low, typically heat lows after landfall, which would otherwise spread false strike probability inland. Poleward of 30°, fast extratropical motion is kept.
- `--blend`:
  - `none` (default): the matched tracks as they are.
  - `append`: the observed best-track fixes before the forecast go in front of each matched track.
  - `adjust`: as `append`, but each forecast is first moved onto the observed state at its first fix. The position offset fades linearly to zero over `--adjust-hours` (default 24). The `msl` and `wind_max` offsets fade over `--intensity-adjust-hours` (default 48), and `category` is recomputed. This is how the operational "interpolated" aids (EMXI, AVNI) are made, and it removes the model's start-up error, including the low bias of grid-scale intensity near the init.
- `--output`, `-o`: output Zarr.

### Output
The matched model tracks, best match first, with:

| Name | Meaning |
|---|---|
| `storm`, `storm_name` | the official storm each track belongs to |
| `match_distance_km` | mean distance to the official track over the first `--match-hours` |
| `points_cut` | points removed by `--max-speed` |
| `match_gap_hours` | 0 when the tracks overlap; otherwise the hours bridged by extrapolation |
| `segment` | with `--blend`: `observed` or `forecast` per point |
| `blend_offset_km`, `blend_offset_msl`, `blend_offset_wind` | with `--blend adjust`: observed minus model at the first forecast fix |

`tc_match_missing_members` (JSON) lists, per storm, the ensemble members without a matched track. Their absence keeps them in the denominator of `tc-strike-probability`, because `ensemble_members` is carried through. Observed points have NaN in model-only variables (`ts_strength`, `zs`, …).

### Notes
- **Assignment.** Each model track joins at most one storm, and each storm takes at most one track per member (and init), assigned best score first.
- **Genesis forecasts** (the model forms the storm later than observed, or before it was named) match through their overlap like any other.
- **Strike probability of the forecast only.** With `--blend`, run `tc-strike-probability --start <init>` so the observed history doesn't count.
- **Real time.** With `--max-gap` matching there is no observed fix at the init, so `--blend adjust` falls back to `append` and says so.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_match.py --tracks ens_tracks.zarr --reference active.zarr \
  --storm IN-FA --blend append -o infa.zarr
```
Keeps each member's IN-FA track, with IN-FA's observed history in front.

### Errors
- Exits 2 when an input is not a track file, the reference has no best tracks, or `--storm` isn't in the reference (the message lists the storms it has).
- Exits 1 when no model track matches. The message gives the closest overlapping distance.
