# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "matplotlib>=3.8",
#   "numpy",
#   "xarray",
# ]
# ///
"""Draw a Skew-T log-p diagram (and hodograph) of a vertical profile with MetPy."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims, names_for
from weather_skills_core.units import dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

# Same roles and names as sounding-indices-metpy, so both take the same inputs.
ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    "temperature": (("air_temperature",), ("t", "ta", "temperature", "air_temperature"), True),
    "dewpoint": (("dew_point_temperature",), ("dewpoint", "td", "dpt"), True),
    "specific_humidity": (("specific_humidity",), ("q", "hus", "specific_humidity"), True),
    "relative_humidity": (("relative_humidity",), ("r", "hur", "rh", "relative_humidity"), True),
    "u": (("eastward_wind",), ("u", "ua", "u_component_of_wind"), True),
    "v": (("northward_wind",), ("v", "va", "v_component_of_wind"), True),
    "height": (("geopotential_height",), ("gh", "zg", "geopotential_height"), True),
    "geopotential": (("geopotential",), ("z", "geopotential"), True),
    "surface_pressure": (
        ("surface_air_pressure",),
        ("sp", "surface_pressure", "pressure_surface", "ps"),
        False,
    ),
    "surface_temperature": (
        ("air_temperature",),
        ("t2m", "2m_temperature", "temperature_2m", "tas"),
        False,
    ),
    "surface_dewpoint": (
        ("dew_point_temperature",),
        ("d2m", "2m_dewpoint_temperature", "dewpoint_temperature_2m", "tdps"),
        False,
    ),
}


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


def _match(ds: xr.Dataset, role: str, vdim: str, given: dict[str, str]) -> str | None:
    """The input variable for ``role``; ambiguity is an error."""
    if role in given:
        return given[role]
    standard_names, names, on_levels = ROLES[role]

    def fits(name):
        return (vdim in ds[name].dims) == on_levels

    by_std = [
        n
        for n, da in ds.data_vars.items()
        if da.attrs.get("standard_name") in standard_names and fits(n)
    ]
    by_name = [n for n in names if n in ds.data_vars and fits(n)]
    found = list(dict.fromkeys(by_std or by_name))
    if len(found) > 1:
        raise UsageError(
            f"several variables could be {role}: {', '.join(found)}; choose with --var {role}=NAME"
        )
    return found[0] if found else None


def _as(da: xr.DataArray, units: str, mu) -> np.ndarray:
    """``da`` as a float array in ``units`` (MetPy's registry handles offsets)."""
    src = da.attrs.get("units")
    if not src:
        raise UsageError(f"{da.name} has no units attr; MetPy needs units")
    try:
        return mu.Quantity(np.asarray(da.values, dtype="float64"), src).to(units).magnitude
    except Exception as exc:
        raise UsageError(f"{da.name} units {src!r} do not convert to {units}: {exc}") from exc


def _select(ds: xr.Dataset, lat, lon, station, isel: list[str], member_dim, vdim):
    """Reduce ``ds`` to one profile (plus the member dim)."""
    picked = []
    if station is not None:
        if "point_id" not in ds.dims:
            raise UsageError("--station needs station data with a point_id dim")
        if station not in ds["point_id"].values:
            raise UsageError(f"--station {station!r} not in the input")
        ds = ds.sel(point_id=station)
        picked.append(f"station {station}")
    else:
        try:
            lat_dim, lon_dim = detect_spatial_dims(ds)
        except UsageError:
            lat_dim = lon_dim = None
        if lat_dim in ds.dims and lon_dim in ds.dims:
            if lat is None or lon is None:
                raise UsageError("pass --lat and --lon to pick the grid cell to plot")
            ds = ds.sel({lat_dim: lat, lon_dim: lon}, method="nearest")
            la, lo = float(ds[lat_dim]), float(ds[lon_dim])
            picked.append(
                f"{abs(la):.2f}°{'S' if la < 0 else 'N'}, {abs(lo):.2f}°{'W' if lo < 0 else 'E'}"
            )
    for item in isel:
        dim, sep, index = item.partition("=")
        if not sep or dim not in ds.dims:
            raise UsageError(f"--isel {item!r}: DIM must be one of {', '.join(map(str, ds.dims))}")
        try:
            ds = ds.isel({dim: int(index)})
        except (ValueError, IndexError) as exc:
            raise UsageError(f"--isel {item!r}: {exc}") from exc
    left = [d for d in ds.dims if d not in (vdim, member_dim) and ds.sizes[d] > 1]
    if left:
        raise UsageError(
            "more than one profile left along "
            + ", ".join(f"{d} ({ds.sizes[d]})" for d in left)
            + "; pick one with --isel, e.g. --isel "
            + f"{left[0]}=0"
        )
    ds = ds.squeeze([d for d in ds.dims if d not in (vdim, member_dim)], drop=False)
    return ds, picked


def _when(ds: xr.Dataset) -> str:
    """Init / valid time label from scalar coords."""
    parts = []
    t = ds.coords.get("time")
    step = ds.coords.get("step")
    if t is not None and t.ndim == 0 and np.issubdtype(t.dtype, np.datetime64):
        stamp = np.datetime_as_string(t.values, unit="h").replace("T", " ") + " UTC"
        if step is not None and step.ndim == 0 and np.issubdtype(step.dtype, np.timedelta64):
            valid = t.values + step.values
            hours = int(step.values / np.timedelta64(1, "h"))
            parts.append(
                f"init {stamp}, valid "
                + np.datetime_as_string(valid, unit="h").replace("T", " ")
                + f" UTC (+{hours} h)"
            )
        else:
            parts.append(stamp)
    return "; ".join(parts)


@weather_skill(name="plot-skewt-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    required=True,
    help=(
        "Zarr with temperature and moisture on pressure levels; repeat to add surface "
        "fields (sp, t2m, d2m) or winds and heights."
    ),
)
@weather_skill.argument("--lat", type=float, help="Latitude of the grid cell to plot (nearest).")
@weather_skill.argument("--lon", type=float, help="Longitude of the grid cell to plot (nearest).")
@weather_skill.argument("--station", help="point_id of the station profile to plot.")
@weather_skill.argument(
    "--isel",
    action="append",
    default=[],
    metavar="DIM=INDEX",
    help="Pick one position along a dim (time, step, ...), repeatable.",
)
@weather_skill.argument(
    "--var",
    action="append",
    default=[],
    metavar="ROLE=NAME",
    help=f"Use variable NAME for ROLE ({', '.join(ROLES)}).",
)
@weather_skill.argument(
    "--no-hodograph", action="store_true", help="Leave out the hodograph inset."
)
@weather_skill.argument("--title", default=None, help="Figure title (default: place and time).")
def plot_skewt(ds, lat, lon, station, isel, var, no_hodograph, title, output, **kwargs):
    """Draw a Skew-T log-p diagram (and hodograph) of a vertical profile with MetPy.

    Temperature and dewpoint (every ensemble member thin, the mean bold), the
    surface parcel with CAPE and CIN shaded, the LCL, wind barbs, dry and moist
    adiabats and mixing lines, plus a hodograph and a box with CAPE, CIN, LCL,
    lifted index and precipitable water, for one grid cell or station of a
    pressure-level forecast, reanalysis or sounding Zarr.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import metpy
    import metpy.calc as mpcalc
    from metpy.plots import Hodograph, SkewT
    from metpy.units import units as mu

    given = {}
    for item in var:
        role, sep, name = item.partition("=")
        if not sep or role not in ROLES or not name:
            raise UsageError(
                f"--var {item!r} must be ROLE=NAME with ROLE one of {', '.join(ROLES)}"
            )
        given[role] = name

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
    member_dim = next((d for d in names_for("member") if d in merged.dims), None)
    merged = merged.sortby(vdim, ascending=False)
    prof, picked = _select(merged, lat, lon, station, isel, member_dim, vdim)

    t_name = _match(prof, "temperature", vdim, given)
    moist_role = next(
        (
            r
            for r in ("dewpoint", "specific_humidity", "relative_humidity")
            if _match(prof, r, vdim, given)
        ),
        None,
    )
    if t_name is None or moist_role is None:
        raise UsageError(
            "need temperature and a moisture field (dewpoint, q or r) on pressure levels; "
            "pass --var when the names differ"
        )
    moist_name = _match(prof, moist_role, vdim, given)
    order = [member_dim, vdim] if member_dim else [vdim]

    def levels(name):
        return xr.broadcast(prof[name], prof[t_name])[0].transpose(*order)

    p = _as(prof[vdim], "hPa", mu)
    t = _as(levels(t_name), "degC", mu)
    raw = levels(moist_name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if moist_role == "dewpoint":
            td = _as(raw, "degC", mu)
        elif moist_role == "specific_humidity":
            td = mpcalc.dewpoint_from_specific_humidity(
                np.broadcast_to(p, raw.shape) * mu.hPa, _as(raw, "dimensionless", mu)
            ).m_as("degC")
        else:
            td = mpcalc.dewpoint_from_relative_humidity(
                t * mu.degC, _as(raw, "percent", mu) / 100.0
            ).m_as("degC")
    if member_dim is None:
        t, td = t[None, :], td[None, :]

    # Surface: hide levels below ground and start the parcel at the surface.
    surface = None
    t2_name = d2_name = None
    sp_name = _match(prof, "surface_pressure", vdim, given)
    if sp_name:
        sp = float(np.nanmean(_as(prof[sp_name], "hPa", mu)))
        t2_name = _match(prof, "surface_temperature", vdim, given)
        d2_name = _match(prof, "surface_dewpoint", vdim, given)
        keep = p < sp
        p, t, td = p[keep], t[:, keep], td[:, keep]
        if t2_name and d2_name:
            t2 = np.atleast_1d(_as(prof[t2_name], "degC", mu))
            d2 = np.atleast_1d(_as(prof[d2_name], "degC", mu))
            p = np.concatenate([[sp], p])
            t = np.column_stack([np.broadcast_to(t2, (t.shape[0],)), t])
            td = np.column_stack([np.broadcast_to(d2, (td.shape[0],)), td])
        surface = sp

    t_mean = np.nanmean(t, axis=0)
    td_mean = np.nanmean(td, axis=0)
    ok = np.isfinite(t_mean) & np.isfinite(td_mean)
    if ok.sum() < 3:
        raise UsageError("fewer than 3 levels with temperature and dewpoint at this point")
    pq, tq, tdq = p[ok] * mu.hPa, t_mean[ok] * mu.degC, td_mean[ok] * mu.degC

    fig = plt.figure(figsize=(11, 8.5))
    # aspect="auto" fills the frame; isotherms stay at 45° (the skew is in display space).
    skew = SkewT(fig, rotation=45, rect=(0.07, 0.08, 0.6, 0.83), aspect="auto")
    if t.shape[0] > 1:
        for k in range(t.shape[0]):
            skew.plot(p, t[k], color="tab:red", lw=0.5, alpha=0.25)
            skew.plot(p, td[k], color="tab:green", lw=0.5, alpha=0.25)
    skew.plot(pq, tq, color="tab:red", lw=2.2, label="temperature")
    skew.plot(pq, tdq, color="tab:green", lw=2.2, label="dewpoint")

    lines = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parcel = mpcalc.parcel_profile(pq, tq[0], tdq[0])
        skew.plot(pq, parcel, color="black", lw=1.5, ls="--", label="surface parcel")
        skew.shade_cape(pq, tq, parcel)
        skew.shade_cin(pq, tq, parcel, tdq)
        lcl_p, lcl_t = mpcalc.lcl(pq[0], tq[0], tdq[0])
        skew.plot(lcl_p, lcl_t, "ko", markerfacecolor="black", ms=6)
        skew.ax.annotate(
            "LCL",
            (lcl_t.m_as("degC"), lcl_p.m_as("hPa")),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
        )
        cape, cin = mpcalc.cape_cin(pq, tq, tdq, parcel)
        li = np.ravel(mpcalc.lifted_index(pq, tq, parcel).m_as("delta_degC"))[0]
        pw = mpcalc.precipitable_water(pq, tdq).m_as("mm")
        lines += [
            f"SBCAPE {cape.m_as('J/kg'):.0f} J/kg",
            f"SBCIN  {cin.m_as('J/kg'):.0f} J/kg",
            f"LCL    {lcl_p.m_as('hPa'):.0f} hPa",
            f"LI     {li:.1f} K",
            f"PW     {pw:.0f} mm",
        ]

    skew.plot_dry_adiabats(alpha=0.25, lw=0.8)
    skew.plot_moist_adiabats(alpha=0.25, lw=0.8)
    skew.plot_mixing_lines(alpha=0.25, lw=0.8)
    bottom = 1050 if surface is None else min(1050, surface + 25)
    skew.ax.set_ylim(bottom, 100)
    tmin = np.nanmin(td_mean[ok]) - 5
    skew.ax.set_xlim(max(tmin, -40), np.nanmax(t_mean[ok]) + 15)
    skew.ax.set_xlabel("temperature (°C)")
    skew.ax.set_ylabel("pressure (hPa)")
    skew.ax.legend(loc="lower left", fontsize=8, framealpha=0.85)

    u_name = _match(prof, "u", vdim, given)
    v_name = _match(prof, "v", vdim, given)
    if u_name and v_name:
        p_lev = _as(prof[vdim], "hPa", mu)
        u = _as(prof[u_name], "knots", mu)
        v = _as(prof[v_name], "knots", mu)
        if member_dim and prof[u_name].ndim > 1:
            u = np.nanmean(xr.DataArray(u, dims=prof[u_name].dims).transpose(member_dim, vdim), 0)
            v = np.nanmean(xr.DataArray(v, dims=prof[v_name].dims).transpose(member_dim, vdim), 0)
        above = p_lev < surface if surface is not None else np.ones_like(p_lev, dtype=bool)
        inside = above & (p_lev > 100)  # a barb at the top edge would stick out of the frame
        skew.plot_barbs(p_lev[inside], u[inside], v[inside], length=6)
        z_name = _match(prof, "height", vdim, given)
        geo = None if z_name else _match(prof, "geopotential", vdim, given)
        if not no_hodograph and (z_name or geo):
            if z_name:
                z = _as(prof[z_name], "m", mu)
            else:
                phi = _as(prof[geo], "m^2/s^2", mu) * mu("m^2/s^2")
                z = mpcalc.geopotential_to_height(phi).m_as("m")
            if member_dim and np.ndim(z) > 1:
                z = np.nanmean(
                    xr.DataArray(z, dims=prof[z_name or geo].dims).transpose(member_dim, vdim), 0
                )
            z_agl = z - np.nanmin(z[above])
            sel = above & (z_agl <= 10000)
            ax_h = fig.add_axes((0.71, 0.53, 0.26, 0.34))
            h = Hodograph(ax_h, component_range=max(20, np.nanmax(np.hypot(u, v)[sel]) * 1.1))
            h.add_grid(increment=10, lw=0.5)
            h.plot_colormapped(u[sel], v[sel], z_agl[sel] / 1000.0, cmap="viridis")
            ax_h.set_title("hodograph (kt), 0-10 km", fontsize=9)
            ax_h.tick_params(labelsize=7)

    members = f"{t.shape[0]} members, mean bold" if t.shape[0] > 1 else ""
    fig.text(
        0.72,
        0.44,
        "\n".join(lines + ([members] if members else [])),
        family="monospace",
        fontsize=9,
        va="top",
        bbox={"boxstyle": "round", "fc": "white", "ec": "0.6"},
    )
    where = ", ".join(picked)
    fig.suptitle(
        title or " — ".join(x for x in (f"Skew-T {where}".strip(), _when(prof)) if x),
        fontsize=12,
    )
    # Bottom-left: core stamps its provenance mark bottom-right.
    fig.text(0.01, 0.01, f"MetPy {metpy.__version__}", fontsize=7, color="0.5")

    print(
        f"plot-skewt-metpy: temperature={t_name}, {moist_role}={moist_name}"
        + (f", surface_pressure={sp_name}" if sp_name else "")
        + (
            f", surface_temperature={t2_name}, surface_dewpoint={d2_name}"
            if sp_name and t2_name and d2_name
            else ""
        )
        + (f", u={u_name}, v={v_name}" if u_name and v_name else ""),
        file=sys.stderr,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_skewt()
