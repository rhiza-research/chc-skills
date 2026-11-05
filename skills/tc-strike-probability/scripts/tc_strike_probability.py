# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "numpy",
#   "pandas",
#   "scipy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Map the chance that a tropical cyclone passes within a radius, from ensemble tc-track tracks."""
# ECMWF's strike probability: the percentage of ensemble members with a tracked storm whose
# centre passes within --radius km (120 by default, as ECMWF) of a grid point during the window.
# Tracks are densified along great circles so fast storms do not skip cells.

from __future__ import annotations

import math

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

EARTH_RADIUS_KM = 6371.0


def _unit_vectors(lat, lon):
    import numpy as np

    p, la = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(p) * np.cos(la), np.cos(p) * np.sin(la), np.sin(p)], axis=-1)


def _present(t):
    """True for a real time (not None or NaT)."""
    import numpy as np

    return t is not None and not (isinstance(t, np.datetime64) and np.isnat(t))


def _hours(values, ref):
    """Hours of each time since ``ref`` (datetime64 or cftime); NaN for missing."""
    import numpy as np

    values = np.asarray(values)
    if np.issubdtype(values.dtype, np.datetime64):
        return (values - ref) / np.timedelta64(1, "h")
    return np.array([np.nan if v is None else (v - ref).total_seconds() / 3600.0 for v in values])


def densify(lat, lon, hours, step_km):
    """Points along a track at most ``step_km`` apart (great-circle interpolation)."""
    import numpy as np

    ok = np.isfinite(lat) & np.isfinite(lon)
    lat, lon, hours = lat[ok], lon[ok], hours[ok]
    if lat.size < 2:
        return lat, lon, hours
    v = _unit_vectors(lat, lon)
    pts, hrs = [v[:1]], [hours[:1]]
    for k in range(lat.size - 1):
        a, b = v[k], v[k + 1]
        ang = math.acos(float(np.clip(a @ b, -1.0, 1.0)))
        n = max(1, math.ceil(ang * EARTH_RADIUS_KM / step_km))
        f = np.arange(1, n + 1) / n
        if ang < 1e-9:
            seg = np.repeat(b[None], n, axis=0)
        else:
            seg = (np.sin((1 - f) * ang)[:, None] * a + np.sin(f * ang)[:, None] * b) / math.sin(
                ang
            )
        pts.append(seg)
        hrs.append(hours[k] + f * (hours[k + 1] - hours[k]))
    p = np.concatenate(pts)
    return (
        np.degrees(np.arcsin(np.clip(p[:, 2], -1, 1))),
        np.degrees(np.arctan2(p[:, 1], p[:, 0])),
        np.concatenate(hrs),
    )


def strike_probability(tracks, lat, lon, radius_km, start=None, end=None):
    """Percentage of members with a track within ``radius_km`` of each grid point.

    Args:
        tracks: tc-track dataset (dequantified).
        lat, lon: 1-D target grid coordinates.
        radius_km: strike radius.
        start, end: optional window bounds (same time type as the tracks), inclusive.

    Returns:
        ``(prob, n_members)``: a (lat, lon) array in percent and the member count.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    members = tracks.attrs.get("ensemble_members")
    if members is None:
        members = (
            sorted(set(tracks["number"].values.tolist())) if "number" in tracks.coords else [0]
        )
    members = [int(m) for m in np.atleast_1d(members)]
    track_member = (
        tracks["number"].values.astype(int)
        if "number" in tracks.coords
        else np.zeros(tracks.sizes["track"], int)
    )
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    tree = cKDTree(_unit_vectors(LA.ravel(), LO.ravel()))
    chord = 2 * math.sin(radius_km / (2 * EARTH_RADIUS_KM))
    times = tracks["time"].values
    finite_times = [t for t in np.ravel(times) if _present(t)]
    ref = min(finite_times) if finite_times else None
    lo_h = None if start is None else _hours(np.array([start]), ref)[0]
    hi_h = None if end is None else _hours(np.array([end]), ref)[0]
    hits = np.zeros((len(members), LA.size), bool)
    index = {m: k for k, m in enumerate(members)}
    for k in range(tracks.sizes["track"]):
        if track_member[k] not in index:
            continue
        hours = _hours(times[k], ref) if ref is not None else np.full(times.shape[1], np.nan)
        dlat, dlon, dh = densify(
            tracks["latitude"].values[k].astype(float),
            tracks["longitude"].values[k].astype(float),
            hours,
            radius_km / 4,
        )
        keep = np.ones(dh.size, bool)
        if lo_h is not None:
            keep &= dh >= lo_h - 1e-6
        if hi_h is not None:
            keep &= dh <= hi_h + 1e-6
        if not keep.any():
            continue
        for cells in tree.query_ball_point(_unit_vectors(dlat[keep], dlon[keep]), chord):
            hits[index[track_member[k]], cells] = True
    prob = 100.0 * hits.mean(axis=0).reshape(LA.shape)
    return prob, len(members)


def _parse_time(value, like):
    """A --start/--end string as the tracks' time type."""
    import numpy as np
    import pandas as pd

    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except ValueError as exc:
        raise UsageError(f"cannot read time {value!r}; use YYYY-MM-DD or YYYY-MM-DDTHH") from exc
    sample = next((t for t in np.ravel(like) if _present(t)), None)
    if sample is not None and not isinstance(sample, np.datetime64):
        return type(sample)(ts.year, ts.month, ts.day, ts.hour)
    return np.datetime64(ts.to_datetime64(), "ns")


@weather_skill(name="tc-strike-probability", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i", "--input", type=Dataset("any"), required=True, dest="ds", help="tc-track output Zarr."
)
@weather_skill.argument("--bbox", default=None, help="N/W/S/E of the output grid.")
@weather_skill.argument(
    "--resolution",
    type=float,
    default=0.5,
    help="Degrees between output grid points with --bbox (default 0.5).",
)
@weather_skill.argument(
    "--like",
    type=Dataset("spatial"),
    default=None,
    help="Zarr whose lat/lon grid the output uses, instead of --bbox.",
)
@weather_skill.argument(
    "--radius", type=float, default=120.0, help="Strike radius in km (default 120, as ECMWF)."
)
@weather_skill.argument("--start", default=None, help="Count track points from this time on.")
@weather_skill.argument("--end", default=None, help="Count track points up to this time.")
def tc_strike_probability(ds, bbox, resolution, like, radius, start, end, output, **kwargs):
    """Map the chance that a tropical cyclone passes within a radius, from ensemble tc-track tracks."""
    import numpy as np
    import xarray as xr
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.units import dequantify_dataset

    tracks = dequantify_dataset(ds)
    if not {"track", "obs"} <= set(tracks.dims) or "latitude" not in tracks.coords:
        raise UsageError("--input is not tc-track output (no track/obs dims)")
    if (bbox is None) == (like is None):
        raise UsageError("pass exactly one of --bbox and --like")
    if radius <= 0 or resolution <= 0:
        raise UsageError("--radius and --resolution must be positive")
    if "forecast_reference_time" in tracks.coords:
        inits = np.unique(tracks["forecast_reference_time"].values)
        if inits.size > 1:
            raise UsageError(f"tracks hold {inits.size} inits; select one forecast first")
    if bbox is not None:
        north, west, south, east = (float(v) for v in bbox)
        if east < west:
            east += 360.0
        lat = np.arange(south, north + resolution / 2, resolution)
        lon = np.arange(west, east + resolution / 2, resolution)
        lon = ((lon + 180.0) % 360.0) - 180.0
    else:
        lat_dim, lon_dim = detect_spatial_dims(like)
        lat = like[lat_dim].values.astype(float)
        lon = like[lon_dim].values.astype(float)
    times = tracks["time"].values
    t0, t1 = _parse_time(start, times), _parse_time(end, times)
    prob, n_members = strike_probability(tracks, lat, lon, radius, t0, t1)

    out = xr.Dataset(
        {
            "strike_probability": (
                ("latitude", "longitude"),
                prob,
                {
                    "units": "%",
                    "long_name": f"probability that a tropical cyclone centre passes within {radius:g} km",
                },
            )
        },
        coords={
            "latitude": (
                "latitude",
                lat,
                {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"},
            ),
            "longitude": (
                "longitude",
                lon,
                {"standard_name": "longitude", "units": "degrees_east", "axis": "X"},
            ),
        },
    )
    if bbox is not None and np.any(np.diff(lon) < 0):
        out = out.sortby("longitude")
    out.attrs = {
        "strike_radius_km": radius,
        "strike_members": n_members,
        "strike_window": f"{start or 'first'} to {end or 'last'}",
        "Conventions": "CF-1.13",
    }
    for key in ("forecast_reference_time", "tc_preset", "weather_skills_source"):
        if key in tracks.attrs:
            out.attrs[key] = tracks.attrs[key]
    # Written here, not by the decorator: it would copy the tracks' trajectory attrs onto
    # the grid. Returning the path lets the decorator stamp the provenance chain.
    import shutil
    from pathlib import Path

    output = Path(output)
    if output.exists():
        shutil.rmtree(output)
    out.to_zarr(output, mode="w", consolidated=True)
    return output


if __name__ == "__main__":
    tc_strike_probability()
