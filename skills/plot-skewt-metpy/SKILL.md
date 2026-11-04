---
name: plot-skewt-metpy
description: "Draw a Skew-T log-p diagram with MetPy for one place and time from a pressure-level forecast, reanalysis or balloon sounding: temperature and dewpoint (every ensemble member, mean bold), the surface parcel with CAPE and CIN shaded, the LCL, wind barbs, dry/moist adiabats and mixing lines, a hodograph, and a box with CAPE, CIN, LCL, lifted index and precipitable water. Overlays several labelled profiles from different sources on one diagram (--profile), e.g. a balloon launch against a model forecast. Use when someone wants to see the vertical profile or sounding at a location, e.g. 'show the forecast sounding for Nairobi tomorrow afternoon' or 'compare the observed sounding with the model'."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py *)
metadata:
  catalog-group: figure
---

# plot-skewt-metpy

Picks one profile (one grid cell or station, one time) from a pressure-level Zarr and draws it on MetPy's Skew-T, with a hodograph when winds and heights are there. With `--profile`, several profiles from different inputs are overlaid.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i LEVELS.zarr [-i SURFACE.zarr] \
  (--lat LAT --lon LON | --station ID) [--isel DIM=INDEX ...] [--var ROLE=NAME ...] \
  [options] -o OUT.png

# several profiles (balloon vs model, two models, two times)
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py \
  --profile 'label=NAME;input=A.zarr[,B.zarr];station=ID;isel=time=5' \
  --profile 'label=NAME;input=C.zarr;lat=LAT;lon=LON;isel=step=1' [options] -o OUT.png
```

### Arguments
- `--input`, `-i` — Zarr with temperature and a moisture field (dewpoint, `q` or `r`) on pressure levels. Repeat to add surface fields (`sp`, `t2m`, `d2m`) or winds (`u`, `v`) and heights (`gh` or `z`) from another store. Same inputs and matching as `sounding-indices-metpy`.
- `--lat`, `--lon` — the grid cell nearest this point. Required for gridded input.
- `--station` — the station id (`point_id` or `station_id`) to plot, for station profiles.
- `--isel DIM=INDEX` — pick one position along any other dim with several values, repeatable: `--isel step=2`, `--isel time=0`. Every dim except pressure and ensemble members must end up with one value.
- `--var ROLE=NAME` — use `NAME` for a role when matching fails or is ambiguous (roles as in `sounding-indices-metpy`).
- `--no-hodograph` — leave out the hodograph. The figure then becomes square and the index box moves inside the diagram.
- `--title` — figure title. Default: the point and the init/valid time (each profile's label and time with `--profile`).
- `--output`, `-o` — output PNG.

**Several profiles** (instead of `-i`, `--lat/--lon/--station/--isel/--var`):
- `--profile SPEC` — repeatable. `SPEC` is `;`-separated `KEY=VALUE`: `label`, `input` (comma list of Zarrs merged for this profile), `station` or `lat` + `lon`, `isel` (comma list `DIM=INDEX`), `var` (comma list `ROLE=NAME`). Quote it in the shell.
- `--profiles-file FILE.json` — the same as a JSON list of objects; `input` may be a list, `isel` and `var` an object (`{"time": 5}`). Use it when labels or paths make quoting awkward.
- `--shade first|all|none` — which profiles get CAPE/CIN shading (default `first`).
- `--barbs first|all|none` — which profiles get wind barbs (default `first`); with `all`, one column each, in the profile's colour.

**Styling** (the defaults draw exactly as before):
- `--shade-alpha A`, `--cape-color C`, `--cin-color C` — make weak CAPE/CIN visible (e.g. `--shade-alpha 0.6`).
- `--pressure-range BOTTOM/TOP` (hPa) and `--temperature-range LEFT/RIGHT` (°C at the bottom of the frame) — zoom, e.g. `960/550` and `-15/10`. With only a pressure range, the temperature axis fits the profiles inside it.
- `--inset on|auto|off` — a small second Skew-T of the levels up to 550 hPa; `auto` adds it when the first profile's SBCAPE is between 0 and 100 J/kg (default `off`).
- `--fiducial-style faint|bold` — reference lines faint (default) or bold.
- `--fiducial-labels` — label dry adiabats (θ, K), moist adiabats (θw, °C) and mixing ratio lines (g/kg), with legend entries.
- `--mixing-lines-top HPA` — draw mixing ratio lines up to this pressure (MetPy stops at 600 hPa).
- `--freezing-isotherm` — highlight 0 °C.
- `--figsize W,H` (inches) and `--dpi N` (default 120).

### What is drawn
- Temperature (red) and dewpoint (green). With an ensemble dim (`number`), each member is a thin line and the ensemble mean is bold; the parcel, shading, LCL and index box use the mean profile.
- The surface parcel (dashed), CAPE shaded red and CIN blue, the LCL marked.
- Wind barbs in knots on the right (ensemble mean), from `u` and `v` on levels.
- Hodograph of the 0–10 km wind (knots), coloured by height above ground, when heights are present.
- Box: SBCAPE, SBCIN, LCL, lifted index and precipitable water of the plotted profile.
- With several profiles: profile 1 solid, then dashed, dotted, dash-dot (T red, Td green); each profile has its own colour (black, blue, purple, orange, ...) for its parcel, LCL, barbs, hodograph trace and a tick at its surface pressure on the left edge. The box gets a column per profile, numbered with a key when labels are long. stderr lists each profile's matched variables and indices.

### Notes
- **Over high ground add `sp` (and `t2m`, `d2m`)**: levels below ground are then hidden and the parcel starts at the real surface (Nairobi ~830 hPa). Without them the lowest levels are drawn as if they were air.
- Wind barbs and the hodograph use only levels above ground; heights are taken above the lowest of them.
- For one member pass `--isel number=N`.
- **Comparing sources: use `--profile`, not stacking.** Each profile is matched, masked below its own surface and converted (dewpoint from `q` or `r`) on its own levels, so a balloon (surface 957 hPa) and a model (surface 874 hPa, 13 levels) need no common grid. Stacking them along `number` instead gives a mean, parcel and indices of air that does not exist.
- A profile with an ensemble dim keeps thin members and a bold mean, in that profile's line style.
- CF bounds variables (`time_bounds(time, nv)` from `sounding-fetch`) are ignored; `--isel time=N` is enough.
- The numbers in the box match `sounding-indices-metpy` for the same profile; use that skill for maps or ensemble statistics of the indices.
- A small "MetPy x.y" note sits bottom-left; core's provenance mark sits bottom-right.

### Errors
- Gridded input without `--lat`/`--lon` → exit 2.
- Neither or both of `-i` and `--profile`/`--profiles-file`, or `--lat/--lon/--station/--isel/--var` next to `--profile` → exit 2.
- Another dim still has several values (e.g. `step`) → exit 2 with the `--isel` to add.
- No pressure dim, temperature or moisture on levels → exit 2.
- Fewer than 3 levels with temperature and dewpoint at the point → exit 2.

### Examples
```bash
# ERA5 sounding for Nairobi, 15 April 2026 12 UTC (time index 1), with surface fields
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --lat -1.29 --lon 36.82 --isel time=1 -o skewt_nairobi.png

# Ensemble forecast sounding at Lodwar, lead index 3, every member drawn
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py -i fc_levels.zarr -i fc_surface.zarr \
  --lat 3.12 --lon 35.6 --isel step=3 -o skewt_lodwar.png

# Comparing a balloon with a model: Innsbruck 1 Nov 2026 03 UTC vs ECMWF HRES 00 UTC +3 h,
# weak CAPE made visible
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_skewt_metpy.py \
  --profile 'label=Balloon 11120;input=innsbruck_soundings.zarr;station=AUM00011120;isel=time=5' \
  --profile 'label=ECMWF HRES +3 h;input=hres_innsbruck.zarr;lat=47.26;lon=11.34;isel=step=1' \
  --shade-alpha 0.6 --pressure-range 960/550 --fiducial-style bold --fiducial-labels \
  --mixing-lines-top 300 --freezing-isotherm -o skewt_innsbruck_compare.png
```
