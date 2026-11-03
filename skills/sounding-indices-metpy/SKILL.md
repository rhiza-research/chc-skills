---
name: sounding-indices-metpy
description: "Compute convection and severe-weather indices from vertical profiles with MetPy: CAPE and CIN (surface-based, mixed-layer, most-unstable), LCL, LFC, EL, lifted, Showalter, K, total totals and Galvez-Davison indices, precipitable water, 0-6 km bulk shear and storm-relative helicity. Works on every member, lead and grid point of a pressure-level forecast or reanalysis, or on balloon soundings. Use for thunderstorm, instability and severe-storm questions ('will storms form', 'how unstable', 'is there shear for organised storms')."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_indices_metpy.py *)
metadata:
  catalog-group: transforms
---

# sounding-indices-metpy

Runs MetPy's sounding functions on every vertical profile in a Zarr and writes one variable per index, with the pressure dim removed and every other dim kept.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_indices_metpy.py -i LEVELS.zarr [-i SURFACE.zarr] \
  [--index INDEX ...] [--var ROLE=NAME ...] [--workers N] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — Zarr with temperature and a moisture field on pressure levels. Repeat to add surface fields (`sp`, `t2m`, `d2m`) or winds and heights from another store; inputs are merged with an inner join.
- `--index` — index to compute, repeatable, or `all`. Default: `sbcape lifted_index lcl k_index total_totals_index precipitable_water`.
- `--var ROLE=NAME` — use variable `NAME` for a role when the automatic match fails or is ambiguous. Roles: `temperature`, `dewpoint`, `specific_humidity`, `relative_humidity`, `u`, `v`, `height`, `geopotential`, `surface_pressure`, `surface_temperature`, `surface_dewpoint`.
- `--workers` — processes for the per-profile indices. Default: 1.
- `--output`, `-o` — output Zarr.

### Indices

| `--index` | Output variables | Units | Computed |
|---|---|---|---|
| `sbcape` | `sbcape`, `sbcin` | J kg-1 | per profile |
| `mlcape` | `mlcape`, `mlcin` (lowest 100 hPa mixed) | J kg-1 | per profile |
| `mucape` | `mucape`, `mucin` (most unstable parcel in the lowest 300 hPa) | J kg-1 | per profile |
| `lfc`, `el` | `lfc_pressure`, `el_pressure` (surface parcel) | hPa | per profile |
| `lifted_index` | `lifted_index` (surface parcel at 500 hPa) | K | per profile |
| `showalter_index` | `showalter_index` (850 hPa parcel) | K | per profile |
| `bulk_shear` | `bulk_shear_0_6km` | m s-1 | per profile, needs winds |
| `srh` | `srh_0_1km`, `srh_0_3km` (Bunkers right mover) | m2 s-2 | per profile, needs winds |
| `lcl` | `lcl_pressure`, `lcl_temperature` | hPa, degree_Celsius | whole field |
| `k_index` | `k_index` | degree_Celsius | whole field |
| `total_totals_index`, `cross_totals`, `vertical_totals` | same names | K | whole field |
| `galvez_davison_index` | `galvez_davison_index` (made for tropical convection) | 1 | whole field |
| `precipitable_water` | `precipitable_water` | mm | whole field |

Rough guide: CAPE above ~1000 J kg-1, lifted index below −3 K, K-index above ~30 °C or Galvez-Davison above ~25 point to deep convection; 0–6 km shear above ~20 m s-1 supports organised and supercell storms; 0–1 km helicity above ~100 m2 s-2 favours tornadoes in supercells. Tropical air is often unstable without severe weather, so read these against local climatology.

### Inputs and matching
- **Pressure dim**: the dim whose coordinate has pressure units (hPa or Pa), e.g. `level` from ARCO-ERA5 or `vertical` from dynamical-fetch. Levels are sorted surface first.
- **Temperature**: `air_temperature` standard name, or `t` / `ta` / `temperature`, on pressure levels.
- **Moisture**: dewpoint, specific humidity (`q`, `hus`) or relative humidity (`r`, `hur`) on pressure levels; MetPy converts it to dewpoint.
- **Surface** (optional, strongly advised over high ground): surface pressure (`sp`) removes the levels below ground in each column; with `t2m` and `d2m` as well, the surface parcel starts at the real surface. Without `sp`, every level counts as above ground and stderr says so.
- **Winds** for `bulk_shear` and `srh`: `u`, `v` and `gh` (or geopotential `z`) on pressure levels. Heights are taken above the surface (at `sp` when given).
- The chosen variables are printed to stderr and recorded in each output's `comment`.

### Output
Dims are the input's minus the pressure dim (`number`, `step`, `time`, `latitude`, `longitude`, `point_id`, …). Dataset attrs come from the inputs, plus `metpy_version`.

### Notes
- The whole-field indices (K, totals, Galvez-Davison) read fixed levels (850, 700, 500 hPa) and use them even where 850 hPa is below ground; over highland Kenya prefer CAPE, lifted index and Galvez-Davison (which uses `sp`).
- Showalter needs an 850 hPa parcel, so it is NaN where 850 hPa is below ground. Any index MetPy cannot compute for a profile is NaN there, with a per-index count on stderr; the other indices of that profile are kept.
- Coarse level sets (S2S's ~10 levels) give rougher CAPE than ERA5's 37. Profiles with fewer than 3 levels above ground are NaN.
- `dynamical-fetch` products carry only a few named levels (850/925 hPa) and no humidity on levels, which is not enough for a profile; ERA5 (ARCO, or CDS with an area subset) and the ECMWF S2S pressure-level fields are.

### Performance
Whole-field indices take seconds. Per-profile indices loop in Python: roughly 0.1 s per profile for the default set and 0.5 s for `all`, divided over `--workers`. 3,182 ERA5 profiles took 48 s (default) and 3.4 min (`all`) with `--workers 8`. A full 101-member × 46-day S2S field over Kenya is millions of profiles: select members, leads or an area first, or compute from the ensemble mean profile (a different quantity from the mean of each member's CAPE).

### Errors
- No dim with pressure units, no temperature, or no moisture on levels → exit 2 naming what is missing.
- `bulk_shear` / `srh` without `u`, `v` and `gh`/`z` on levels → exit 2.
- Two candidates for one role, or a `--var` name not in the input → exit 2; pass `--var`.
- A variable without units, or units that don't convert → exit 2.

### Examples
```bash
# Instability for every member and lead of a pressure-level forecast, with surface fields
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_indices_metpy.py -i fc_levels.zarr -i fc_surface.zarr \
  --workers 8 -o fc_instability.zarr

# Severe-weather ingredients from ERA5 (levels with t, q, u, v, z, plus sp, t2m, d2m)
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_indices_metpy.py -i era5_pl.zarr -i era5_sfc.zarr \
  --index mucape --index bulk_shear --index srh --workers 8 -o era5_severe.zarr
```
