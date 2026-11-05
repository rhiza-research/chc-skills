---
name: tc-swath
description: "Rainfall or wind brought by a tropical cyclone: a gridded field (forecast precipitation or 10 m wind, observed rain) counted only within a radius of each member's storm centre from tc-match or tc-besttrack-fetch. Gives the storm-total rain or maximum wind per member, or, with --per-step, the storm-only field on its time axis for the indicator skill (e.g. the chance of 100 mm in 3 days). Use when a task asks how much rain or wind a named storm will bring or brought."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_swath.py *)
metadata:
  catalog-group: transforms
---

# tc-swath

Counts a field only where a storm is. This turns an ensemble precipitation forecast into "the rain this cyclone brings", member by member.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_swath.py --tracks MATCHED.zarr -i FIELD.zarr \
  [--field rain|wind] [--variable NAME] [--radius KM] [--storm ID|NAME] [--per-step] -o SWATH.zarr
```

### Arguments
- `--tracks`:
  - `tc-match` output (one track per member, with `number`) for an ensemble field;
  - `tc-besttrack-fetch` output for observations (CHIRPS, IMERG, ERA5);
  - only `best_track` tracks are used from a best-track file.
- `--input`, `-i`: the gridded field. Use the same model and members as the tracks for a forecast (`number`, `step`), or a `time`-indexed reanalysis or observation store.
- `--field`:
  - `rain` (default): precipitation rates, which `dynamical-fetch`, `ecmwf-fetch`, `chirps-fetch` and `imerg-fetch` all write. Converted to mm day-1.
  - `wind`: 10 m wind speed, or a u/v pair.
- `--variable`: the field's variable name. By default it is found: `tp`, `precipitation_surface`, `pr`, `total_precipitation`, …; `si10`, `wind_speed_10m`, `sfcWind`, `u10`/`v10`, `wind_u_10m`/`wind_v_10m`, ….
- `--radius`: km around the centre. Default 500 for rain, the radius most TC rainfall studies use (e.g. Lonfat et al. 2004), and 300 for wind.
- `--storm`: use one storm's tracks (id or name).
- `--per-step`: keep the field's time axis (see Output).
- `--output`, `-o`: output Zarr.

### Output
- **Default.** On `(number, latitude, longitude)` (no `number` for observations):
  - `tc_rain` (mm): the storm total;
  - or `tc_wind_max` (m s-1): the highest wind inside the radius over the storm's life;
  - `storm_hours`: the storm time counted per member.
  
  A member without the storm has 0 everywhere, which keeps ensemble probabilities honest. Continue with:
  - `exceedance-probability --dim number --threshold 100 --comparison ge`: P(storm total ≥ 100 mm);
  - `summarize-dim` for the ensemble mean;
  - `plot` or `clip-region`.
- **`--per-step`.** On the field's own dims (`number, step, latitude, longitude`, or `time, latitude, longitude`):
  - `tc_rain` (mm day-1) or `tc_wind` (m s-1), the field inside the radius and 0 outside.
  
  The `indicator` skill applies windowed rules to it:
  - `--rule "tc_rain sum 3d >= 100" --detect any --probability`: P(the storm brings 100 mm within some 3 days);
  - `--rule "tc_wind count-above 17.5 1d >= 1" --detect any --probability`: P(tropical-storm-force wind at any time).

Attrs record the field, variable, radius, storms and time convention.

### Notes
- **Time convention.** A forecast value (`step`) covers the interval ending at its valid time. A `time`-indexed value covers the interval starting at its time, as for CHIRPS and IMERG daily data. Each interval counts when the storm centre, interpolated to the interval's middle, is within the radius. Wind is instantaneous, so it uses the centre at the valid time.
- **Storm time only.** Before genesis and after the track ends nothing is counted. Run `tc-match --blend append` with observations to include the observed history.
- **Observed rain for verification.** Run the same command with `chirps-fetch` or `imerg-fetch` rates and the best track, then compare with `difference` or `plot-compare`. For US landfalls, tropycal's WPC rain gauge data (`tropycal.rain`) is another reference.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_swath.py --tracks chido_matched.zarr -i ifs_ens_precip.zarr -o chido_rain.zarr
```

### Errors
- Exits 2 when:
  - the tracks or field is the wrong kind;
  - the field has members but the tracks have no `number`;
  - the rain field isn't a rate;
  - `--storm` isn't in the tracks.
- Exits 1 when no storm centre falls within the field's times.
