---
name: tc-strike-probability
description: "Map the probability that a tropical cyclone passes within a radius (120 km by default, as ECMWF) of each place, from ensemble tc-track tracks: the percentage of members with a storm centre that close during a time window. Use when a task asks how likely a cyclone is to affect a coast, country or city, or for an ECMWF-style strike probability map; plot it with plot-tc-tracks --kind strike or plot."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_strike_probability.py *)
metadata:
  catalog-group: transforms
---

# tc-strike-probability

Turns an ensemble of tracks into a gridded strike probability.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_strike_probability.py -i TRACKS.zarr (--bbox N/W/S/E [--resolution DEG] | --like GRID.zarr) \
  [--radius KM] [--start TIME] [--end TIME] -o STRIKE.zarr
```

### Arguments
- `--input`, `-i`: `tc-track` output holding one forecast (one init).
- `--bbox`: N/W/S/E of the output grid. A west greater than east crosses the dateline. Exactly one of `--bbox` and `--like` is required.
- `--resolution`: degrees between grid points with `--bbox`. Default: `0.5`.
- `--like`: a Zarr whose latitude/longitude grid the output uses.
- `--radius`: strike radius in km. Default: `120`, the radius of ECMWF's strike-probability product. Larger radii (e.g. 300) map where the storm's wider wind and rain field may reach.
- `--start`, `--end`: count only track points from or up to these times (`YYYY-MM-DD` or `YYYY-MM-DDTHH`, inclusive). Default: the whole track. For ECMWF's product (within 120 km in the next 240 h), pass the init and the init plus 10 days.
- `--output`, `-o`: output Zarr.

### Output
A gridded Zarr with `strike_probability(latitude, longitude)` in `%`. Attrs: `strike_radius_km`, `strike_members` (the denominator), `strike_window`, and the tracks' `forecast_reference_time`.

### Notes
- **Denominator.** The denominator is every member of the forecast, taken from the tracks' `ensemble_members` attr, so members with no storm count as misses. Tracks without members (a reanalysis) give 100% along the track and 0% elsewhere.
- **Densifying.** Tracks are densified along great circles at radius/4 steps before counting, so a fast storm or a daily time step does not skip cells.
- **One init.** Tracks with several inits exit 2; select one forecast first.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_strike_probability.py -i tracks.zarr --bbox -5/30/-30/60 --resolution 0.25 \
  --start 2026-02-01T00 --end 2026-02-06T00 -o strike.zarr
```
Maps the chance of a storm passing within 120 km in the first 5 days over the SW Indian Ocean and Mozambique.

## Details
A grid point is struck by a member when any densified point of any of that member's tracks, inside the window, is within `--radius` km (great-circle distance). The probability is 100 × the struck members / all members.

### Errors
- Exits 2 when the input is not tc-track output, when both or neither of `--bbox` and `--like` is given, when `--radius` or `--resolution` is not positive, when there are several inits, or when a time cannot be read.
