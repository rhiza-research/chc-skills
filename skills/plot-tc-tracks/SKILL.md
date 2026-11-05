---
name: plot-tc-tracks
description: "Draw tropical cyclone maps from tc-track: a spaghetti map of every member's track coloured by central pressure, maximum wind or intensity class (TD/TS/Cat 1/Cat 2/Cat 3+); an ECMWF-style strike-probability map from tc-strike-probability with the control and ensemble mean tracks in black; or one track's Hart cyclone phase diagrams (B vs -VTL, -VTL vs -VTU) from tc-phase-space. Use when a task needs a cyclone track map, an ensemble track (spaghetti) plot, a strike probability chart or a phase-space diagram."
license: MIT
compatibility: Requires Python 3.12 and uv. Cartopy downloads Natural Earth coastlines on first use.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py *)
metadata:
  catalog-group: figure
---

# plot-tc-tracks

Renders a track (spaghetti) map, a strike-probability map with the control and ensemble mean tracks, or one track's Hart phase diagrams, as a PNG.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i TRACKS.zarr [--kind map|strike|phase|errors] [--reference BEST.zarr] [--storm ID] [--strike-prob STRIKE.zarr] \
  [--color-by msl|wind|category] [--bbox N/W/S/E] [--track ID] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i`: `tc-track` output. For `--kind phase`, use `tc-phase-space` output.
- `--kind`: `map` (default) draws every member's track, coloured by `--color-by`. `strike` shades the strike probability (needs `--strike-prob`) and draws only the control and the ensemble mean tracks on top: the shading already shows the spread. `phase` draws one track's two phase diagrams. `errors` draws `tc-verify` output (track mode): each member's position error against lead time in grey, with the member mean, the ensemble-mean track, the control and the spread; a second panel has the mean along- and cross-track error.
- `--strike-prob`: `tc-strike-probability` output, for `--kind strike` (refused with `--kind map`). Shaded in ECMWF's strike-probability classes: 5–10 % dark green, 10–20 % green, 20–30 % yellow-green, 30–40 % yellow, 40–50 % light orange, 50–60 % tan, 60–70 % orange, 70–80 % red, 80–90 % dark red, > 90 % violet.
- `--color-by`: colour track segments by central pressure `msl` (default, 940–1010 hPa), by maximum wind `wind` (5–50 m s-1), or by intensity class `category` (tropical depression, tropical storm, Cat 1, Cat 2, Cat 3+, as on ECMWF's TC charts; needs tc-track's `category`). A category segment takes the stronger end's class. Maps wider than about twice their height, or with more than 40 tracks, use horizontal colour bars, thinner lines and no tropical-storm dots.
- `--reference`: `tc-besttrack-fetch` output drawn on the top layer of `map` and `strike`. The official best track is a thick blue line with a white edge and dots coloured by the observed intensity class; the official forecast is dashed blue; a-deck model tracks are thin grey lines (a single model is labelled at its end).
- `--storm`: draw one storm (id or name) from the input and `--reference`; with `--kind errors`, the storm to show.
- `--bbox`: N/W/S/E map extent. Default: the tracks (and `--reference`) plus a 5° margin.
- `--track`: the track id for `--kind phase`. Default: the track with the most phase-space points.
- `--title`: figure title. Default: automatic, giving the number of tracks, the members and the init.
- `--output`, `-o`: PNG path.

### Output
A PNG carrying the provenance chain.

The track map (`--kind map`) draws:
- each track as a line coloured by intensity
- the control (`number` 0) as a solid black line and the ensemble mean as a dashed black line, as on the strike map, drawn on top of the member tracks
- a white circle at each track's first fix
- black dots where `ts_strength` is 1, i.e. tropical-storm wind for that grid

The strike map (`--kind strike`) draws:
- the strike probability in the classes above
- the control's tracks as solid black lines, with a dot at the first fix
- an ensemble mean track per storm as a dashed black line
- both black lines on top of the shading, coastlines and member tracks, with a thin white edge so they stay readable over dark classes

For the ensemble mean, member tracks are grouped into storms. Seeds are taken control first, then longest first. A track from another member joins a seed's storm when they share at least two valid times and are on average less than 500 km apart over those times. The mean position is averaged on the sphere and drawn only at times when at least 20 % of the ensemble (11 of 51 members) have the storm. Storms in fewer members show only as shading.

The phase diagrams:
- **left:** B against −VTL, with the B = 10 m line
- **right:** −VTL against −VTU
- points are coloured by time, the start is marked A and the end Z, and the quadrants are labelled

### Notes
- **Observed history.** Tracks from `tc-match --blend` carry an `observed` segment. Members, the control and the ensemble mean are drawn for the forecast part only; the observed history is drawn once, as the best track, or from `--reference` when given.
- **Dateline.** Tracks crossing the dateline are drawn continuously.
- **Errors.** `--kind strike` without `--strike-prob`, or `--strike-prob` with `--kind map`, exits 2. `--kind phase` exits 2 without B/−VTL/−VTU, for a track id that is not in the input, or for an input with no tracks. A map with no tracks exits 2 unless `--bbox` is given.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i tracks.zarr --color-by category -o tracks_map.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i tracks.zarr --kind strike --strike-prob strike.zarr -o strike_map.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i phase.zarr --kind phase --track m000-001 -o phase.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i matched.zarr --kind strike --strike-prob strike.zarr --reference best.zarr --storm IN-FA -o infa_strike.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_tc_tracks.py -i verify.zarr --kind errors -o errors.png
```
