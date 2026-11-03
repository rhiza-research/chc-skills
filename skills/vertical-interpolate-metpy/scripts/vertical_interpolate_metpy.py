# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "xarray",
# ]
# ///
"""Interpolate pressure-level fields to other pressure levels, heights or isentropes with MetPy."""

from __future__ import annotations

import sys
import warnings

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import STANDARD, dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

HEIGHT = (("geopotential_height",), ("gh", "zg", "geopotential_height"))
GEOPOTENTIAL = (("geopotential",), ("z", "geopotential"))
TEMPERATURE = (("air_temperature",), ("t", "ta", "temperature", "air_temperature"))
SURFACE_PRESSURE = (("surface_air_pressure",), ("sp", "surface_pressure", "pressure_surface", "ps"))


def _pressure_dim(ds: xr.Dataset) -> str | None:
    """The dim whose coordinate is a pressure (hPa, Pa, ...)."""
    for dim in ds.dims:
        coord = ds.coords.get(dim)
        units = coord.attrs.get("units") if coord is not None else None
        if not units:
            continue
        try:
            if ureg.Unit(units).is_compatible_with(ureg.Unit("Pa")):
                return dim
        except Exception:  # noqa: BLE001, S112 — not a unit pint knows
            continue
    return None


def _find(ds, spec, on_levels, vdim, explicit=None):
    """A variable by name, CF standard name or common name; ambiguity is an error."""
    if explicit:
        if explicit not in ds.data_vars:
            raise UsageError(f"{explicit!r} not in the input; variables: {', '.join(ds.data_vars)}")
        return explicit
    standard_names, names = spec

    def fits(n):
        return (vdim in ds[n].dims) == on_levels

    by_std = [
        n
        for n, da in ds.data_vars.items()
        if da.attrs.get("standard_name") in standard_names and fits(n)
    ]
    by_name = [n for n in names if n in ds.data_vars and fits(n)]
    found = list(dict.fromkeys(by_std or by_name))
    if len(found) > 1:
        raise UsageError(f"several candidates ({', '.join(found)}); name one explicitly")
    return found[0] if found else None


def _as(da: xr.DataArray, units: str, mu) -> np.ndarray:
    """``da`` as a float array in ``units`` (MetPy's registry handles offsets)."""
    src = da.attrs.get("units")
    if not src:
        raise UsageError(f"{da.name} has no units attr")
    try:
        return mu.Quantity(np.asarray(da.values, dtype="float64"), src).to(units).magnitude
    except Exception as exc:
        raise UsageError(f"{da.name} units {src!r} do not convert to {units}: {exc}") from exc


def _levels(text: str, flag: str) -> np.ndarray:
    try:
        values = np.array([float(x) for x in text.split(",") if x.strip()])
    except ValueError as exc:
        raise UsageError(f"{flag} takes comma-separated numbers, e.g. 850,700,500") from exc
    if values.size == 0:
        raise UsageError(f"{flag} needs at least one level")
    return values


def _unit_spelling(units: str) -> str:
    unit = ureg.Unit(units)
    standard = {ureg.Unit(spec["units"]): spec["units"] for spec in STANDARD.values()}
    return standard.get(unit) or format(unit, "cf") or "1"


@weather_skill(name="vertical-interpolate-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    required=True,
    help="Zarr with fields on pressure levels; repeat to add surface pressure (sp) from another store.",
)
@weather_skill.argument(
    "--pressure-levels",
    metavar="P1,P2,...",
    help="Target pressure levels in hPa (log-pressure interpolation).",
)
@weather_skill.argument(
    "--height-levels",
    metavar="Z1,Z2,...",
    help="Target heights in metres, above sea level unless --above-ground (needs gh or z).",
)
@weather_skill.argument(
    "--isentropic-levels",
    metavar="TH1,TH2,...",
    help="Target potential temperatures in K (isentropic analysis; needs temperature).",
)
@weather_skill.argument(
    "--above-ground",
    action="store_true",
    help="With --height-levels: heights above the surface (at sp, else the lowest level).",
)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    default=[],
    help="Interpolate only these level variables (repeatable). Default: all of them.",
)
@weather_skill.argument(
    "--height-variable", help="Geopotential height or geopotential variable. Default: gh / z."
)
@weather_skill.argument("--temperature-variable", help="Temperature variable. Default: t / ta.")
def vertical_interpolate(
    ds,
    pressure_levels,
    height_levels,
    isentropic_levels,
    above_ground,
    variable,
    height_variable,
    temperature_variable,
    **kwargs,
):
    """Interpolate pressure-level fields to other pressure levels, heights or isentropes with MetPy.

    Every variable on pressure levels is put on new pressure levels (log-p),
    on heights above sea level or above ground, or on isentropic surfaces
    (potential temperature, with the pressure of each surface), for every
    member, lead and grid point. Levels below ground are dropped first when
    surface pressure is given.
    """
    import metpy
    import metpy.calc as mpcalc
    from metpy.interpolate import interpolate_1d, log_interpolate_1d
    from metpy.units import units as mu

    modes = [m for m in (pressure_levels, height_levels, isentropic_levels) if m]
    if len(modes) != 1:
        raise UsageError(
            "pass exactly one of --pressure-levels, --height-levels, --isentropic-levels"
        )
    if above_ground and not height_levels:
        raise UsageError("--above-ground goes with --height-levels")

    datasets = [dequantify_dataset(d) for d in (ds if isinstance(ds, list) else [ds])]
    try:
        merged = xr.merge(
            datasets, join="inner", compat="no_conflicts", combine_attrs="drop_conflicts"
        )
    except Exception as exc:
        raise UsageError(f"inputs do not combine: {exc}") from exc
    vdim = _pressure_dim(merged)
    if vdim is None:
        raise UsageError("no pressure-level dim found (a dim whose coordinate has pressure units)")
    merged = merged.sortby(vdim, ascending=False)
    p_src = _as(merged[vdim], "hPa", mu)

    level_vars = [n for n, da in merged.data_vars.items() if vdim in da.dims]
    missing = [v for v in variable if v not in level_vars]
    if missing:
        raise UsageError(
            f"not on pressure levels: {', '.join(missing)}; level variables: {', '.join(level_vars)}"
        )
    chosen = variable or level_vars
    if not chosen:
        raise UsageError("no variables on pressure levels to interpolate")

    # Broadcast every level variable to one shape, pressure first.
    template = xr.broadcast(*[merged[n] for n in level_vars])[0]
    other = [d for d in template.dims if d != vdim]
    order = [vdim, *other]

    def stack(name):
        return xr.broadcast(merged[name], template)[0].transpose(*order)

    p_full = np.broadcast_to(
        p_src.reshape(-1, *([1] * len(other))), template.transpose(*order).shape
    )

    # Below ground: NaN out levels under the surface pressure, per column.
    sp_name = _find(merged, SURFACE_PRESSURE, False, vdim)
    below = np.zeros(p_full.shape, dtype=bool)
    sp = None
    if sp_name:
        sp_da = xr.broadcast(merged[sp_name], template.isel({vdim: 0}, drop=True))[0]
        sp = _as(sp_da.transpose(*other), "hPa", mu)
        below = p_full > sp[None, ...]

    def values(name):
        arr = stack(name).values.astype("float64")
        return np.where(below, np.nan, arr)

    if pressure_levels:
        targets = _levels(pressure_levels, "--pressure-levels")
        new_dim, new_attrs, new_units = vdim, dict(merged[vdim].attrs), "hPa"
        new_attrs.update(units="hPa")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out_arrays = log_interpolate_1d(
                targets, np.where(below, np.nan, p_full), *[values(n) for n in chosen], axis=0
            )
        if len(chosen) == 1:
            out_arrays = [out_arrays]
        how = "log-pressure interpolation"
    elif height_levels:
        targets = _levels(height_levels, "--height-levels")
        z_name = _find(merged, HEIGHT, True, vdim, height_variable)
        geo = None if z_name else _find(merged, GEOPOTENTIAL, True, vdim)
        if not (z_name or geo):
            raise UsageError("--height-levels needs geopotential height (gh) or geopotential (z)")
        if z_name:
            z = _as(stack(z_name).assign_attrs(merged[z_name].attrs), "m", mu)
        else:
            phi = _as(stack(geo).assign_attrs(merged[geo].attrs), "m^2/s^2", mu)
            z = mpcalc.geopotential_to_height(phi * mu("m^2/s^2")).m_as("m")
        if above_ground:
            if sp is not None:
                # Surface height: log-p interpolation between the two levels around sp,
                # using the heights before the below-ground levels are blanked.
                lp = np.log(p_src)
                k = np.clip((p_src[:, None] >= sp.reshape(1, -1)).sum(axis=0) - 1, 0, lp.size - 2)
                k = k.reshape(sp.shape)
                z0 = np.take_along_axis(z, k[None, ...], axis=0)[0]
                z1 = np.take_along_axis(z, k[None, ...] + 1, axis=0)[0]
                w = (np.log(sp) - lp[k]) / (lp[k + 1] - lp[k])
                z_sfc = z0 + w * (z1 - z0)
            else:
                z_sfc = np.nanmin(z, axis=0)
            z = z - z_sfc[None, ...]
        z = np.where(below, np.nan, z)
        new_dim = "height" if above_ground else "altitude"
        new_attrs = {
            "units": "m",
            "standard_name": new_dim,
            "long_name": "height above ground" if above_ground else "height above sea level",
            "axis": "Z",
            "positive": "up",
        }
        new_units = "m"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out_arrays = interpolate_1d(targets, z, *[values(n) for n in chosen], axis=0)
        if len(chosen) == 1:
            out_arrays = [out_arrays]
        how = "linear interpolation in height"
    else:
        targets = _levels(isentropic_levels, "--isentropic-levels")
        t_name = _find(merged, TEMPERATURE, True, vdim, temperature_variable)
        if t_name is None:
            raise UsageError("--isentropic-levels needs temperature on pressure levels")
        t_k = _as(stack(t_name).assign_attrs(merged[t_name].attrs), "K", mu)
        others = [n for n in chosen if n != t_name]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # MetPy's isentropic solver breaks on NaN in a column, so it runs on the full
            # columns; surfaces that sit below ground are blanked afterwards.
            res = mpcalc.isentropic_interpolation(
                targets * mu.K,
                p_src * mu.hPa,
                t_k * mu.K,
                *[stack(n).values.astype("float64") for n in others],
                vertical_dim=0,
                temperature_out=True,
            )
        p_isen, t_isen, *rest = res
        underground = (
            p_isen.m_as("hPa") > sp[None, ...] if sp is not None else np.zeros(p_isen.shape, bool)
        )
        p_isen = np.where(underground, np.nan, p_isen.m_as("hPa")) * mu.hPa
        t_isen = np.where(underground, np.nan, t_isen.m_as("K")) * mu.K
        out_arrays = [
            np.where(underground, np.nan, np.asarray(r.magnitude if hasattr(r, "magnitude") else r))
            for r in rest
        ]
        chosen = others
        new_dim = "isentropic_level"
        new_attrs = {
            "units": "K",
            "standard_name": "air_potential_temperature",
            "long_name": "isentropic level",
            "axis": "Z",
            "positive": "up",
        }
        new_units = "K"
        how = "isentropic interpolation"

    coords = {k: v for k, v in template.coords.items() if vdim not in v.dims}
    coords[new_dim] = xr.DataArray(targets, dims=new_dim, attrs=new_attrs)
    out = xr.Dataset(coords=coords)
    dims = [new_dim, *other]
    for name, arr in zip(chosen, out_arrays, strict=True):
        out[name] = xr.DataArray(np.asarray(arr), dims=dims).assign_attrs(merged[name].attrs)
    if isentropic_levels:
        out["pressure"] = xr.DataArray(p_isen.m_as("hPa"), dims=dims).assign_attrs(
            units="hPa",
            standard_name="air_pressure",
            long_name="pressure of the isentropic surface",
        )
        out["temperature"] = xr.DataArray(t_isen.m_as("K"), dims=dims).assign_attrs(
            units="K",
            standard_name="air_temperature",
            long_name="temperature on the isentropic surface",
        )
    for name in out.data_vars:
        out[name].attrs["comment"] = f"MetPy {metpy.__version__} {how} from {vdim}"
        if "units" in out[name].attrs:
            out[name].attrs["units"] = _unit_spelling(out[name].attrs["units"])
    # The input's dim order, with the new vertical dim where the pressure dim was.
    out = out.transpose(*[new_dim if d == vdim else d for d in template.dims])
    # Pass through fields that aren't on levels (sp, t2m, ...), for chaining.
    for name, da in merged.data_vars.items():
        if vdim not in da.dims and name not in out:
            out[name] = da
    out.attrs = dict(merged.attrs)
    out.attrs.setdefault("Conventions", "CF-1.13")
    out.attrs["metpy_version"] = metpy.__version__
    print(
        f"vertical-interpolate-metpy: {how} of {', '.join(chosen)} to {new_dim} "
        f"{', '.join(f'{x:g}' for x in targets)} {new_units}"
        + (f"; below-ground levels removed with {sp_name}" if sp_name else ""),
        file=sys.stderr,
    )
    return out


if __name__ == "__main__":
    vertical_interpolate()
