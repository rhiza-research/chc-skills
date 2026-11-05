---
name: tc-phase-space
description: "Add Hart (2003) cyclone phase space parameters to tc-track tracks: thermal asymmetry B and the lower and upper thermal winds -VTL and -VTU, from geopotential on 900 to 300 hPa. These tell tropical (symmetric warm-core) storms from extratropical (asymmetric cold-core) ones and show extratropical transition or subtropical/hybrid structure; --tropical-only drops the non-tropical lows and cuts tracks at extratropical transition. Use after tc-track when a task asks whether a storm is tropical, transitioning or frontal, or to remove extratropical detections; plot the result with plot-tc-tracks --kind phase."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/tc_phase_space.py *)
metadata:
  catalog-group: transforms
---

# tc-phase-space

Computes Hart's cyclone phase space at every track point.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_phase_space.py --tracks TRACKS.zarr -i GEOPOTENTIAL.zarr [-i MORE.zarr] \
  [--no-smooth] [--tropical-only [--min-tropical-hours 24] [--keep-transition]] -o PHASE.zarr
```

### Arguments
- `--tracks`: `tc-track` output.
- `--input`, `-i`: geopotential or geopotential height on pressure levels, from the same source as the tracks (same members and init). It may be one variable on a level dim (`gh`, `zg`, `z`, `geopotential`, …) or per-level variables (`z900`, `gh_600`, …). Units may be m or m2 s-2, and levels hPa or Pa. Repeat `-i` to combine stores.
- `--no-smooth`: leave out Hart's 24 h running mean.
- `--tropical-only`: keep only each track's tropical spell (see `tropical` below): from its first tropical point to extratropical transition, the first stretch of more than 12 h that is not tropical. Shorter non-tropical gaps stay in the spell. The first spell that is tropical for at least `--min-tropical-hours` (default 24) is kept; tracks without one are dropped. This removes extratropical and frontal lows and everything after extratropical transition, including a transitioned low that later turns warm and symmetric again (a warm seclusion).
- `--keep-transition`: with `--tropical-only`, keep each track after its tropical spell ends instead of cutting it. Lows that were never tropical are still dropped, which is what removes the false positives; real storms keep their extratropical transition, labelled `extratropical` in `phase`.
- `--output`, `-o`: output Zarr.

### Output
The tracks with five variables added on `(track, obs)`:

| Variable | Meaning | Reading |
|---|---|---|
| `B` | right-minus-left 600−900 hPa thickness across the storm's motion, within 500 km, times +1 north / −1 south | > 10 m: frontal (asymmetric); < 10 m: non-frontal |
| `neg_vtl` | −VTL: slope of ΔZ = max(Z) − min(Z) within 500 km against ln p, over 900−600 hPa | > 0: lower warm core; < 0: cold core |
| `neg_vtu` | −VTU: the same slope over 600−300 hPa | > 0: upper warm core |
| `tropical` | 1 where B is below the threshold and −VTL > 0 (Hart's symmetric warm core), 0 elsewhere, NaN where either is missing | used by `--tropical-only` |
| `phase` | `pre-genesis` (before the tropical spell), `tropical` (the spell), `extratropical` (after it), `non-tropical` (a track never tropical long enough) | drawn by `plot-tc-tracks`; `tc-strike-probability` counts only `tropical` by default |

`B`, `neg_vtl` and `neg_vtu` are in m.

A tropical cyclone sits at B ≈ 0 with both −VTL and −VTU positive. Extratropical transition moves it to B > 10, then to −VTL < 0. Attrs record the radius, the levels used, the lower layer, the B threshold, the smoothing and the reference.

### Notes
- **Levels.** Hart uses 900, 600 and 300 hPa and the levels between them (925, 575 and so on are accepted within 25 hPa). More levels give better slopes; ERA5 has all of Hart's 50 hPa levels.
- **Lower-layer mode.** Without a level near 600 hPa, the lower layer runs up to the nearest level between 450 and 700 hPa, and the output says it is approximate (`phase_space_lower_layer_approximate=1`). dynamical's IFS-ENS, with 925, 850 and 500 hPa, gets a 925−500 hPa layer. The B threshold scales with the layer's depth in ln p: 10 m × ln(925/500) / ln(900/600) ≈ 15.2 m. Without 300 hPa, −VTU is NaN. The `tropical` flag needs only B and −VTL, so it works in this mode.
- **Matching.** Track points are matched to the geopotential by member, init and exact valid time. Points with no matching time stay NaN, with a note on stderr.
- **Motion.** The direction of motion, used for B, comes from the neighbouring track points (one-sided at the ends). A single-point track has B = NaN.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_phase_space.py --tracks idai_tracks.zarr -i era5_geopotential.zarr -o idai_phase.zarr
```
Adds B, −VTL and −VTU to each ERA5 track, smoothed over 24 h.

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/tc_phase_space.py --tracks ens_tracks.zarr -i ifs_ens_gh.zarr --tropical-only -o ens_tropical.zarr
```
Keeps only the tropical part of each IFS-ENS track, from 925/850/500 hPa heights.

## Details
The method follows Hart (2003), MWR 131:585. ΔZ is taken over all grid cells within 500 km (great-circle distance) of the centre. The thermal winds are least-squares slopes of ΔZ against ln p over the levels in each layer. The 24 h running mean is centred and averages points within ±12 h, ignoring NaN. With `--tropical-only`, a spell's tropical time is its number of tropical points times the track's time step.

### Errors
- Exits 1 when there is no level near 900 hPa or between 450 and 700 hPa. The message lists the levels the input has.
- Exits 2 when `--tracks` is not tc-track output, when there is no geopotential, or when the geopotential has members or inits that the tracks lack.
