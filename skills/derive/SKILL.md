---
name: derive
description: "Compute a new weather field from existing ones with MetPy (metpy.calc): relative humidity, dewpoint or specific humidity from each other, heat index, apparent temperature, wind chill, wet-bulb and potential temperatures, wind speed/direction or u/v components, vorticity, divergence, deformation, advection, geostrophic wind, frontogenesis, Q-vectors, gradients and smoothing. Works on forecasts (every member, step and level), reanalysis and station data. Use whenever a question needs a quantity the data does not hold directly but MetPy can calculate from it, e.g. 'how humid', 'how hot does it feel', 'where is air converging'."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py *)
metadata:
  catalog-group: transforms
---

# derive

Applies one allow-listed `metpy.calc` function to variables of a weather-skills Zarr and writes the result as a new variable. Every other dim passes through: `number`, `step`, `time`, pressure levels, `point_id`.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i IN.zarr [-i IN2.zarr] -f FUNCTION \
  [--arg PARAM=VALUE ...] [--name NAME] [--keep-inputs] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py --list-functions
```

### Arguments
- `--input`, `-i` — input Zarr (grid, forecast or stations). Repeat to combine variables from several stores; they are merged with an inner join on shared coords.
- `--function`, `-f` — the MetPy function (table below).
- `--arg PARAM=VALUE` — sets one parameter, repeatable. `VALUE` is an input variable or coordinate (`temperature=t2m`, `pressure=level`), a quantity (`pressure=850 hPa`), a number (`n=9`) or `true`/`false`. Parameter names are MetPy's.
- `--name` — output variable name; comma-separated for the two-output functions. Defaults in the table.
- `--keep-inputs` — also write the input variables, so the output can feed the next `derive`.
- `--list-functions` — print every function with its parameters and outputs (tab-separated), then exit. No `-i`/`-o` needed.
- `--output`, `-o` — output Zarr.

### Which function answers which question

| Question | Function | Needs |
|---|---|---|
| How humid is it? | `relative_humidity_from_dewpoint` | t2m, d2m |
| | `relative_humidity_from_specific_humidity` | pressure, t, q |
| Dewpoint / moisture content | `dewpoint_from_relative_humidity`, `dewpoint_from_specific_humidity`, `specific_humidity_from_dewpoint`, `mixing_ratio_from_relative_humidity`, `vapor_pressure` | |
| How hot does it feel? | `heat_index` (hot and humid), `apparent_temperature` (adds wind), `windchill` (cold and windy) | t2m, RH (and wind speed) |
| Heat stress on people | `wet_bulb_temperature` | pressure, t2m, d2m |
| Air-mass and instability ingredients | `potential_temperature`, `equivalent_potential_temperature`, `virtual_temperature`, `moist_static_energy` | |
| How strong / from where does the wind blow? | `wind_speed`, `wind_direction` (`--arg convention=to` for the heading) | u, v |
| u/v from speed and direction (stations) | `wind_components` → `u`, `v` | speed, direction |
| Where is air converging (rain) or diverging aloft? | `divergence` | u, v grid |
| Rotation: cyclones, troughs, vortices | `vorticity`, `absolute_vorticity`, `curvature_vorticity`, `shear_vorticity` | u, v grid |
| Is warm/cold or moist air being carried in? | `advection` (`--arg scalar=VAR`) | field, u, v grid |
| Balanced flow from heights | `geostrophic_wind` → `u_geostrophic`, `v_geostrophic`; `ageostrophic_wind` | gh grid |
| Fronts forming or weakening | `frontogenesis` | theta, u, v grid |
| Forcing for ascent (mid-latitudes) | `q_vector` → `q_vector_x`, `q_vector_y` | u, v, t, pressure |
| Smoother fields for maps | `smooth_gaussian` (`--arg n=…`), `smooth_n_point`, `smooth_rectangular`, `smooth_circular` | any grid field |
| Gradients | `geospatial_gradient`, `geospatial_laplacian` (`--arg f=VAR`) | any grid field |

`--list-functions` gives the full list, including height/pressure conversions, `coriolis_parameter` and `vertical_velocity` (omega to w). Grid functions need `latitude`/`longitude` dims; on stations they exit 2.

### Matching parameters to variables
A parameter without `--arg` is looked up by CF `standard_name` first, then by common names:

| Parameter | Names tried |
|---|---|
| `temperature` | `air_temperature`; t2m, 2m_temperature, tas, t, temperature |
| `dewpoint` | `dew_point_temperature`; d2m, 2m_dewpoint_temperature, tdps, dewpoint |
| `relative_humidity` | `relative_humidity`; r2, rh, r, hurs, hur |
| `specific_humidity` | `specific_humidity`; q, huss, hus |
| `u`, `v` | `eastward_wind` / `northward_wind`; u10/v10, uas/vas, u/v, ua/va |
| `speed` | `wind_speed`; si10, ws, sfcWind |
| `height` | `geopotential_height`; gh, zg |
| `geopotential` | `geopotential`; z |
| `omega` | w (ECMWF's `w` is omega, Pa s-1), omega, wap |
| `pressure` | the pressure-level coordinate of the other inputs; else `surface_air_pressure`; sp, surface_pressure |

Two matches for one parameter (say `t2m` and `t`) exit 2 and ask for `--arg`. When exactly one parameter and one variable are left over, they are paired. The chosen mapping is printed to stderr (`derive: metpy.calc.heat_index(temperature=t2m, relative_humidity=relative_humidity)`) and stored in each output's `comment` with the MetPy version.

### Output
One variable per function output (two for `wind_components`, `geostrophic_wind`, `ageostrophic_wind`, `inertial_advective_wind`, `q_vector`, `geospatial_gradient`), on the input's dims and coords. Other input variables are dropped unless `--keep-inputs`. Dataset attrs come from the inputs.

- Units are MetPy's result units, written in CF form (`degree_Celsius`, `K`, `s-1`, `m s-1`). Relative humidity is written in `%`; specific humidity and mixing ratio are dimensionless (`1`, i.e. kg kg-1). Change them with `unit-convert`.
- `standard_name` is set where a CF name applies (`relative_humidity`, `dew_point_temperature`, `wind_speed`, `atmosphere_relative_vorticity`, `divergence_of_wind`, …).
- Values MetPy masks are NaN (see heat index below).

### Notes
- **Input units come from each variable's `units` attr**, so `t2m` in `degree_Celsius` and `d2m` in `K` combine correctly. A variable without units exits 2.
- **Surface fields need a pressure** for `wet_bulb_temperature`, `equivalent_potential_temperature`, `mixing_ratio_from_relative_humidity` and the like. Use surface pressure (`sp`) when the input has it. A constant is a stand-in: `--arg pressure=1000 hPa` suits the coast, but highland Kenya sits near 800–850 hPa.
- **Heat index is NaN where it is not defined** (cooler than about 27 °C, by MetPy's NOAA definition), so cool cells and highlands come out empty. `--arg mask_undefined=false` writes the air temperature there instead. The same flag applies to `apparent_temperature` and `windchill`.
- **Chaining**: run `derive` twice, giving the first output as a second `-i` (or use `--keep-inputs`): `relative_humidity_from_dewpoint` then `heat_index`; `potential_temperature` then `frontogenesis`.
- Horizontal derivatives use the lat/lon spacing on the Earth's surface. Over a 1.5° grid, vorticity and divergence show synoptic-scale features only.
- Smoothing works over grid points, not kilometres: `smooth_gaussian --arg n=4` is a different amount of smoothing on a 0.25° grid than on a 1.5° one.

### Performance
Point functions are vectorized and take seconds. `wet_bulb_temperature` solves each value separately, about 1.5 ms per value: 101 members × 42 days × 56 cells took ~6 minutes. Clip, select members or steps first when that is too slow. The inputs are loaded into memory whole.

### Errors
- Unknown `--function`, or an `--arg` that is not one of the function's parameters → exit 2, listing the valid ones.
- A required parameter not found, or found twice → exit 2, naming the `--arg` to pass.
- Grid function on station data → exit 2.
- Missing `units` attr, or MetPy rejecting the units or shapes (e.g. a temperature given as `pressure`) → exit 2 with MetPy's message.
- Inputs whose same-named variables disagree → exit 2.

### Examples
```bash
# Relative humidity for every member and day of a Kenya S2S forecast
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i kenya_t2m_d2m.zarr \
  -f relative_humidity_from_dewpoint -o kenya_rh.zarr

# Heat index from that forecast and the humidity just derived
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i kenya_t2m_d2m.zarr -i kenya_rh.zarr \
  -f heat_index -o kenya_heat_index.zarr

# 700 hPa relative vorticity and divergence over East Africa
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i uv700.zarr -f vorticity -o vort700.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i uv700.zarr -f divergence -o div700.zarr

# Potential temperature on every pressure level (pressure comes from the level coordinate)
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i t_levels.zarr -f potential_temperature -o theta.zarr

# Temperature advection at 850 hPa from one store holding t, u and v
uv run ${CLAUDE_SKILL_DIR}/scripts/derive.py -i tuv850.zarr -f advection \
  --arg scalar=t -o t_advection.zarr
```
