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
"""Draw a Skew-T log-p diagram (and hodograph) of one or more vertical profiles with MetPy."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import NamedTuple

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

# Dims that only CF cell bounds use (time_bounds(time, nv) and the like).
BOUNDS_DIMS = ("nv", "bnds", "nbnd", "bounds")
# Per profile: line style of T/Td, and a colour for its parcel, barbs, hodograph and tick.
LINESTYLES = ("-", "--", ":", "-.")
COLOURS = ("black", "tab:blue", "tab:purple", "tab:orange", "tab:brown", "tab:cyan")
PROFILE_KEYS = ("label", "input", "station", "lat", "lon", "isel", "var")


class ProfileSpec:
    """One --profile: where to find it. Core opens and records ``zarr_paths()``."""

    def __init__(self, label, inputs, station=None, lat=None, lon=None, isel=(), var=()):
        if not inputs:
            raise argparse.ArgumentTypeError("a profile needs input=PATH[,PATH]")
        self.label = label
        self.inputs = [Path(p) for p in inputs]
        self.station = station
        self.lat = None if lat in (None, "") else float(lat)
        self.lon = None if lon in (None, "") else float(lon)
        self.isel = list(isel)
        self.var = list(var)
        self.ds = None
        self.datasets = None

    def zarr_paths(self):
        return self.inputs

    def opened(self):
        return self.datasets if self.datasets is not None else [self.ds]


def _items(value) -> list[str]:
    """A comma list, a JSON list, or a JSON object {key: value} as KEY=VALUE strings."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [f"{k}={v}" for k, v in value.items()]
    if isinstance(value, list | tuple):
        return [str(v) for v in value]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _spec(fields: dict, where: str) -> ProfileSpec:
    unknown = set(fields) - set(PROFILE_KEYS)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}; keys: {', '.join(PROFILE_KEYS)}"
        )
    try:
        return ProfileSpec(
            label=str(fields.get("label") or ""),
            inputs=_items(fields.get("input")),
            station=fields.get("station") or None,
            lat=fields.get("lat"),
            lon=fields.get("lon"),
            isel=_items(fields.get("isel")),
            var=_items(fields.get("var")),
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{where}: {exc}") from exc


def _parse_profile(text: str) -> ProfileSpec:
    """``label=..;input=a.zarr,b.zarr;station=ID|lat=..;lon=..;isel=time=5;var=role=name``."""
    fields = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise argparse.ArgumentTypeError(f"--profile part {part!r} is not KEY=VALUE")
        fields[key.strip()] = value.strip()
    return _spec(fields, "--profile")


class ProfilesFile:
    """--profiles-file: a JSON list of profiles with the --profile keys."""

    def __init__(self, path: str):
        try:
            entries = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise argparse.ArgumentTypeError(f"--profiles-file {path}: {exc}") from exc
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise argparse.ArgumentTypeError(
                f"--profiles-file {path}: expected a JSON list of objects"
            )
        self.specs = [_spec(e, f"--profiles-file entry {i + 1}") for i, e in enumerate(entries)]
        self.ds = None
        self.datasets = None

    def zarr_paths(self):
        return [p for spec in self.specs for p in spec.inputs]

    def split(self) -> list[ProfileSpec]:
        """Hand the opened datasets back to each profile, in order."""
        opened = iter(self.datasets if self.datasets is not None else [self.ds])
        for spec in self.specs:
            spec.datasets = [next(opened) for _ in spec.inputs]
        return self.specs


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


def _drop_bounds(ds: xr.Dataset) -> xr.Dataset:
    """Without CF bounds variables, whose extra dim (nv, bnds) is not a profile choice."""
    names = {str(v.attrs["bounds"]) for v in ds.variables.values() if "bounds" in v.attrs}
    names |= {str(n) for n, v in ds.variables.items() if set(v.dims) & set(BOUNDS_DIMS)}
    return ds.drop_vars([n for n in names if n in ds.variables])


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
        sdim = next((d for d in names_for("point_id") if d in ds.dims), None)
        if sdim is None:
            raise UsageError("--station needs station data with a point_id or station_id dim")
        if station not in ds[sdim].values:
            raise UsageError(f"--station {station!r} not in the input")
        ds = ds.sel({sdim: station})
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


def _given(var: list[str]) -> dict[str, str]:
    given = {}
    for item in var:
        role, sep, name = item.partition("=")
        if not sep or role not in ROLES or not name:
            raise UsageError(
                f"--var {item!r} must be ROLE=NAME with ROLE one of {', '.join(ROLES)}"
            )
        given[role] = name
    return given


class Profile(NamedTuple):
    """One profile, ready to draw: levels above ground, surface point first."""

    label: str
    picked: list[str]
    when: str
    p: np.ndarray  # hPa
    t: np.ndarray  # (members, level), degC
    td: np.ndarray
    ok: np.ndarray  # levels where the mean T and Td are finite
    t_mean: np.ndarray
    td_mean: np.ndarray
    surface: float | None  # hPa
    wind: tuple | None  # (p hPa, u kt, v kt) above ground, finite, below the top
    hodo: tuple | None  # (u kt, v kt, height above ground km), 0-10 km
    mapping: str


def _load_profile(datasets, label, lat, lon, station, isel, given, mu, mpcalc) -> Profile:
    """Merge the inputs and reduce them to one profile with MetPy-ready arrays."""
    datasets = [dequantify_dataset(d) for d in datasets]
    try:
        merged = xr.merge(
            datasets, join="inner", compat="no_conflicts", combine_attrs="drop_conflicts"
        )
    except Exception as exc:
        raise UsageError(f"inputs do not combine: {exc}") from exc
    merged = _drop_bounds(merged)
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

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN levels
        t_mean = np.nanmean(t, axis=0)
        td_mean = np.nanmean(td, axis=0)
    ok = np.isfinite(t_mean) & np.isfinite(td_mean)
    if ok.sum() < 3:
        raise UsageError(
            f"{label + ': ' if label else ''}fewer than 3 levels with temperature and dewpoint "
            "at this point"
        )

    wind = hodo = None
    u_name = _match(prof, "u", vdim, given)
    v_name = _match(prof, "v", vdim, given)
    z_name = geo = None
    if u_name and v_name:
        p_lev = _as(prof[vdim], "hPa", mu)
        u = _as(prof[u_name], "knots", mu)
        v = _as(prof[v_name], "knots", mu)
        if member_dim and prof[u_name].ndim > 1:
            u = np.nanmean(xr.DataArray(u, dims=prof[u_name].dims).transpose(member_dim, vdim), 0)
            v = np.nanmean(xr.DataArray(v, dims=prof[v_name].dims).transpose(member_dim, vdim), 0)
        above = p_lev < surface if surface is not None else np.ones_like(p_lev, dtype=bool)
        inside = above & (p_lev > 100)  # a barb at the top edge would stick out of the frame
        idx = np.flatnonzero(inside & np.isfinite(u) & np.isfinite(v))
        wind = (p_lev[idx], u[idx], v[idx])
        z_name = _match(prof, "height", vdim, given)
        geo = None if z_name else _match(prof, "geopotential", vdim, given)
        if z_name or geo:
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
            hodo = (u[sel], v[sel], z_agl[sel] / 1000.0)

    mapping = (
        f"temperature={t_name}, {moist_role}={moist_name}"
        + (f", surface_pressure={sp_name}" if sp_name else "")
        + (
            f", surface_temperature={t2_name}, surface_dewpoint={d2_name}"
            if sp_name and t2_name and d2_name
            else ""
        )
        + (f", u={u_name}, v={v_name}" if u_name and v_name else "")
    )
    return Profile(
        label, picked, _when(prof), p, t, td, ok, t_mean, td_mean, surface, wind, hodo, mapping
    )


def _indices(prof: Profile, mu, mpcalc) -> dict:
    """Surface parcel, CAPE, CIN, LCL, lifted index and precipitable water of the mean profile."""
    pq = prof.p[prof.ok] * mu.hPa
    tq = prof.t_mean[prof.ok] * mu.degC
    tdq = prof.td_mean[prof.ok] * mu.degC
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parcel = mpcalc.parcel_profile(pq, tq[0], tdq[0])
        lcl_p, lcl_t = mpcalc.lcl(pq[0], tq[0], tdq[0])
        cape, cin = mpcalc.cape_cin(pq, tq, tdq, parcel)
        li = np.ravel(mpcalc.lifted_index(pq, tq, parcel).m_as("delta_degC"))[0]
        pw = mpcalc.precipitable_water(pq, tdq).m_as("mm")
    return {
        "pq": pq,
        "tq": tq,
        "tdq": tdq,
        "parcel": parcel,
        "lcl_p": lcl_p,
        "lcl_t": lcl_t,
        "sbcape": float(cape.m_as("J/kg")),
        "sbcin": float(cin.m_as("J/kg")),
        "lcl": float(lcl_p.m_as("hPa")),
        "li": float(li),
        "pw": float(pw),
    }


def _box_text(profiles: list[Profile], stats: list[dict]) -> str:
    """The index box: today's list for one profile, a table with a column each for several."""
    rows = [
        ("SBCAPE", "sbcape", "{:.0f}", "J/kg"),
        ("SBCIN", "sbcin", "{:.0f}", "J/kg"),
        ("LCL", "lcl", "{:.0f}", "hPa"),
        ("LI", "li", "{:.1f}", "K"),
        ("PW", "pw", "{:.0f}", "mm"),
    ]
    if len(profiles) == 1:
        s = stats[0]
        lines = [f"{name:<6} {fmt.format(s[key])} {unit}" for name, key, fmt, unit in rows]
        n = profiles[0].t.shape[0]
        return "\n".join(lines + ([f"{n} members, mean bold"] if n > 1 else []))
    # Labels as column heads when short; else numbered columns with a key below.
    short = len(profiles) <= 2 and all(len(p.label or "") <= 12 for p in profiles)
    heads = [(p.label or f"#{k + 1}") if short else str(k + 1) for k, p in enumerate(profiles)]
    widths = [max(len(h), 5) for h in heads]
    lines = [" " * 8 + "  ".join(h.rjust(w) for h, w in zip(heads, widths, strict=True))]
    for name, key, fmt, unit in rows:
        cells = [fmt.format(s[key]).rjust(w) for s, w in zip(stats, widths, strict=True)]
        lines.append(f"{name:<7} " + "  ".join(cells) + f" {unit}")
    if any(p.t.shape[0] > 1 for p in profiles):
        cells = [
            (str(p.t.shape[0]) if p.t.shape[0] > 1 else "-").rjust(w)
            for p, w in zip(profiles, widths, strict=True)
        ]
        lines.append(f"{'members':<7} " + "  ".join(cells))
    if not short:
        lines += [f"{k + 1}: {p.label or f'#{k + 1}'}" for k, p in enumerate(profiles)]
    return "\n".join(lines)


def _place_box(ax, text, points, x_right, font, style):
    """Put the box in the frame corner that covers the fewest profile points."""
    candidates = [
        (x_right, 0.985, "right", "top"),
        (0.02, 0.985, "left", "top"),
        (x_right, 0.62, "right", "top"),
        (x_right, 0.35, "right", "top"),
    ]
    renderer = ax.figure.canvas.get_renderer()
    best = None
    for x, y, ha, va in candidates:
        art = ax.text(
            x, y, text, family="monospace", fontsize=font, ha=ha, va=va, multialignment="left",
            transform=ax.transAxes, bbox=style, zorder=5,
        )  # fmt: skip
        box = art.get_window_extent(renderer).transformed(ax.transAxes.inverted())
        hits = int(
            np.sum(
                (points[:, 0] >= box.x0)
                & (points[:, 0] <= box.x1)
                & (points[:, 1] >= box.y0)
                & (points[:, 1] <= box.y1)
            )
        )
        if best is None or hits < best[0]:
            if best is not None:
                best[1].remove()
            best = (hits, art)
        else:
            art.remove()
        if hits == 0:
            break


def _range(text: str | None, flag: str) -> tuple[float, float] | None:
    if text is None:
        return None
    a, sep, b = text.partition("/")
    try:
        if not sep:
            raise ValueError
        return float(a), float(b)
    except ValueError:
        raise UsageError(f"{flag} {text!r} must be two numbers like 960/550") from None


def _axes_xy(ax, temp, pres) -> np.ndarray:
    """(temperature, pressure) points in axes coordinates (the skew included)."""
    pts = ax.transData.transform(np.column_stack([temp, pres]))
    return ax.transAxes.inverted().transform(pts)


def _fit_xlim(ax, temp, pres, margin=0.04):
    """Temperature limits so these points sit inside the skewed frame."""
    temp, pres = np.asarray(temp), np.asarray(pres)
    good = np.isfinite(temp) & np.isfinite(pres)
    temp, pres = temp[good], pres[good]
    if temp.size == 0:
        return
    ax.set_xlim(np.min(temp) - 3, np.max(temp) + 3)
    for _ in range(6):  # the skew offset scales with the width: a few rounds settle
        lo, hi = ax.get_xlim()
        x = _axes_xy(ax, temp, pres)[:, 0]
        span = hi - lo
        ax.set_xlim(lo + (x.min() - margin) * span, lo + (x.max() + margin) * span)


def _label_line(ax, temp, pres, text, color, y_max=0.97):
    """Write ``text`` at the highest point of a reference line inside the frame."""
    xy = _axes_xy(ax, temp, pres)
    inside = (xy[:, 0] > 0.02) & (xy[:, 0] < 0.95) & (xy[:, 1] > 0.03) & (xy[:, 1] < y_max)
    if not inside.any():
        return
    i = np.flatnonzero(inside)[np.argmax(xy[inside, 1])]
    ax.text(
        temp[i],
        pres[i],
        text,
        fontsize=6.5,
        color=color,
        ha="center",
        va="center",
        clip_on=True,
        bbox={"boxstyle": "round,pad=0.1", "fc": "white", "ec": "none", "alpha": 0.7},
        zorder=3,
    )


THETA = np.arange(250, 451, 10.0)  # K, labelled dry adiabats
THETA_W = np.arange(-8, 33, 4.0)  # degC, labelled moist adiabats
MIXING = np.array([0.4, 1, 2, 4, 7, 10, 16, 24.0])  # g/kg, MetPy's default set


def _fiducials(skew, bottom, top, style, labels, mixing_top, mu):
    """Dry and moist adiabats and mixing lines; exactly MetPy's defaults unless asked."""
    if style == "faint" and not labels and mixing_top is None:
        skew.plot_dry_adiabats(alpha=0.25, lw=0.8)
        skew.plot_moist_adiabats(alpha=0.25, lw=0.8)
        skew.plot_mixing_lines(alpha=0.25, lw=0.8)
        return
    kw = {"alpha": 0.25, "lw": 0.8} if style == "faint" else {"alpha": 0.7, "lw": 1.1}
    p = np.geomspace(bottom, top, 60) * mu.hPa
    if labels:
        skew.plot_dry_adiabats(t0=THETA * mu.K, pressure=p, label="dry adiabat (θ, K)", **kw)
        skew.plot_moist_adiabats(
            t0=THETA_W * mu.degC, pressure=p, label="moist adiabat (θw, °C)", **kw
        )
    else:
        skew.plot_dry_adiabats(pressure=p, **kw)
        skew.plot_moist_adiabats(pressure=p, **kw)
    skew.plot_mixing_lines(
        mixing_ratio=(MIXING / 1000.0) * mu.dimensionless,
        pressure=_mixing_pressures(bottom, mixing_top) * mu.hPa,
        **({"label": "mixing ratio (g/kg)"} if labels else {}),
        **kw,
    )


def _mixing_pressures(bottom, mixing_top):
    w_top = 600.0 if mixing_top is None else float(mixing_top)
    return np.geomspace(max(bottom, w_top + 1), w_top, 40)


def _fiducial_labels(skew, bottom, top, mixing_top, mu, mpcalc):
    """Value labels at the top of each reference line inside the final frame."""
    p = np.geomspace(bottom, top, 120)
    for th in THETA:
        _label_line(skew.ax, th * (p / 1000.0) ** 0.2857 - 273.15, p, f"{th:.0f}", "tab:red")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for tw in THETA_W:
            # moist_lapse runs from its first pressure: start at 1000 hPa, go up and down.
            temp = np.full(p.shape, np.nan)
            up = np.flatnonzero(p <= 1000)[np.argsort(-p[p <= 1000])]
            down = np.flatnonzero(p > 1000)[np.argsort(p[p > 1000])]
            for part in (up, down):  # each monotonic away from 1000 hPa
                if part.size:
                    seg = np.concatenate([[1000.0], p[part]]) * mu.hPa
                    temp[part] = mpcalc.moist_lapse(seg, tw * mu.degC).m_as("degC")[1:]
            _label_line(skew.ax, temp, p, f"{tw:.0f}", "tab:blue", y_max=0.93)
        p_mix = _mixing_pressures(bottom, mixing_top)
        for w in MIXING:
            e = mpcalc.vapor_pressure(p_mix * mu.hPa, w / 1000.0 * mu.dimensionless)
            temp = mpcalc.dewpoint(e).m_as("degC")
            _label_line(skew.ax, temp, p_mix, f"{w:g}", "tab:green", y_max=0.89)


def _draw_profiles(skew, profiles, stats, shade, shade_kw, multi, lcl_text=True):
    """Temperature, dewpoint, parcel, shading and LCL of every profile."""
    for k, (prof, s) in enumerate(zip(profiles, stats, strict=True)):
        ls = LINESTYLES[k % len(LINESTYLES)]
        colour = COLOURS[k % len(COLOURS)]
        name = prof.label or f"#{k + 1}"
        if prof.t.shape[0] > 1:
            for m in range(prof.t.shape[0]):
                skew.plot(prof.p, prof.t[m], color="tab:red", lw=0.5, alpha=0.25, ls=ls)
                skew.plot(prof.p, prof.td[m], color="tab:green", lw=0.5, alpha=0.25, ls=ls)
        skew.plot(
            s["pq"], s["tq"], color="tab:red", lw=2.2, ls=ls,
            label=f"{name} T" if multi else "temperature",
        )  # fmt: skip
        skew.plot(
            s["pq"], s["tdq"], color="tab:green", lw=2.2, ls=ls,
            label=f"{name} Td" if multi else "dewpoint",
        )  # fmt: skip
        skew.plot(
            s["pq"], s["parcel"], color=colour, lw=1.5, ls="--",
            label=f"{name} parcel" if multi else "surface parcel",
        )  # fmt: skip
        if shade == "all" or (shade == "first" and k == 0):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                skew.shade_cape(s["pq"], s["tq"], s["parcel"], **shade_kw["cape"])
                skew.shade_cin(s["pq"], s["tq"], s["parcel"], s["tdq"], **shade_kw["cin"])
        skew.plot(s["lcl_p"], s["lcl_t"], "o", color=colour, markerfacecolor=colour, ms=6)
        if lcl_text and k == 0:
            skew.ax.annotate(
                "LCL",
                (s["lcl_t"].m_as("degC"), s["lcl_p"].m_as("hPa")),
                xytext=(8, 0),
                textcoords="offset points",
                va="center",
                fontsize=9,
            )


def _surface_ticks(ax, profiles):
    """A short tick on the left edge at each profile's surface pressure."""
    import matplotlib.transforms as mtransforms

    blend = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
    for k, prof in enumerate(profiles):
        if prof.surface is None:
            continue
        colour = COLOURS[k % len(COLOURS)]
        ax.plot([0, 0.035], [prof.surface, prof.surface], color=colour, lw=2.5, transform=blend)
        ax.text(
            0.04, prof.surface, f"{prof.surface:.0f}", color=colour, fontsize=7.5,
            va="center", transform=blend,
        )  # fmt: skip


@weather_skill(name="plot-skewt-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    help=(
        "Zarr with temperature and moisture on pressure levels; repeat to add surface "
        "fields (sp, t2m, d2m) or winds and heights. For one profile; use --profile for several."
    ),
)
@weather_skill.argument("--lat", type=float, help="Latitude of the grid cell to plot (nearest).")
@weather_skill.argument("--lon", type=float, help="Longitude of the grid cell to plot (nearest).")
@weather_skill.argument(
    "--station", help="Station id (point_id / station_id) of the profile to plot."
)
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
    "--profile",
    type=_parse_profile,
    action="append",
    default=[],
    metavar="SPEC",
    help=(
        "One profile to overlay, repeatable, instead of -i: "
        "'label=Balloon;input=a.zarr[,b.zarr];station=ID (or lat=..;lon=..);"
        "isel=time=5[,step=1];var=ROLE=NAME[,...]'."
    ),
)
@weather_skill.argument(
    "--profiles-file",
    type=ProfilesFile,
    metavar="JSON",
    help="A JSON list of profiles with the --profile keys, instead of -i or --profile.",
)
@weather_skill.argument(
    "--shade",
    choices=("first", "all", "none"),
    default="first",
    help="Which profiles get CAPE/CIN shading (default first).",
)
@weather_skill.argument(
    "--barbs",
    choices=("first", "all", "none"),
    default="first",
    help="Which profiles get wind barbs; with all, one column each (default first).",
)
@weather_skill.argument(
    "--shade-alpha", type=float, help="Opacity of the CAPE/CIN shading (MetPy's default 0.2)."
)
@weather_skill.argument("--cape-color", help="Colour of the CAPE shading (default red).")
@weather_skill.argument("--cin-color", help="Colour of the CIN shading (default blue).")
@weather_skill.argument(
    "--pressure-range", metavar="BOTTOM/TOP", help="Pressure axis in hPa, e.g. 960/550."
)
@weather_skill.argument(
    "--temperature-range",
    metavar="LEFT/RIGHT",
    help="Temperature axis in °C at the bottom of the frame, e.g. -15/10.",
)
@weather_skill.argument(
    "--fiducial-style",
    choices=("faint", "bold"),
    default="faint",
    help="Dry/moist adiabats and mixing lines: faint (default) or bold.",
)
@weather_skill.argument(
    "--fiducial-labels",
    action="store_true",
    help="Label the reference lines (theta in K, theta-w in °C, mixing ratio in g/kg).",
)
@weather_skill.argument(
    "--mixing-lines-top",
    type=float,
    metavar="HPA",
    help="Draw the mixing ratio lines up to this pressure (default 600).",
)
@weather_skill.argument(
    "--freezing-isotherm", action="store_true", help="Highlight the 0 °C isotherm."
)
@weather_skill.argument(
    "--inset",
    choices=("off", "on", "auto"),
    default="off",
    help="A zoomed Skew-T of the lower troposphere; auto when SBCAPE is under 100 J/kg.",
)
@weather_skill.argument(
    "--no-hodograph", action="store_true", help="Leave out the hodograph inset."
)
@weather_skill.argument("--figsize", metavar="W,H", help="Figure size in inches.")
@weather_skill.argument("--dpi", type=int, default=120, help="Resolution of the PNG.")
@weather_skill.argument("--title", default=None, help="Figure title (default: place and time).")
def plot_skewt(
    ds,
    lat,
    lon,
    station,
    isel,
    var,
    profile,
    profiles_file,
    shade,
    barbs,
    shade_alpha,
    cape_color,
    cin_color,
    pressure_range,
    temperature_range,
    fiducial_style,
    fiducial_labels,
    mixing_lines_top,
    freezing_isotherm,
    inset,
    no_hodograph,
    figsize,
    dpi,
    title,
    output,
    **kwargs,
):
    """Draw a Skew-T log-p diagram (and hodograph) of one or more vertical profiles with MetPy.

    Temperature and dewpoint (every ensemble member thin, the mean bold), the
    surface parcel with CAPE and CIN shaded, the LCL, wind barbs, dry and moist
    adiabats and mixing lines, plus a hodograph and a box with CAPE, CIN, LCL,
    lifted index and precipitable water, for one grid cell or station of a
    pressure-level forecast, reanalysis or sounding Zarr. With --profile,
    several labelled profiles (e.g. a balloon and a model) are overlaid, each
    on its own levels and surface.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import metpy
    import metpy.calc as mpcalc
    from metpy.plots import Hodograph, SkewT
    from metpy.units import units as mu

    specs = list(profile) + (profiles_file.split() if profiles_file is not None else [])
    if specs and ds:
        raise UsageError("use either -i (one profile) or --profile / --profiles-file, not both")
    if not specs and not ds:
        raise UsageError("give -i (one profile) or --profile / --profiles-file")
    if specs and (lat is not None or lon is not None or station or isel or var):
        raise UsageError(
            "with --profile, put station / lat / lon / isel / var inside each profile spec"
        )
    p_range = _range(pressure_range, "--pressure-range")
    t_range = _range(temperature_range, "--temperature-range")
    size = None
    if figsize:
        try:
            w, h = (float(x) for x in figsize.split(","))
            size = (w, h)
        except ValueError:
            raise UsageError(f"--figsize {figsize!r} must be W,H in inches") from None

    if specs:
        profiles = [
            _load_profile(
                s.opened(), s.label, s.lat, s.lon, s.station, s.isel, _given(s.var), mu, mpcalc
            )
            for s in specs
        ]
    else:
        inputs = ds if isinstance(ds, list) else [ds]
        profiles = [_load_profile(inputs, "", lat, lon, station, isel, _given(var), mu, mpcalc)]
    multi = len(profiles) > 1
    stats = [_indices(p, mu, mpcalc) for p in profiles]
    for prof, s in zip(profiles, stats, strict=True):
        name = f"{prof.label}: " if prof.label else ""
        print(f"plot-skewt-metpy: {name}{prof.mapping}", file=sys.stderr)
        print(
            f"plot-skewt-metpy: {name}sbcape={s['sbcape']:.1f} sbcin={s['sbcin']:.1f} "
            f"lcl={s['lcl']:.1f} li={s['li']:.2f} pw={s['pw']:.1f} "
            f"bottom={s['pq'][0].m_as('hPa'):.1f}",
            file=sys.stderr,
        )

    has_hodo = not no_hodograph and any(p.hodo is not None for p in profiles)
    want_inset = inset == "on" or (inset == "auto" and 0 < stats[0]["sbcape"] < 100)
    column = has_hodo or want_inset  # the right-hand column of the classic layout
    fig = plt.figure(figsize=size or ((11, 8.5) if column else (8.5, 8.5)))
    # aspect="auto" fills the frame; isotherms stay at 45° (the skew is in display space).
    rect = (0.07, 0.08, 0.6, 0.83) if column else (0.09, 0.07, 0.86, 0.84)
    skew = SkewT(fig, rotation=45, rect=rect, aspect="auto")
    shade_kw = {
        "cape": {
            **({"alpha": shade_alpha} if shade_alpha is not None else {}),
            **({"color": cape_color} if cape_color else {}),
        },
        "cin": {
            **({"alpha": shade_alpha} if shade_alpha is not None else {}),
            **({"color": cin_color} if cin_color else {}),
        },
    }
    _draw_profiles(skew, profiles, stats, shade, shade_kw, multi)

    surfaces = [p.surface for p in profiles]
    if p_range:
        bottom, top = p_range
    else:
        bottom = 1050 if None in surfaces else min(1050, max(surfaces) + 25)
        top = 100
    _fiducials(skew, bottom, top, fiducial_style, fiducial_labels, mixing_lines_top, mu)
    if freezing_isotherm:
        skew.ax.axvline(0, color="tab:cyan", lw=1.8, label="0 °C isotherm")
    skew.ax.set_ylim(bottom, top)
    if t_range:
        skew.ax.set_xlim(*t_range)
    elif p_range:
        lo_p, hi_p = max(p_range), min(p_range)
        temps, pres = [], []
        for prof in profiles:
            win = prof.ok & (prof.p <= lo_p) & (prof.p >= hi_p)
            temps += [prof.t_mean[win], prof.td_mean[win]]
            pres += [prof.p[win], prof.p[win]]
        _fit_xlim(skew.ax, np.concatenate(temps), np.concatenate(pres))
    else:
        t_all = np.concatenate([p.t_mean[p.ok] for p in profiles])
        td_all = np.concatenate([p.td_mean[p.ok] for p in profiles])
        skew.ax.set_xlim(max(np.nanmin(td_all) - 5, -40), np.nanmax(t_all) + 15)
    if fiducial_labels:
        _fiducial_labels(skew, bottom, top, mixing_lines_top, mu, mpcalc)
    if multi:
        _surface_ticks(skew.ax, profiles)
    skew.ax.set_xlabel("temperature (°C)")
    skew.ax.set_ylabel("pressure (hPa)")
    skew.ax.legend(loc="lower left", fontsize=8 if not multi else 7.5, framealpha=0.85)

    barb_cols = []
    if barbs != "none":
        chosen = profiles[:1] if barbs == "first" else profiles
        for k, prof in enumerate(chosen):
            if prof.wind is None or prof.wind[0].size == 0:
                continue
            p_w, u, v = prof.wind
            keep = (p_w <= bottom) & (p_w >= top)
            p_w, u, v = p_w[keep], u[keep], v[keep]
            # At most ~30 barbs: a sounding gridded every 10 hPa would otherwise be a solid column.
            idx = np.arange(p_w.size)[:: max(1, int(np.ceil(p_w.size / 30)))]
            xloc = 1.0 - 0.07 * len(barb_cols)
            extra = {"color": COLOURS[k % len(COLOURS)]} if multi else {}
            skew.plot_barbs(p_w[idx], u[idx], v[idx], length=6, xloc=xloc, **extra)
            barb_cols.append(xloc)

    if has_hodo:
        ax_h = fig.add_axes((0.71, 0.53, 0.26, 0.34))
        with_hodo = [(k, p) for k, p in enumerate(profiles) if p.hodo is not None]
        speed = max(
            (np.nanmax(np.hypot(p.hodo[0], p.hodo[1])) for _, p in with_hodo if p.hodo[0].size),
            default=0.0,
        )
        h = Hodograph(ax_h, component_range=max(20, speed * 1.1))
        h.add_grid(increment=10, lw=0.5)
        if not multi:
            u, v, z = profiles[0].hodo
            h.plot_colormapped(u, v, z, cmap="viridis")
        else:
            for k, prof in with_hodo:
                h.plot(
                    prof.hodo[0], prof.hodo[1], color=COLOURS[k % len(COLOURS)], linewidth=1.6,
                    ls=LINESTYLES[k % len(LINESTYLES)], label=prof.label or f"#{k + 1}",
                )  # fmt: skip
            ax_h.legend(fontsize=7, loc="upper left")
        ax_h.set_title("hodograph (kt), 0-10 km", fontsize=9)
        ax_h.tick_params(labelsize=7)

    if want_inset:
        inset_rect = (0.735, 0.08, 0.235, 0.22) if has_hodo else (0.735, 0.53, 0.235, 0.36)
        small = SkewT(fig, rotation=45, rect=inset_rect, aspect="auto")
        _draw_profiles(small, profiles, stats, shade, shade_kw, multi, lcl_text=False)
        i_bottom = 1050 if None in surfaces else min(1050, max(surfaces) + 10)
        small.ax.set_ylim(i_bottom, 550)
        temps, pres = [], []
        for prof in profiles:
            win = prof.ok & (prof.p >= 550)
            temps += [prof.t_mean[win], prof.td_mean[win]]
            pres += [prof.p[win], prof.p[win]]
        _fit_xlim(small.ax, np.concatenate(temps), np.concatenate(pres))
        small.ax.set_xlabel("")
        small.ax.set_ylabel("")
        small.ax.tick_params(labelsize=6.5)
        small.ax.set_title("lowest levels to 550 hPa", fontsize=8)

    box = _box_text(profiles, stats)
    box_style = {"boxstyle": "round", "fc": "white", "ec": "0.6"}
    font = 9 if not multi else 8
    if column:
        fig.text(0.72, 0.44, box, family="monospace", fontsize=font, va="top", bbox=box_style)
    else:
        # Inside the frame, left of the barbs, where it hides the least of the profiles.
        pts = [_axes_xy(skew.ax, prof.t_mean[prof.ok], prof.p[prof.ok]) for prof in profiles] + [
            _axes_xy(skew.ax, prof.td_mean[prof.ok], prof.p[prof.ok]) for prof in profiles
        ]
        pts += [_axes_xy(skew.ax, s["parcel"].m_as("degC"), s["pq"].m_as("hPa")) for s in stats]
        x_right = (min(barb_cols) if barb_cols else 1.0) - 0.07
        _place_box(skew.ax, box, np.concatenate(pts), x_right, font, box_style)

    if title:
        heading = title
    elif not multi:
        where = ", ".join(profiles[0].picked)
        heading = " — ".join(x for x in (f"Skew-T {where}".strip(), profiles[0].when) if x)
    else:
        parts = [
            f"{p.label or f'#{k + 1}'} ({p.when or ', '.join(p.picked)})"
            if (p.when or p.picked)
            else (p.label or f"#{k + 1}")
            for k, p in enumerate(profiles)
        ]
        joined = "Skew-T: " + " vs ".join(parts)
        heading = joined if len(joined) <= 100 else "Skew-T\n" + "\n".join(parts)
    fig.suptitle(heading, fontsize=12 if heading.count("\n") == 0 else 10)
    # Bottom-left: core stamps its provenance mark bottom-right.
    fig.text(0.01, 0.01, f"MetPy {metpy.__version__}", fontsize=7, color="0.5")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if column:
        fig.savefig(output, dpi=dpi)
    else:
        fig.savefig(output, dpi=dpi, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_skewt()
