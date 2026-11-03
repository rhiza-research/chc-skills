---
name: vertical-interpolate-metpy
description: "Put pressure-level fields on other vertical levels with MetPy: new pressure levels (log-pressure, e.g. 775 hPa, or a common level set for two models), heights above sea level or above ground (from geopotential height), or isentropic surfaces (potential temperature, with the pressure of each surface). Works on every member, lead and grid point, and blanks levels below ground when surface pressure is given. Use to compare models on common levels, to get a field 1 km above ground, or for isentropic analysis."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/vertical_interpolate_metpy.py *)
metadata:
  catalog-group: transforms
---

# vertical-interpolate-metpy

Interpolates every variable on pressure levels to a new vertical coordinate, with MetPy's interpolation routines.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/vertical_interpolate_metpy.py -i LEVELS.zarr [-i SURFACE.zarr] \
  (--pressure-levels P1,P2,... | --height-levels Z1,Z2,... [--above-ground] | --isentropic-levels TH1,TH2,...) \
  [-v VAR ...] [--height-variable NAME] [--temperature-variable NAME] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — Zarr with fields on pressure levels (a dim whose coordinate has pressure units). Repeat to add surface pressure (`sp`) from another store.
- `--pressure-levels` — target pressures in hPa. Log-pressure interpolation (`metpy.interpolate.log_interpolate_1d`).
- `--height-levels` — target heights in metres, above sea level. Needs geopotential height (`gh`) or geopotential (`z`). Linear in height (`interpolate_1d`).
- `--above-ground` — with `--height-levels`: heights above the surface. The surface height is the geopotential height interpolated to `sp` (log-p), or the lowest level's height without `sp`.
- `--isentropic-levels` — target potential temperatures in K (`metpy.calc.isentropic_interpolation`). Needs temperature on levels.
- `--variable`, `-v` — interpolate only these level variables (repeatable). Default: every variable on pressure levels.
- `--height-variable`, `--temperature-variable` — name the height or temperature variable when the default match (`gh`/`z`, `t`/`ta`) fails.
- `--output`, `-o` — output Zarr.

### Output
The pressure dim is replaced, in the same position:

| Mode | New dim | Units | standard_name |
|---|---|---|---|
| `--pressure-levels` | same name as the input's (e.g. `level`) | hPa | as input |
| `--height-levels` | `altitude` | m | `altitude` (above sea level) |
| `--height-levels --above-ground` | `height` | m | `height` (above ground) |
| `--isentropic-levels` | `isentropic_level` | K | `air_potential_temperature` |

Isentropic output also has `pressure` (hPa, the pressure of each surface) and `temperature` (K) on the isentropes. Variables keep their names and units. Fields without a pressure dim (`sp`, `t2m`, …) pass through unchanged. Dataset attrs gain `metpy_version`.

### Notes
- **Below ground.** With `sp`, levels below the surface are dropped first, so targets under the ground come out NaN (875 hPa over highland Kenya, 1500 m above sea level on the plateau, isentropes colder than the surface air). Without `sp`, model values extrapolated below ground are used as if they were air.
- **Between ground and the first level.** Surface (2 m) values are not used, so above-ground heights below the first level above ground are NaN: at Nairobi (~830 hPa) the next level is 775 or 700 hPa, so 500 m above ground is often empty. Use `t2m`/`d2m` directly for near-surface values.
- Targets outside the input's range are NaN; nothing is extrapolated.
- Isentropic analysis suits stable mid-latitude air (frontal zones, the 2 PVU tropopause with PV). In the tropics isentropes are nearly flat and lie close together, and low isentropes are often under the surface.
- Coarse level sets (S2S's ~10 levels) give rougher interpolation than ERA5's 37.

### Errors
- None or more than one of the three modes → exit 2.
- `--above-ground` without `--height-levels` → exit 2.
- No pressure dim; no `gh`/`z` for heights; no temperature for isentropes → exit 2.
- `-v` names a variable that is not on pressure levels → exit 2, listing the level variables.

### Examples
```bash
# ERA5 or forecast fields on 775 and 650 hPa, below-ground cells blank
uv run ${CLAUDE_SKILL_DIR}/scripts/vertical_interpolate_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --pressure-levels 775,650 -o era5_775_650.zarr

# Winds 1 km and 3 km above ground
uv run ${CLAUDE_SKILL_DIR}/scripts/vertical_interpolate_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --height-levels 1000,3000 --above-ground -v u -v v -o winds_agl.zarr

# Isentropic analysis: pressure, humidity and winds on 310-340 K
uv run ${CLAUDE_SKILL_DIR}/scripts/vertical_interpolate_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --isentropic-levels 310,320,330,340 -o era5_isentropic.zarr
```
