# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "xarray",
# ]
# ///
"""Derive a new field from existing ones with a MetPy calculation (metpy.calc)."""

from __future__ import annotations

import sys
import warnings
from typing import NamedTuple

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims, names_for
from weather_skills_core.units import STANDARD, dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy makes MetPy's constants and the inputs
# land in different pint registries ("Cannot operate with Quantity and Quantity
# of different registries").


class Calc(NamedTuple):
    """One allow-listed metpy.calc function.

    ``kind`` is ``point`` (value by value: grids, forecasts and stations) or
    ``grid`` (horizontal derivatives or smoothing over a lat/lon grid).
    ``outputs`` are the default output variable names; ``{param}`` is replaced
    by the variable passed for that parameter. ``standard_names`` holds CF
    standard names for outputs that have one, ``percent`` marks a
    dimensionless relative humidity written in percent.
    """

    kind: str
    required: tuple[str, ...]
    outputs: tuple[str, ...]
    optional: tuple[str, ...] = ()
    long_names: tuple[str, ...] = ()
    standard_names: tuple[str | None, ...] = ()
    percent: bool = False


_RH = {"outputs": ("relative_humidity",), "standard_names": ("relative_humidity",), "percent": True}

CALCS: dict[str, Calc] = {
    # Thermodynamics
    "potential_temperature": Calc(
        "point",
        ("pressure", "temperature"),
        ("theta",),
        standard_names=("air_potential_temperature",),
    ),
    "temperature_from_potential_temperature": Calc(
        "point",
        ("pressure", "potential_temperature"),
        ("temperature",),
        standard_names=("air_temperature",),
    ),
    "equivalent_potential_temperature": Calc(
        "point",
        ("pressure", "temperature", "dewpoint"),
        ("theta_e",),
        standard_names=("equivalent_potential_temperature",),
    ),
    "saturation_equivalent_potential_temperature": Calc(
        "point", ("pressure", "temperature"), ("theta_es",)
    ),
    "virtual_temperature": Calc(
        "point",
        ("temperature", "mixing_ratio"),
        ("virtual_temperature",),
        standard_names=("virtual_temperature",),
    ),
    "virtual_temperature_from_dewpoint": Calc(
        "point",
        ("pressure", "temperature", "dewpoint"),
        ("virtual_temperature",),
        standard_names=("virtual_temperature",),
    ),
    "virtual_potential_temperature": Calc(
        "point", ("pressure", "temperature", "mixing_ratio"), ("theta_v",)
    ),
    "wet_bulb_temperature": Calc(
        "point",
        ("pressure", "temperature", "dewpoint"),
        ("wet_bulb_temperature",),
        standard_names=("wet_bulb_temperature",),
    ),
    "wet_bulb_potential_temperature": Calc(
        "point",
        ("pressure", "temperature", "dewpoint"),
        ("theta_w",),
        standard_names=("wet_bulb_potential_temperature",),
    ),
    "density": Calc(
        "point",
        ("pressure", "temperature", "mixing_ratio"),
        ("air_density",),
        standard_names=("air_density",),
    ),
    "exner_function": Calc("point", ("pressure",), ("exner_function",)),
    "dry_static_energy": Calc("point", ("height", "temperature"), ("dry_static_energy",)),
    "moist_static_energy": Calc(
        "point", ("height", "temperature", "specific_humidity"), ("moist_static_energy",)
    ),
    # Moisture
    "dewpoint": Calc(
        "point", ("vapor_pressure",), ("dewpoint",), standard_names=("dew_point_temperature",)
    ),
    "dewpoint_from_relative_humidity": Calc(
        "point",
        ("temperature", "relative_humidity"),
        ("dewpoint",),
        standard_names=("dew_point_temperature",),
    ),
    "dewpoint_from_specific_humidity": Calc(
        "point",
        ("pressure", "specific_humidity"),
        ("dewpoint",),
        standard_names=("dew_point_temperature",),
    ),
    "relative_humidity_from_dewpoint": Calc("point", ("temperature", "dewpoint"), **_RH),
    "relative_humidity_from_specific_humidity": Calc(
        "point", ("pressure", "temperature", "specific_humidity"), **_RH
    ),
    "relative_humidity_from_mixing_ratio": Calc(
        "point", ("pressure", "temperature", "mixing_ratio"), **_RH
    ),
    "specific_humidity_from_dewpoint": Calc(
        "point",
        ("pressure", "dewpoint"),
        ("specific_humidity",),
        standard_names=("specific_humidity",),
    ),
    "specific_humidity_from_mixing_ratio": Calc(
        "point", ("mixing_ratio",), ("specific_humidity",), standard_names=("specific_humidity",)
    ),
    "mixing_ratio_from_relative_humidity": Calc(
        "point",
        ("pressure", "temperature", "relative_humidity"),
        ("mixing_ratio",),
        standard_names=("humidity_mixing_ratio",),
    ),
    "mixing_ratio_from_specific_humidity": Calc(
        "point",
        ("specific_humidity",),
        ("mixing_ratio",),
        standard_names=("humidity_mixing_ratio",),
    ),
    "saturation_mixing_ratio": Calc(
        "point", ("total_press", "temperature"), ("saturation_mixing_ratio",)
    ),
    "saturation_vapor_pressure": Calc("point", ("temperature",), ("saturation_vapor_pressure",)),
    "vapor_pressure": Calc(
        "point",
        ("pressure", "mixing_ratio"),
        ("vapor_pressure",),
        standard_names=("water_vapor_partial_pressure_in_air",),
    ),
    # Apparent temperature
    "heat_index": Calc(
        "point",
        ("temperature", "relative_humidity"),
        ("heat_index",),
        optional=("mask_undefined",),
    ),
    "apparent_temperature": Calc(
        "point",
        ("temperature", "relative_humidity", "speed"),
        ("apparent_temperature",),
        optional=("face_level_winds", "mask_undefined"),
    ),
    "windchill": Calc(
        "point",
        ("temperature", "speed"),
        ("wind_chill",),
        optional=("face_level_winds", "mask_undefined"),
    ),
    # Wind
    "wind_speed": Calc("point", ("u", "v"), ("wind_speed",), standard_names=("wind_speed",)),
    "wind_direction": Calc(
        "point",
        ("u", "v"),
        ("wind_direction",),
        optional=("convention",),
        long_names=("wind direction (direction the wind blows from, unless --arg convention=to)",),
    ),
    "wind_components": Calc(
        "point",
        ("speed", "wind_direction"),
        ("u", "v"),
        standard_names=("eastward_wind", "northward_wind"),
    ),
    # Heights, pressure, vertical motion
    "geopotential_to_height": Calc("point", ("geopotential",), ("height",)),
    "height_to_geopotential": Calc(
        "point", ("height",), ("geopotential",), standard_names=("geopotential",)
    ),
    "pressure_to_height_std": Calc("point", ("pressure",), ("height_std",)),
    "height_to_pressure_std": Calc("point", ("height",), ("pressure_std",)),
    "coriolis_parameter": Calc(
        "point", ("latitude",), ("coriolis_parameter",), standard_names=("coriolis_parameter",)
    ),
    "vertical_velocity": Calc(
        "point",
        ("omega", "pressure", "temperature"),
        ("w",),
        optional=("mixing_ratio",),
        standard_names=("upward_air_velocity",),
    ),
    "vertical_velocity_pressure": Calc(
        "point",
        ("w", "pressure", "temperature"),
        ("omega",),
        optional=("mixing_ratio",),
        standard_names=("lagrangian_tendency_of_air_pressure",),
    ),
    # Horizontal kinematics on a lat/lon grid
    "vorticity": Calc(
        "grid", ("u", "v"), ("vorticity",), standard_names=("atmosphere_relative_vorticity",)
    ),
    "absolute_vorticity": Calc("grid", ("u", "v"), ("absolute_vorticity",)),
    "divergence": Calc("grid", ("u", "v"), ("divergence",), standard_names=("divergence_of_wind",)),
    "shearing_deformation": Calc("grid", ("u", "v"), ("shearing_deformation",)),
    "stretching_deformation": Calc("grid", ("u", "v"), ("stretching_deformation",)),
    "total_deformation": Calc("grid", ("u", "v"), ("total_deformation",)),
    "curvature_vorticity": Calc("grid", ("u", "v"), ("curvature_vorticity",)),
    "shear_vorticity": Calc("grid", ("u", "v"), ("shear_vorticity",)),
    "advection": Calc("grid", ("scalar", "u", "v"), ("{scalar}_advection",)),
    "geostrophic_wind": Calc("grid", ("height",), ("u_geostrophic", "v_geostrophic")),
    "ageostrophic_wind": Calc("grid", ("height", "u", "v"), ("u_ageostrophic", "v_ageostrophic")),
    "inertial_advective_wind": Calc(
        "grid",
        ("u", "v", "u_geostrophic", "v_geostrophic"),
        ("u_inertial_advective", "v_inertial_advective"),
    ),
    "frontogenesis": Calc("grid", ("potential_temperature", "u", "v"), ("frontogenesis",)),
    "q_vector": Calc(
        "grid",
        ("u", "v", "temperature", "pressure"),
        ("q_vector_x", "q_vector_y"),
        optional=("static_stability",),
    ),
    "potential_vorticity_barotropic": Calc(
        "grid", ("height", "u", "v"), ("potential_vorticity_barotropic",)
    ),
    "geospatial_gradient": Calc("grid", ("f",), ("{f}_gradient_x", "{f}_gradient_y")),
    "geospatial_laplacian": Calc("grid", ("f",), ("{f}_laplacian",)),
    # Smoothing (over the lat/lon grid points)
    "smooth_gaussian": Calc("grid", ("scalar_grid", "n"), ("{scalar_grid}_smoothed",)),
    "smooth_n_point": Calc(
        "grid", ("scalar_grid",), ("{scalar_grid}_smoothed",), optional=("n", "passes")
    ),
    "smooth_rectangular": Calc(
        "grid", ("scalar_grid", "size"), ("{scalar_grid}_smoothed",), optional=("passes",)
    ),
    "smooth_circular": Calc(
        "grid", ("scalar_grid", "radius"), ("{scalar_grid}_smoothed",), optional=("passes",)
    ),
}

# Parameters filled from the input when --arg leaves them out: CF standard
# names first, then variable names. "w" (geometric vertical velocity) is
# matched by standard name only: ECMWF's "w" is omega in Pa s-1.
_PRESSURE = (
    ("surface_air_pressure", "air_pressure"),
    ("sp", "surface_pressure", "pressure_surface", "pres", "ps"),
)
ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "temperature": (
        ("air_temperature",),
        ("t2m", "2m_temperature", "temperature_2m", "tas", "t", "temperature", "air_temperature"),
    ),
    "dewpoint": (
        ("dew_point_temperature",),
        ("d2m", "2m_dewpoint_temperature", "dewpoint_temperature_2m", "tdps", "dewpoint", "td"),
    ),
    "relative_humidity": (
        ("relative_humidity",),
        ("r2", "rh", "r", "hurs", "hur", "relative_humidity", "relative_humidity_2m"),
    ),
    "specific_humidity": (
        ("specific_humidity",),
        ("q", "huss", "hus", "specific_humidity", "specific_humidity_2m"),
    ),
    "mixing_ratio": (("humidity_mixing_ratio",), ("mixing_ratio",)),
    "u": (
        ("eastward_wind",),
        (
            "u10",
            "10m_u_component_of_wind",
            "wind_u_10m",
            "uas",
            "u",
            "ua",
            "u_component_of_wind",
            "wind_u",
        ),
    ),
    "v": (
        ("northward_wind",),
        (
            "v10",
            "10m_v_component_of_wind",
            "wind_v_10m",
            "vas",
            "v",
            "va",
            "v_component_of_wind",
            "wind_v",
        ),
    ),
    "speed": (
        ("wind_speed",),
        ("wind_speed", "si10", "ws", "sfcWind", "wind_speed_10m", "10m_wind_speed"),
    ),
    "wind_direction": (("wind_from_direction",), ("wind_direction", "wdir")),
    "height": (("geopotential_height",), ("gh", "zg", "geopotential_height")),
    "geopotential": (("geopotential",), ("z", "geopotential")),
    "omega": (("lagrangian_tendency_of_air_pressure",), ("w", "omega", "wap")),
    "w": (("upward_air_velocity",), ()),
    "potential_temperature": (
        ("air_potential_temperature",),
        ("theta", "potential_temperature"),
    ),
    "u_geostrophic": ((), ("u_geostrophic",)),
    "v_geostrophic": ((), ("v_geostrophic",)),
    "pressure": _PRESSURE,
    "total_press": _PRESSURE,
}
_PRESSURE_PARAMS = ("pressure", "total_press")


def _list_functions() -> str:
    """Plain-text catalog for --list-functions."""
    lines = ["function\tkind\trequired\toptional\toutputs"]
    for name, calc in CALCS.items():
        lines.append(
            "\t".join(
                (
                    name,
                    calc.kind,
                    ",".join(calc.required),
                    ",".join(calc.optional) or "-",
                    ",".join(calc.outputs),
                )
            )
        )
    return "\n".join(lines)


def _parse_arg(text: str) -> tuple[str, str]:
    """``PARAM=VALUE`` from one --arg."""
    param, sep, value = text.partition("=")
    if not sep or not param.strip() or not value.strip():
        raise UsageError(f"--arg {text!r} must be PARAM=VALUE, e.g. --arg temperature=t2m")
    return param.strip(), value.strip()


def _constant(param: str, value: str, mu):
    """A literal --arg value: a bool, a plain number, or a quantity like ``850 hPa``."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        number = float(value)
    except ValueError:
        pass
    else:
        return int(number) if number.is_integer() and "." not in value else number
    try:
        quantity = mu.Quantity(value)
    except Exception:  # noqa: BLE001 — not a quantity either; a plain string option
        return value
    if quantity.dimensionless and quantity.units == mu.dimensionless:
        return value
    return quantity


def _pressure_coord(arrays: list[xr.DataArray]) -> xr.DataArray | None:
    """The pressure-valued vertical coordinate of the resolved inputs, if any."""
    for da in arrays:
        for name in names_for("vertical"):
            if name not in da.dims:
                continue
            coord = da[name]
            units = coord.attrs.get("units")
            if not units:
                raise UsageError(
                    f"vertical coordinate {name!r} has no units attr; pass the pressure "
                    f"explicitly, e.g. --arg pressure={name} after adding units, or "
                    "--arg pressure='850 hPa'"
                )
            try:
                if ureg.Unit(units).is_compatible_with(ureg.Unit("Pa")):
                    return coord.reset_coords(drop=True)
            except Exception:  # noqa: BLE001, S112 — not a pressure axis
                continue
    return None


def _match(ds: xr.Dataset, param: str, used: set[str]) -> str | None:
    """The input variable for ``param`` from ALIASES; ambiguity is an error."""
    standard_names, names = ALIASES.get(param, ((), ()))
    by_std = [
        n
        for n, da in ds.data_vars.items()
        if n not in used and da.attrs.get("standard_name") in standard_names
    ]
    by_name = [n for n in names if n in ds.data_vars and n not in used]
    candidates = list(dict.fromkeys(by_std or by_name))
    if len(candidates) > 1:
        raise UsageError(
            f"several variables could be {param!r}: {', '.join(candidates)}; "
            f"choose one with --arg {param}=VAR"
        )
    return candidates[0] if candidates else None


def _resolve(ds: xr.Dataset, calc: Calc, given: dict[str, str], mu):
    """Map every parameter to an input DataArray or a constant.

    Returns ``(values, sources)``: values by parameter, and a printable source
    for each (variable name, coordinate name or literal).
    """
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    known = (*calc.required, *calc.optional)
    for param, value in given.items():
        if param not in known:
            raise UsageError(
                f"--arg {param}: not a parameter of this function; "
                f"required: {', '.join(calc.required)}"
                + (f"; optional: {', '.join(calc.optional)}" if calc.optional else "")
            )
        if value in ds.variables:
            values[param] = ds[value]
        else:
            values[param] = _constant(param, value, mu)
        sources[param] = value

    used = {v for v in sources.values() if v in ds.data_vars}
    missing = [p for p in calc.required if p not in values]
    # Pressure last: a variable on pressure levels takes its own level coordinate.
    for param in sorted(missing, key=lambda p: p in _PRESSURE_PARAMS):
        if param == "latitude":
            lat_dim, _ = detect_spatial_dims(ds)
            values[param], sources[param] = ds[lat_dim], f"{lat_dim} (coordinate)"
            continue
        if param in _PRESSURE_PARAMS:
            arrays = [v for v in values.values() if isinstance(v, xr.DataArray)]
            coord = _pressure_coord(arrays)
            if coord is not None:
                values[param], sources[param] = coord, f"{coord.name} (coordinate)"
                continue
        name = _match(ds, param, used)
        if name is not None:
            values[param], sources[param] = ds[name], name
            used.add(name)

    unresolved = [p for p in calc.required if p not in values]
    spare = [n for n in ds.data_vars if n not in used]
    if len(unresolved) == 1 and len(spare) == 1 and unresolved[0] not in _PRESSURE_PARAMS:
        values[unresolved[0]], sources[unresolved[0]] = ds[spare[0]], spare[0]
        unresolved = []
    if unresolved:
        hint = " ".join(f"--arg {p}=VAR" for p in unresolved)
        if any(p in _PRESSURE_PARAMS for p in unresolved):
            hint += " (pressure may also be a constant, e.g. --arg pressure='1000 hPa')"
        raise UsageError(
            f"could not find {', '.join(unresolved)} in the input "
            f"(variables: {', '.join(ds.data_vars) or 'none'}); pass {hint}"
        )
    return values, sources


def _check_units(values: dict[str, object], sources: dict[str, str]) -> None:
    """Every DataArray argument needs a units attr for MetPy."""
    for param, value in values.items():
        if isinstance(value, xr.DataArray) and not value.attrs.get("units"):
            raise UsageError(
                f"{sources[param]} (for {param}) has no units attr; MetPy needs units "
                "(set them upstream, or convert with unit-convert)"
            )


def _to_output(result, name: str, calc: Calc, index: int, provenance: str) -> xr.DataArray:
    """MetPy's pint DataArray -> plain array with a CF units attr, NaN where masked."""
    if not isinstance(result, xr.DataArray):
        raise UsageError(
            f"MetPy returned {type(result).__name__}, not a field; give at least one "
            "parameter as an input variable"
        )
    quantity = result.metpy.unit_array
    if calc.percent:
        quantity = quantity.to("percent")
    magnitude = quantity.magnitude
    if np.ma.isMaskedArray(magnitude):
        magnitude = np.ma.filled(magnitude.astype("float64"), np.nan)
    # Same spelling as core's writer: the standard units as spelled there
    # (degree_Celsius, not °C), anything else in CF form.
    unit = ureg.Unit(str(quantity.units))
    standard = {ureg.Unit(spec["units"]): spec["units"] for spec in STANDARD.values()}
    units = standard.get(unit) or format(unit, "cf") or "1"
    drop = [c for c in result.coords if c == "metpy_crs"]
    out = xr.DataArray(
        np.asarray(magnitude), dims=result.dims, coords=result.drop_vars(drop).coords, name=name
    )
    out.attrs["units"] = units
    long_name = calc.long_names[index] if index < len(calc.long_names) else None
    out.attrs["long_name"] = long_name or name.replace("_", " ")
    standard = calc.standard_names[index] if index < len(calc.standard_names) else None
    if standard:
        out.attrs["standard_name"] = standard
    out.attrs["comment"] = provenance
    return out


@weather_skill(name="derive", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    required=True,
    help="Input Zarr; repeat to combine variables from several stores (inner join on coords).",
)
@weather_skill.argument(
    "--function",
    "-f",
    choices=sorted(CALCS),
    required=True,
    metavar="FUNCTION",
    help="metpy.calc function to apply; see --list-functions.",
)
@weather_skill.argument(
    "--arg",
    action="append",
    default=[],
    metavar="PARAM=VALUE",
    help=(
        "Set one MetPy parameter: an input variable or coordinate (temperature=t2m), "
        "a quantity (pressure='850 hPa'), a number (n=9) or true/false. Repeatable. "
        "Parameters left out are matched from CF standard names and common variable names."
    ),
)
@weather_skill.argument(
    "--name",
    help="Output variable name(s), comma-separated for functions with two outputs.",
)
@weather_skill.argument(
    "--keep-inputs",
    action="store_true",
    help="Also write the input variables, so the output can feed the next derive.",
)
@weather_skill.argument(
    "--list-functions",
    action="store_true",
    probe=True,
    help="Print the supported functions, their parameters and outputs, then exit.",
)
def derive(ds, function, arg, name, keep_inputs, list_functions, **kwargs):
    """Derive a new field from existing ones with a MetPy calculation (metpy.calc).

    Applies one allow-listed metpy.calc function (humidity conversions, heat
    index, potential temperatures, wind speed and direction, vorticity,
    divergence, advection, frontogenesis, smoothing, ...) to variables of a
    weather-skills Zarr, keeping every other dim (members, steps, levels,
    stations). Inputs it isn't told about are matched from CF standard names
    and common variable names (t2m, d2m, u10, q, ...).
    """
    if list_functions:
        print(_list_functions())
        return None

    import metpy
    import metpy.calc as mpcalc
    from metpy.units import units as mu

    calc = CALCS[function]
    datasets = [dequantify_dataset(item) for item in (ds if isinstance(ds, list) else [ds])]
    try:
        merged = xr.merge(
            datasets, join="inner", compat="no_conflicts", combine_attrs="drop_conflicts"
        )
    except Exception as exc:
        raise UsageError(f"inputs do not combine: {exc}") from exc

    given = dict(_parse_arg(a) for a in arg)
    values, sources = _resolve(merged, calc, given, mu)
    _check_units(values, sources)

    if calc.kind == "grid":
        try:
            lat_dim, lon_dim = detect_spatial_dims(merged)
        except UsageError:
            lat_dim = lon_dim = None
        # Stations carry latitude/longitude as coordinates along point_id, not as dims.
        if lat_dim not in merged.dims or lon_dim not in merged.dims:
            raise UsageError(
                f"{function} needs a lat/lon grid; the input has dims {dict(merged.sizes)}"
            )
        for param, value in values.items():
            if isinstance(value, xr.DataArray) and {lat_dim, lon_dim} <= set(value.dims):
                values[param] = value.transpose(..., lat_dim, lon_dim)

    for param, value in values.items():
        if isinstance(value, xr.DataArray):
            values[param] = value.load()
    mapping = ", ".join(
        f"{p}={sources[p]}" for p in (*calc.required, *calc.optional) if p in sources
    )
    print(f"derive: metpy.calc.{function}({mapping})", file=sys.stderr)

    func = getattr(mpcalc, function)
    positional = [values[p] for p in calc.required]
    optional = {p: values[p] for p in calc.optional if p in values}
    try:
        with warnings.catch_warnings():
            # Horizontal-only calls (no w, no dz) never use the vertical dim, yet
            # MetPy warns when it cannot find one and guesses (number/step).
            warnings.filterwarnings("ignore", message="Vertical dimension .* not found")
            result = func(*positional, **optional)
    except Exception as exc:
        raise UsageError(f"metpy.calc.{function}({mapping}) failed: {exc}") from exc

    results = result if isinstance(result, tuple) else (result,)
    template = {p: sources[p].split(" ")[0] for p in sources}
    defaults = [o.format(**template) for o in calc.outputs]
    names = [n.strip() for n in name.split(",")] if name else defaults
    if len(names) != len(results):
        raise UsageError(
            f"{function} writes {len(results)} variable(s); --name got {len(names)} "
            f"({', '.join(names)})"
        )

    provenance = f"metpy.calc.{function}({mapping}), MetPy {metpy.__version__}"
    fields = {
        n: _to_output(r, n, calc, i, provenance)
        for i, (n, r) in enumerate(zip(names, results, strict=True))
    }
    out = merged.copy() if keep_inputs else xr.Dataset(coords=merged.coords)
    clash = [n for n in fields if n in out.data_vars]
    if clash:
        raise UsageError(f"output name(s) {', '.join(clash)} already in the input; pass --name")
    for n, field in fields.items():
        out[n] = field
    out.attrs = dict(merged.attrs)
    out.attrs.setdefault("Conventions", "CF-1.13")
    return out


if __name__ == "__main__":
    derive()
