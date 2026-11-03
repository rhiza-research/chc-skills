# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "xarray",
# ]
# ///
"""Cut a vertical cross-section of gridded fields along a line between two points with MetPy."""

from __future__ import annotations

import sys
import warnings

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims
from weather_skills_core.units import dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

EARTH_RADIUS_KM = 6371.0
U_NAMES = (("eastward_wind",), ("u", "ua", "u_component_of_wind", "u10", "wind_u"))
V_NAMES = (("northward_wind",), ("v", "va", "v_component_of_wind", "v10", "wind_v"))
SP_NAMES = (("surface_air_pressure",), ("sp", "surface_pressure", "pressure_surface", "ps"))


def _point(text: str, flag: str) -> tuple[float, float]:
    try:
        lat, lon = (float(x) for x in text.split(","))
    except ValueError as exc:
        raise UsageError(f"{flag} takes LAT,LON, e.g. {flag} -1.3,36.8") from exc
    if not -90 <= lat <= 90:
        raise UsageError(f"{flag}: latitude {lat} is outside -90..90")
    return lat, lon


def _find(ds, spec):
    standard_names, names = spec
    by_std = [
        n for n, da in ds.data_vars.items() if da.attrs.get("standard_name") in standard_names
    ]
    found = list(dict.fromkeys(by_std or [n for n in names if n in ds.data_vars]))
    return found[0] if len(found) == 1 else None


def _pressure_dim(ds: xr.Dataset) -> str | None:
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


def _distance_km(lat, lon):
    """Great-circle distance from the first point, km."""
    phi, lam = np.radians(lat), np.radians(lon)
    a = (
        np.sin((phi - phi[0]) / 2) ** 2
        + np.cos(phi[0]) * np.cos(phi) * np.sin((lam - lam[0]) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


@weather_skill(name="cross-section-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("spatial"),
    action="append",
    required=True,
    help="Gridded Zarr (lat/lon, usually on pressure levels); repeat to add sp or winds.",
)
@weather_skill.argument("--start", required=True, metavar="LAT,LON", help="Start of the section.")
@weather_skill.argument("--end", required=True, metavar="LAT,LON", help="End of the section.")
@weather_skill.argument(
    "--steps", type=int, default=100, help="Points along the section. Default: 100."
)
@weather_skill.argument(
    "--interp",
    choices=("linear", "nearest"),
    default="linear",
    help="Interpolation from the grid to the section points. Default: linear.",
)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    default=[],
    help="Cut only these variables (repeatable). Default: all gridded variables.",
)
@weather_skill.argument(
    "--wind-components",
    action="store_true",
    help="Add the wind along (tangential) and across (normal) the section from u and v.",
)
def cross_section(ds, start, end, steps, interp, variable, wind_components, **kwargs):
    """Cut a vertical cross-section of gridded fields along a line between two points with MetPy.

    Interpolates every gridded variable to points along the great circle from
    --start to --end (metpy.interpolate.cross_section), keeping levels,
    members and times, adds the distance along the section, and optionally
    the wind along and across it. Levels below ground are blanked when
    surface pressure is given.
    """
    import metpy
    import metpy.calc as mpcalc
    from metpy.interpolate import cross_section as mp_cross_section

    a, b = _point(start, "--start"), _point(end, "--end")
    if steps < 2:
        raise UsageError("--steps must be 2 or more")

    datasets = [dequantify_dataset(d) for d in (ds if isinstance(ds, list) else [ds])]
    try:
        merged = xr.merge(
            datasets, join="inner", compat="no_conflicts", combine_attrs="drop_conflicts"
        )
    except Exception as exc:
        raise UsageError(f"inputs do not combine: {exc}") from exc
    lat_dim, lon_dim = detect_spatial_dims(merged)
    if lat_dim not in merged.dims or lon_dim not in merged.dims:
        raise UsageError(f"needs a lat/lon grid; the input has dims {dict(merged.sizes)}")
    gridded = [n for n, da in merged.data_vars.items() if {lat_dim, lon_dim} <= set(da.dims)]
    missing = [v for v in variable if v not in gridded]
    if missing:
        raise UsageError(
            f"not gridded or not found: {', '.join(missing)}; gridded: {', '.join(gridded)}"
        )
    chosen = list(variable or gridded)

    u_name = _find(merged, U_NAMES)
    v_name = _find(merged, V_NAMES)
    sp_name = _find(merged, SP_NAMES)
    vdim = _pressure_dim(merged)
    if wind_components and not (u_name and v_name):
        raise UsageError("--wind-components needs exactly one u and one v variable on the grid")
    extra = [n for n in (u_name, v_name) if wind_components and n] + (
        [sp_name] if sp_name and vdim else []
    )
    cut = merged[list(dict.fromkeys(chosen + extra))]
    lon_vals = cut[lon_dim].values
    if lon_vals.max() > 180 and min(a[1], b[1]) < 0:
        a, b = (a[0], a[1] % 360), (b[0], b[1] % 360)
    lo, hi = float(cut[lat_dim].min()), float(cut[lat_dim].max())
    for name, (la, lo_) in (("--start", a), ("--end", b)):
        if not lo <= la <= hi or not lon_vals.min() <= lo_ <= lon_vals.max():
            raise UsageError(
                f"{name} {la},{lo_} is outside the grid "
                f"(lat {lo:g}..{hi:g}, lon {lon_vals.min():g}..{lon_vals.max():g}); "
                "fetch a wider bbox or move the point"
            )

    cut = cut.metpy.assign_crs(grid_mapping_name="latitude_longitude", earth_radius=6371229.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            section = mp_cross_section(cut, a, b, steps=steps, interp_type=interp)
        except Exception as exc:
            raise UsageError(f"metpy.interpolate.cross_section failed: {exc}") from exc

    if wind_components:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tangential, normal = mpcalc.cross_section_components(section[u_name], section[v_name])
        units = section[u_name].attrs.get("units", "m s-1")
        section["wind_tangential"] = (
            tangential.metpy.dequantify()
            .drop_vars("metpy_crs", errors="ignore")
            .assign_attrs(
                units=units, long_name=f"wind along the section, positive from {start} to {end}"
            )
        )
        section["wind_normal"] = (
            normal.metpy.dequantify()
            .drop_vars("metpy_crs", errors="ignore")
            .assign_attrs(
                units=units, long_name="wind across the section, positive to the left of travel"
            )
        )
        section = section.drop_vars([n for n in (u_name, v_name) if n not in chosen])
    # The CRS coordinate is MetPy-internal (the components above need it).
    section = section.drop_vars("metpy_crs", errors="ignore")

    if sp_name and vdim:
        p_hpa = ureg.Quantity(section[vdim].values, section[vdim].attrs["units"]).m_as("hPa")
        sp_hpa = ureg.Quantity(section[sp_name].values, section[sp_name].attrs["units"]).m_as("hPa")
        sp_da = xr.DataArray(sp_hpa, dims=section[sp_name].dims, coords=section[sp_name].coords)
        below = xr.DataArray(p_hpa, dims=vdim, coords={vdim: section[vdim]}) > sp_da
        for name in section.data_vars:
            if vdim in section[name].dims:
                section[name] = section[name].where(~below).assign_attrs(section[name].attrs)
        if sp_name not in chosen:
            section = section.drop_vars(sp_name)

    lat = section[lat_dim].values
    lon = section[lon_dim].values
    section = section.assign_coords(
        distance=(
            "index",
            _distance_km(lat, lon),
            {"units": "km", "long_name": f"distance along the section from {start}"},
        )
    )
    for name in section.data_vars:
        section[name].attrs["comment"] = (
            f"MetPy {metpy.__version__} cross_section {start} -> {end}, {steps} points, {interp}"
        )
    section.attrs = dict(merged.attrs)
    section.attrs.setdefault("Conventions", "CF-1.13")
    section.attrs["metpy_version"] = metpy.__version__
    section.attrs["cross_section_start"] = start
    section.attrs["cross_section_end"] = end
    print(
        f"cross-section-metpy: {', '.join(section.data_vars)} from {start} to {end}, "
        f"{float(section['distance'][-1]):.0f} km, {steps} points"
        + (f"; below ground blanked with {sp_name}" if sp_name and vdim else ""),
        file=sys.stderr,
    )
    return section


if __name__ == "__main__":
    cross_section()
