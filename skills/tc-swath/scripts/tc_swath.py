# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "scipy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Rainfall total or maximum wind brought by a tropical cyclone: the field counted only within
a radius of each member's storm centre, per ensemble member."""

from __future__ import annotations

import math
import sys

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

EARTH_RADIUS_KM = 6371.0
RADIUS_KM = {"rain": 500.0, "wind": 300.0}
RAIN_NAMES = (
    "tp",
    "precipitation_surface",
    "pr",
    "total_precipitation",
    "precipitation",
    "precip",
    "prcp",
)
WIND_NAMES = ("si10", "wind_speed_10m", "sfcWind", "ws10", "10m_wind_speed", "wind_speed")
WIND_PAIRS = (
    ("u10", "v10"),
    ("wind_u_10m", "wind_v_10m"),
    ("uas", "vas"),
    ("10m_u_component_of_wind", "10m_v_component_of_wind"),
)
MEMBER_DIMS = ("number", "member", "ensemble_member", "realization")
INIT_NAMES = ("time", "forecast_reference_time", "init_time")


def _unit(lat, lon):
    import numpy as np

    p, la = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return np.stack([np.cos(p) * np.cos(la), np.cos(p) * np.sin(la), np.sin(p)], axis=-1)


def _hours(times):
    import numpy as np

    t = np.asarray(times, dtype="datetime64[ns]")
    out = (t - np.datetime64("1970-01-01", "ns")) / np.timedelta64(1, "h")
    return np.where(np.isnat(t), np.nan, out)


def centre_at(hours, lat, lon, when):
    """Track centre at ``when`` (hours), interpolated on the sphere; None outside the track."""
    import numpy as np

    ok = np.isfinite(hours) & np.isfinite(lat) & np.isfinite(lon)
    h, la, lo = hours[ok], lat[ok], lon[ok]
    if h.size == 0 or when < h.min() - 1e-6 or when > h.max() + 1e-6:
        return None
    order = np.argsort(h)
    h, xyz = h[order], _unit(la[order], lo[order])
    v = np.array([np.interp(when, h, xyz[:, c]) for c in range(3)])
    return v / np.linalg.norm(v)


def _layout(ds):
    """``(member dim or None, time dim, valid times, value convention)``.

    Forecast stores (``step``) hold the value of the interval ending at each valid time;
    time-indexed stores (observations, reanalysis) the interval starting at each time.
    """
    import numpy as np

    member = next((d for d in MEMBER_DIMS if d in ds.dims), None)
    if "step" in ds.dims:
        init = next((n for n in INIT_NAMES if n in ds.coords and ds[n].size == 1), None)
        if init is None:
            raise UsageError("forecast field has a step dim but no scalar init time coordinate")
        valid = np.datetime64(ds[init].values.reshape(-1)[0], "ns") + ds["step"].values.astype(
            "timedelta64[ns]"
        )
        return member, "step", valid, "ending"
    if "time" in ds.dims:
        return member, "time", ds["time"].values.astype("datetime64[ns]"), "starting"
    raise UsageError("field has neither a step nor a time dimension")


def _field(ds, field, variable):
    """The rain rate (mm day-1) or wind speed (m s-1) DataArray."""
    import numpy as np
    from weather_skills_core.units import convert_values

    if field == "rain":
        name = variable or next((n for n in RAIN_NAMES if n in ds.data_vars), None)
        if name is None or name not in ds.data_vars:
            raise UsageError(
                f"no precipitation in the field; expected one of {', '.join(RAIN_NAMES)} "
                f"(or pass --variable); it has {', '.join(map(str, ds.data_vars))}"
            )
        da = ds[name]
        units = (da.attrs.get("units") or "").strip()
        try:
            values, _ = convert_values(da.values, units, "mm day-1")
        except Exception as exc:
            raise UsageError(
                f"{name!r} has units {units!r}; tc-swath needs a precipitation rate "
                "(mm day-1, mm h-1, kg m-2 s-1), as the fetchers write"
            ) from exc
        return da.copy(data=values), name
    name = variable or next((n for n in WIND_NAMES if n in ds.data_vars), None)
    if name is not None and name in ds.data_vars:
        da = ds[name]
        values, _ = convert_values(da.values, (da.attrs.get("units") or "m s-1"), "m s-1")
        return da.copy(data=values), name
    for u, v in WIND_PAIRS:
        if u in ds.data_vars and v in ds.data_vars:
            uu, _ = convert_values(ds[u].values, ds[u].attrs.get("units") or "m s-1", "m s-1")
            vv, _ = convert_values(ds[v].values, ds[v].attrs.get("units") or "m s-1", "m s-1")
            return ds[u].copy(data=np.hypot(uu, vv)), f"{u},{v}"
    raise UsageError(
        f"no 10 m wind in the field; expected one of {', '.join(WIND_NAMES)} or a u/v pair"
    )


def swath(values, valid_hours, convention, grid_xyz, centres, chord, field):
    """One member's storm total (rain, mm) or maximum (wind) on the flattened grid.

    Args:
        values: (time, cells) rain rate (mm day-1) or wind speed.
        valid_hours: hours of each time index.
        convention: ``ending`` or ``starting``: the interval each value covers.
        grid_xyz: (cells, 3) unit vectors, ``centres``: callable hour -> unit vector or None.
        chord: search radius as a chord on the unit sphere.

    Returns:
        ``(result, storm hours, mask)``: the per-cell result, the hours of storm time counted,
        and a (time, cells) boolean mask of the cells within the radius at each time.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    tree = cKDTree(grid_xyz)
    n_t = values.shape[0]
    steps = np.diff(valid_hours)
    out = np.zeros(values.shape[1]) if field == "rain" else np.full(values.shape[1], np.nan)
    mask = np.zeros(values.shape, bool)
    counted = 0.0
    for k in range(n_t):
        if field == "rain":
            if convention == "ending":
                if k == 0:
                    continue
                dt = steps[k - 1]
                mid = valid_hours[k] - dt / 2
            else:
                dt = steps[k] if k < n_t - 1 else (steps[-1] if steps.size else 24.0)
                mid = valid_hours[k] + dt / 2
        else:  # instantaneous wind: the centre at the valid time; dt only for bookkeeping
            dt = steps[k - 1] if k > 0 else (steps[0] if steps.size else 0.0)
            mid = valid_hours[k]
        centres_k = [c for c in centres(mid) if c is not None]
        if not centres_k:
            continue
        counted += dt
        idx = sorted({i for c in centres_k for i in tree.query_ball_point(c, chord)})
        if not idx:
            continue
        mask[k, idx] = True
        v = values[k, idx]
        if field == "rain":
            out[idx] += np.where(np.isfinite(v), v, 0.0) * dt / 24.0
        else:
            out[idx] = np.fmax(out[idx], v)
    if field == "wind":
        out = np.where(np.isfinite(out), out, 0.0)
    return out, counted, mask


@weather_skill(name="tc-swath", version=_SKILL_VERSION)
@weather_skill.argument(
    "--tracks",
    type=Dataset("any"),
    required=True,
    help="Storm tracks: tc-match output (per member) or tc-besttrack-fetch output.",
)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("spatial"),
    required=True,
    dest="ds",
    help="Gridded field: precipitation rate or 10 m wind, same members as the tracks.",
)
@weather_skill.argument(
    "--field", default="rain", choices=["rain", "wind"], help="rain (total, mm) or wind (max)."
)
@weather_skill.argument("--variable", default=None, help="Field variable name (default: found).")
@weather_skill.argument(
    "--radius",
    type=float,
    default=None,
    help="km from the storm centre (default 500 for rain, 300 for wind).",
)
@weather_skill.argument("--storm", default=None, help="Use the tracks of one storm (id or name).")
@weather_skill.argument(
    "--per-step",
    action="store_true",
    help="Keep the field's time axis: the rain rate or wind inside the radius, 0 outside, "
    "for the indicator skill (rules such as 'tc_rain sum 3d >= 100').",
)
def tc_swath(tracks, ds, field, variable, radius, storm, per_step, **kwargs):
    """Rainfall total or maximum wind brought by a tropical cyclone: the field counted only
    within a radius of each member's storm centre, per ensemble member."""
    import numpy as np
    import xarray as xr
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.units import dequantify_dataset

    tracks, ds = dequantify_dataset(tracks), dequantify_dataset(ds)
    if not {"track", "obs"} <= set(tracks.dims) or "latitude" not in tracks.coords:
        raise UsageError("--tracks is not a track file (no track/obs dims)")
    if "kind" in tracks.coords:
        keep = [k for k, v in enumerate(tracks["kind"].values) if v in ("model", "best_track")]
        if keep:
            tracks = tracks.isel(track=keep)
    if storm is not None:
        want = storm.upper()
        ids = [str(s).upper() for s in tracks["storm"].values] if "storm" in tracks.coords else []
        names = (
            [str(s).upper() for s in tracks["storm_name"].values]
            if "storm_name" in tracks.coords
            else []
        )
        keep = [
            k for k in range(tracks.sizes["track"]) if want in (ids[k : k + 1] + names[k : k + 1])
        ]
        if not keep:
            raise UsageError(f"--storm {storm!r} is not in --tracks")
        tracks = tracks.isel(track=keep)
    radius = radius if radius is not None else RADIUS_KM[field]
    da, name = _field(ds, field, variable)
    member, tdim, valid, convention = _layout(ds)
    lat_dim, lon_dim = detect_spatial_dims(ds)
    lat = ds[lat_dim].values.astype(float)
    lon = ds[lon_dim].values.astype(float)
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    grid_xyz = _unit(LA.ravel(), LO.ravel())
    chord = 2 * math.sin(radius / EARTH_RADIUS_KM / 2)
    valid_hours = _hours(valid)

    t_hours = [_hours(t) for t in tracks["time"].values]
    t_lat = tracks["latitude"].values.astype(float)
    t_lon = tracks["longitude"].values.astype(float)
    t_num = tracks["number"].values if "number" in tracks.coords else None
    if member is not None and t_num is None:
        raise UsageError("the field has ensemble members but --tracks has no `number`")

    def centres_for(rows):
        return lambda h: [centre_at(t_hours[k], t_lat[k], t_lon[k], h) for k in rows]

    members = ds[member].values if member is not None else [None]
    results, hours_counted, steps_out = [], [], []
    for m in members:
        rows = (
            list(range(tracks.sizes["track"]))
            if m is None
            else [k for k in range(tracks.sizes["track"]) if int(t_num[k]) == int(m)]
        )
        sub = da.sel({member: m}) if member is not None else da
        values = sub.transpose(tdim, lat_dim, lon_dim).values.reshape(len(valid), -1)
        if rows:
            res, counted, mask = swath(
                values, valid_hours, convention, grid_xyz, centres_for(rows), chord, field
            )
        else:
            res, counted, mask = np.zeros(values.shape[1]), 0.0, np.zeros(values.shape, bool)
        finite = np.isfinite(values).any(axis=0)
        results.append(np.where(finite, res, np.nan).reshape(LA.shape))
        hours_counted.append(counted)
        if per_step:
            kept = np.where(mask, values, np.where(np.isfinite(values), 0.0, np.nan))
            steps_out.append(kept.reshape(len(valid), *LA.shape))
    data = np.stack(results) if member is not None else results[0]
    dims = (member, lat_dim, lon_dim) if member is not None else (lat_dim, lon_dim)
    out_name, attrs = (
        (
            "tc_rain",
            {
                "units": "mm",
                "long_name": f"rainfall within {radius:g} km of the storm centre, storm total",
                "cell_methods": "time: sum",
            },
        )
        if field == "rain"
        else (
            "tc_wind_max",
            {
                "units": "m s-1",
                "long_name": f"maximum 10 m wind within {radius:g} km of the storm centre",
                "cell_methods": "time: maximum",
            },
        )
    )
    coords = {lat_dim: ds[lat_dim], lon_dim: ds[lon_dim]}
    if member is not None:
        coords[member] = ds[member]
    if per_step:
        data = np.stack(steps_out) if member is not None else steps_out[0]
        dims = (member, tdim, lat_dim, lon_dim) if member is not None else (tdim, lat_dim, lon_dim)
        coords[tdim] = ds[tdim]
        for name_c, var in ds.coords.items():  # init time, valid_time, ...
            if name_c not in coords and set(var.dims) <= {tdim}:
                coords[name_c] = var
        out_name = "tc_rain" if field == "rain" else "tc_wind"
        attrs = {
            "units": "mm day-1" if field == "rain" else "m s-1",
            "long_name": (
                f"{'rain rate' if field == 'rain' else '10 m wind'} within {radius:g} km of the "
                "storm centre (0 outside)"
            ),
        }
    out = xr.Dataset({out_name: (dims, data, attrs)}, coords=coords)
    if member is not None:
        out["storm_hours"] = (
            member,
            np.array(hours_counted),
            {"units": "hours", "long_name": "storm time counted for this member"},
        )
    storms = sorted({str(s) for s in tracks["storm"].values}) if "storm" in tracks.coords else []
    out.attrs.update(
        tc_swath_field=field,
        tc_swath_variable=name,
        tc_swath_radius_km=radius,
        tc_swath_storms=", ".join(storms),
        tc_swath_time_convention=(
            "value covers the interval ending at each valid time"
            if convention == "ending"
            else "value covers the interval starting at each time"
        ),
    )
    with_storm = int(np.sum(np.array(hours_counted) > 0))
    if with_storm == 0:
        raise DataError("no storm centre falls within the field's times; nothing was counted")
    peak = float(np.nanmax(data))
    unit = ("mm day-1 (rate)" if per_step else "mm") if field == "rain" else "m s-1"
    print(
        f"{out_name}: storm in {with_storm} of {len(members)} member(s); peak {peak:.0f} {unit}",
        file=sys.stderr,
    )
    return out


if __name__ == "__main__":
    tc_swath()
