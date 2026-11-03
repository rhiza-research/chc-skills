# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "matplotlib>=3.8",
#   "numpy",
#   "pandas",
#   "pillow",
#   "scipy",
#   "timezonefinder>=6",
#   "tzdata",
#   "xarray",
#   "zarr",
# ]
# ///
"""Render a probabilistic ensemble meteogram PNG for one point.

The layout follows milankl/prob_meteogram (MIT licence, SciPy 2019 plotting contest):
clouds on top, rain/snow likelihood rows in the middle, and a temperature fan with
windsocks at the bottom. Probabilities are shown as transparency, not as numbers.
"""

from __future__ import annotations

from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# Field -> (variable names, CF standard names), matched in that order.
FIELDS = {
    "t2m": (("t2m", "2t", "temperature_2m", "tas"), ("air_temperature",)),
    "tp": (
        ("tp", "precip", "precipitation_surface", "pr", "precipitation"),
        ("lwe_precipitation_rate", "precipitation_flux"),
    ),
    "u10": (("u10", "10u", "wind_u_10m", "uas"), ("eastward_wind",)),
    "v10": (("v10", "10v", "wind_v_10m", "vas"), ("northward_wind",)),
    "ws10": (("ws10", "si10", "10si", "wind_speed_10m", "sfcWind"), ("wind_speed",)),
    "fg10": (
        ("fg10", "10fg", "i10fg", "gust", "wind_gust_10m", "vmax_10m"),
        ("wind_speed_of_gust",),
    ),
    "tcc": (("tcc", "total_cloud_cover_atmosphere", "clct", "clt"), ("cloud_area_fraction",)),
    "lcc": (
        ("lcc", "low_cloud_cover", "cloud_cover_low", "clcl"),
        ("low_type_cloud_area_fraction",),
    ),
    "mcc": (
        ("mcc", "medium_cloud_cover", "cloud_cover_medium", "clcm"),
        ("medium_type_cloud_area_fraction",),
    ),
    "hcc": (
        ("hcc", "high_cloud_cover", "cloud_cover_high", "clch"),
        ("high_type_cloud_area_fraction",),
    ),
}
UNITS = {"t2m": "degree_Celsius", "tp": "mm day-1", "cloud": "1", "wind": "m s-1"}

RAIN_COLOR = (0.0, 0.17, 0.41)
WIND_COLOR = (0.71, 0.12, 0.12)
SKY = "lightskyblue"
SUN = "#ffe600"
# Cloud layer -> (grey, opacity) of its part of the cloud bar.
CLOUD_STYLE = {
    "hcc": ("0.93", 0.9),
    "mcc": ("0.7", 0.9),
    "lcc": ("0.45", 0.9),
    "tcc": ("0.7", 0.88),
}
BEAUFORT = (8.0, 10.8, 13.9)  # Beaufort 5, 6 and 7 lower bounds (m/s)
T_RANGE = (-5.0, 35.0)  # colour scale of the temperature background (degC)
MIN_ALPHA = 0.05  # probabilities below this are not drawn


def _droplet():
    """Raindrop marker: a circle with a pointed top, leaning as if falling in wind."""
    import numpy as np
    from matplotlib.path import Path as MPath
    from matplotlib.transforms import Affine2D

    ang = np.linspace(np.radians(45), np.radians(-225), 40)
    verts = np.c_[np.cos(ang), np.sin(ang) - 0.2]
    verts = np.vstack([verts, [0.0, np.sqrt(2) - 0.2], verts[:1]])
    return Affine2D().rotate_deg(-30).transform_path(MPath(verts, closed=True))


def _snowflake():
    """Snowflake marker (an open path: draw it with edge colours): six branched arms."""
    import numpy as np
    from matplotlib.path import Path as MPath

    verts, codes = [], []
    for k in range(6):
        a = np.radians(90 + 60 * k)
        tip = np.array([np.cos(a), np.sin(a)])
        verts += [(0, 0), tuple(tip)]
        codes += [MPath.MOVETO, MPath.LINETO]
        for side in (-1, 1):  # a small V on each arm
            b = a + side * np.radians(45)
            base = 0.55 * tip
            verts += [tuple(base), tuple(base + 0.35 * np.array([np.cos(b), np.sin(b)]))]
            codes += [MPath.MOVETO, MPath.LINETO]
    return MPath(verts, codes)


def _windsock(strength):
    """Red and white parts of a striped windsock; it droops less with ``strength`` (0, 1, 2).

    Both paths hold the pole, so matplotlib scales them alike and they line up.
    """
    import numpy as np
    from matplotlib.path import Path as MPath

    droop = np.radians((-45, -22, -5)[strength])
    d = np.array([np.cos(droop), np.sin(droop)])
    n = np.array([-d[1], d[0]])
    origin, length, segments = np.array([0.02, 0.8]), 0.95, 5

    def half_width(s):
        return 0.17 - 0.08 * s / length

    pole = [(-0.05, -1), (0.02, -1), (0.02, 1), (-0.05, 1), (-0.05, -1)]
    box = [MPath.MOVETO] + [MPath.LINETO] * 3 + [MPath.CLOSEPOLY]
    parts = {0: (list(pole), list(box)), 1: (list(pole), list(box))}
    for i in range(segments):
        s0, s1 = i * length / segments, (i + 1) * length / segments
        quad = [
            origin + s0 * d + half_width(s0) * n,
            origin + s1 * d + half_width(s1) * n,
            origin + s1 * d - half_width(s1) * n,
            origin + s0 * d - half_width(s0) * n,
        ]
        verts, codes = parts[i % 2]
        verts += [tuple(p) for p in quad] + [tuple(quad[0])]
        codes += box
    return MPath(*parts[0]), MPath(*parts[1])


def _find(ds, field):
    """Name of the data variable holding ``field`` in ``ds``, or None."""
    names, standard_names = FIELDS[field]
    for name in names:
        if name in ds.data_vars:
            return name
    for name, da in ds.data_vars.items():
        if da.attrs.get("standard_name") in standard_names:
            return name
    return None


def _latlon_names(ds):
    lat = next((n for n in ("latitude", "lat") if n in ds.coords), None)
    lon = next((n for n in ("longitude", "lon") if n in ds.coords), None)
    if lat is None or lon is None:
        raise UsageError("input has no latitude/longitude coordinates")
    return lat, lon


def _point(ds, lat, lon):
    """Nearest grid point to (lat, lon), plus its coordinates."""
    lat_name, lon_name = _latlon_names(ds)
    lons = ds[lon_name].values
    want_lon = lon % 360 if float(lons.max()) > 180 else lon
    pt = ds.sel({lat_name: lat, lon_name: want_lon}, method="nearest")
    glat, glon = float(pt[lat_name]), float(pt[lon_name])
    return pt, glat, (glon + 180) % 360 - 180


def _distance_km(lat1, lon1, lat2, lon2):
    import numpy as np

    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (
        np.sin((p2 - p1) / 2) ** 2
        + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _members(da):
    """(member, step) array of a point series; a run without members is one member."""
    import numpy as np

    extra = [d for d in da.dims if d not in ("number", "step")]
    if extra:
        return None
    if "number" not in da.dims:
        da = da.expand_dims("number")
    return np.asarray(da.transpose("number", "step").values, dtype=float)


def _step_intervals(ds, var):
    """Start and end lead of the interval each step's rate covers, as timedelta64 arrays."""
    import numpy as np
    import pandas as pd

    steps = ds["step"].values.astype("timedelta64[ns]")
    bounds_name = ds["step"].attrs.get("bounds") or "step_bounds"
    if bounds_name in ds:
        b = ds[bounds_name].transpose("step", ...).values.astype("timedelta64[ns]")
        return b[:, 0], b[:, 1]
    interval = ds[var].attrs.get("data_interval")
    if interval:
        width = pd.Timedelta(interval.replace(" ", ""))
        width = np.full(steps.shape, width.to_timedelta64())
    elif len(steps) > 1:
        diffs = np.diff(steps)
        width = np.r_[diffs, diffs[-1:]]
    else:
        width = np.full(steps.shape, np.timedelta64(1, "D"))
    return steps, steps + width.astype("timedelta64[ns]")


def _default_thresholds(hours):
    """Light/medium/heavy rain thresholds (mm per display interval)."""
    if hours <= 6:
        return (0.1, 1.0, 4.0)
    if hours <= 12:
        return (0.2, 2.0, 8.0)
    days = hours / 24
    return tuple(round(t * max(days, 1.0), 1) for t in (1.0, 5.0, 20.0))


def _sun_times(lat, lon, day):
    """Sunrise and sunset (UTC pandas Timestamps) on ``day``, or None in polar day/night.

    NOAA's general solar position equations; accurate to a minute or two.
    """
    import numpy as np
    import pandas as pd

    gamma = 2 * np.pi / 365 * (day.dayofyear - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    phi = np.radians(lat)
    cos_h = np.cos(np.radians(90.833)) / (np.cos(phi) * np.cos(decl)) - np.tan(phi) * np.tan(decl)
    if abs(cos_h) > 1:
        return None
    h = np.degrees(np.arccos(cos_h))
    midnight = pd.Timestamp(day.date())
    rise = midnight + pd.Timedelta(minutes=float(720 - 4 * (lon + h) - eqtime))
    sset = midnight + pd.Timedelta(minutes=float(720 - 4 * (lon - h) - eqtime))
    return rise, sset


def _timezone(lat, lon, utc):
    """(zoneinfo or None, label) for the point's local time."""
    if utc:
        return None, "UTC"
    import sys
    from zoneinfo import ZoneInfo

    try:
        from timezonefinder import TimezoneFinder

        name = TimezoneFinder().timezone_at(lng=lon, lat=lat)
    except Exception as exc:  # noqa: BLE001 - fall back to UTC with a note
        print(f"note: no time zone lookup ({exc}); times are UTC", file=sys.stderr)
        return None, "UTC"
    if name is None:
        print("note: no time zone at this point; times are UTC", file=sys.stderr)
        return None, "UTC"
    return ZoneInfo(name), name


def _to_local(times_utc, tz):
    """UTC pandas DatetimeIndex -> naive local DatetimeIndex."""
    if tz is None:
        return times_utc
    return times_utc.tz_localize("UTC").tz_convert(tz).tz_localize(None)


def _smooth(x, y, xs):
    """Shape-preserving interpolation of ``y`` (last axis) from ``x`` onto ``xs``."""
    from scipy.interpolate import PchipInterpolator

    if len(x) < 2:
        return y[..., :1].repeat(len(xs), axis=-1)
    return PchipInterpolator(x, y, axis=-1)(xs)


def _bin_sum(starts, ends, amounts, edges):
    """Sum per-interval amounts (member, step) into display bins split at ``edges``.

    An interval straddling a bin edge is shared in proportion to its overlap.
    """
    import numpy as np

    out = np.zeros((amounts.shape[0], len(edges) - 1))
    s, e = starts.astype("int64"), ends.astype("int64")
    ed = edges.astype("int64")
    for k in range(len(ed) - 1):
        overlap = np.clip(np.minimum(e, ed[k + 1]) - np.maximum(s, ed[k]), 0, None)
        frac = overlap / np.maximum(e - s, 1)
        out[:, k] = np.nansum(amounts * frac, axis=1)
    return out


def _bin_max(times, values, edges):
    """Maximum of (member, step) ``values`` over the steps falling in each bin."""
    import numpy as np

    out = np.full((values.shape[0], len(edges) - 1), np.nan)
    for k in range(len(edges) - 1):
        sel = (times >= edges[k]) & (times < edges[k + 1])
        if sel.any():
            out[:, k] = np.nanmax(values[:, sel], axis=1)
    return out


@weather_skill(name="plot-meteogram", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("forecast"),
    action="append",
    required=True,
    dest="ds",
    help=(
        "Forecast Zarr (number, step, scalar init time, lat/lon); repeat to take fields "
        "from several stores. Needs 2 m temperature; precipitation, 10 m wind, gusts and "
        "cloud cover are used when present."
    ),
)
@weather_skill.argument("--lat", type=float, required=True, help="Latitude of the point.")
@weather_skill.argument(
    "--lon", type=float, required=True, help="Longitude of the point (degrees east)."
)
@weather_skill.argument("--label", default=None, help="Place name for the title.")
@weather_skill.argument(
    "--max-lead",
    default="10D",
    help="Last lead to plot (pandas timedelta: 168h, 10D). Default 10D.",
)
@weather_skill.argument(
    "--utc", action="store_true", help="Show UTC instead of the point's local time."
)
@weather_skill.argument(
    "--precip-interval",
    default=None,
    help="Length of one rain column (pandas timedelta). Default: 6h, or the data's step if longer.",
)
@weather_skill.argument(
    "--precip-thresholds",
    default=None,
    help="Light,medium,heavy rain thresholds in mm per --precip-interval (default by interval).",
)
@weather_skill.argument("--no-clouds", action="store_true", help="Leave out the cloud panel.")
@weather_skill.argument("--no-wind", action="store_true", help="Leave out the windsocks.")
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_meteogram(
    ds,
    lat,
    lon,
    label,
    max_lead,
    utc,
    precip_interval,
    precip_thresholds,
    no_clouds,
    no_wind,
    title,
    output,
    **kwargs,
):
    """Render a probabilistic ensemble meteogram PNG for one point.

    Three stacked panels: cloud cover (ensemble median, grey bands over a day/night sky),
    rain or snow in light/medium/heavy rows whose opacity is the share of members above
    each threshold, and a 2 m temperature fan whose colour fades out where few members
    are, with windsocks for the chance of 10 m wind of Beaufort 5 or more. Layout after
    milankl/prob_meteogram (MIT).
    """
    import sys

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from weather_skills_core.units import convert_dataarray, dequantify_dataset, units_convertible

    if not -90 <= lat <= 90:
        raise UsageError("--lat must be within -90..90")
    try:
        lead = pd.Timedelta(max_lead).to_timedelta64()
    except ValueError as exc:
        raise UsageError(f"--max-lead {max_lead!r}: use a timedelta such as 168h or 10D") from exc

    # --- pick each field from the first input that has it, at the nearest grid point
    datasets = [dequantify_dataset(item) for item in (ds if isinstance(ds, list) else [ds])]
    found = {}
    grid = None
    for item in datasets:
        if "step" not in item.dims:
            raise UsageError("input has no step dimension; pass forecast Zarrs")
        item = item.sel(step=item["step"] <= lead)
        if item.sizes["step"] == 0:
            raise UsageError(f"no steps within --max-lead {max_lead}")
        pt = None
        for field in FIELDS:
            if field in found:
                continue
            name = _find(item, field)
            if name is None:
                continue
            if pt is None:
                pt, glat, glon = _point(item, lat, lon)
                grid = grid or (glat, glon)
            values = pt[name]
            if field == "tp":
                tp_units = item[name].attrs.get("units", "")
                if not tp_units or not units_convertible(tp_units, UNITS["tp"]):
                    raise UsageError(
                        f"{name} is in {tp_units or 'no units'}, not a rate; the fetchers "
                        "write mm day-1 (run deaccumulate on a cumulative archive)"
                    )
            target = {
                "t2m": UNITS["t2m"],
                "tp": UNITS["tp"],
                "u10": UNITS["wind"],
                "v10": UNITS["wind"],
                "ws10": UNITS["wind"],
                "fg10": UNITS["wind"],
            }.get(field, UNITS["cloud"])
            if values.attrs.get("units"):
                values, _ = convert_dataarray(values, target)
            arr = _members(values)
            if arr is None:
                print(f"note: {name} has extra dims {values.dims}; skipped", file=sys.stderr)
                continue
            init = pd.Timestamp(np.asarray(item["time"].values).ravel()[0])
            entry = {
                "name": name,
                "values": arr,
                "valid": pd.DatetimeIndex(init + pd.to_timedelta(item["step"].values)),
                "init": init,
            }
            if field == "tp":
                entry["start"], entry["end"] = _step_intervals(item, name)
                entry["start"] = pd.DatetimeIndex(init + pd.to_timedelta(entry["start"]))
                entry["end"] = pd.DatetimeIndex(init + pd.to_timedelta(entry["end"]))
            found[field] = entry

    if "t2m" not in found:
        raise UsageError(
            "no 2 m temperature in the inputs (looked for t2m, temperature_2m, tas or "
            "standard_name air_temperature)"
        )
    glat, glon = grid
    dist = _distance_km(lat, lon, glat, glon)
    print(
        f"grid point {glat:.2f}, {glon:.2f} ({dist:.0f} km from {lat:.2f}, {lon:.2f})",
        file=sys.stderr,
    )
    if "ws10" not in found and {"u10", "v10"} <= found.keys():
        u, v = found["u10"], found["v10"]
        if u["values"].shape == v["values"].shape:
            found["ws10"] = {
                **u,
                "name": "wind speed",
                "values": np.hypot(u["values"], v["values"]),
            }
    for field, what in (("tp", "rain panel"), ("ws10", "windsocks")):
        if field not in found:
            print(
                f"note: no {'precipitation' if field == 'tp' else '10 m wind'}; no {what}",
                file=sys.stderr,
            )
    clouds = [f for f in ("hcc", "mcc", "lcc") if f in found]
    if not clouds and "tcc" in found:
        clouds = ["tcc"]
    if not clouds and not no_clouds:
        print("note: no cloud cover; no cloud panel", file=sys.stderr)
    show_clouds = bool(clouds) and not no_clouds
    show_rain = "tp" in found
    show_wind = "ws10" in found and not no_wind

    tz, tz_label = _timezone(lat, lon, utc)
    temp = found["t2m"]
    t_valid = temp["valid"]
    t_local = _to_local(t_valid, tz)
    x0, x1 = t_local[0], t_local[-1]

    # Display bins for rain and wind: --precip-interval, else 6 h or the native step.
    native = (
        pd.Timedelta(np.median(np.diff(t_valid.values))) if len(t_valid) > 1 else pd.Timedelta("1D")
    )
    if precip_interval:
        try:
            bin_len = pd.Timedelta(precip_interval)
        except ValueError as exc:
            raise UsageError(
                f"--precip-interval {precip_interval!r}: use a timedelta such as 6h"
            ) from exc
        if bin_len <= pd.Timedelta(0):
            raise UsageError("--precip-interval must be positive")
    else:
        bin_len = max(pd.Timedelta("6h"), native)
    hours = bin_len / pd.Timedelta("1h")
    if precip_thresholds:
        try:
            thresholds = tuple(float(t) for t in precip_thresholds.split(","))
        except ValueError as exc:
            raise UsageError("--precip-thresholds: use three numbers, e.g. 0.1,1,4") from exc
        if len(thresholds) != 3 or not thresholds[0] < thresholds[1] < thresholds[2]:
            raise UsageError("--precip-thresholds needs three increasing numbers")
    else:
        thresholds = _default_thresholds(hours)

    # --- figure
    panels = [p for p, on in (("clouds", show_clouds), ("rain", show_rain), ("temp", True)) if on]
    heights = {"clouds": 0.8, "rain": 1.4, "temp": 4.2}
    fig, axes = plt.subplots(
        len(panels),
        1,
        sharex=True,
        figsize=(12, 1.4 + 0.62 * sum(heights[p] for p in panels)),
        gridspec_kw={"height_ratios": [heights[p] for p in panels], "hspace": 0.0},
        squeeze=False,
    )
    fig.subplots_adjust(left=0.07, right=0.8 if show_rain else 0.97, top=0.9, bottom=0.14)
    ax = dict(zip(panels, axes[:, 0], strict=True))
    fine = pd.date_range(x0, x1, periods=400)
    fine_num = mdates.date2num(fine)
    t_num = mdates.date2num(t_local)

    days = pd.date_range(x0.normalize(), x1.normalize() + pd.Timedelta("1D"), freq="D")
    nights = []
    for day in days:
        sun = _sun_times(lat, lon, day)
        if sun is None:
            continue
        rise, sset = (_to_local(pd.DatetimeIndex([s]), tz)[0] for s in sun)
        nights.append((rise, sset))

    # Clouds: one bar, centred in the sky, whose height is the median total cover; it is
    # split low (bottom) / mid / high (top) in proportion to each layer's median cover.
    if show_clouds:
        a = ax["clouds"]
        a.set_facecolor(SKY)
        noons = [rise + (sset - rise) / 2 for rise, sset in nights]
        noons = [t for t in noons if x0 <= t <= x1]
        if noons:
            a.scatter(noons, np.full(len(noons), 0.5), s=170, color=SUN, lw=0, zorder=1)

        def cover(field):
            entry = found[field]
            med = np.clip(np.nanmedian(entry["values"], axis=0), 0, 1)
            x = mdates.date2num(_to_local(entry["valid"], tz))
            return np.clip(_smooth(x, med, fine_num), 0, 1)

        cloud_fields = [f for f in ("lcc", "mcc", "hcc") if f in found]
        if cloud_fields:
            parts = np.array([cover(f) for f in cloud_fields])
            # Without a total, assume the layers overlap at random.
            total = cover("tcc") if "tcc" in found else 1 - np.prod(1 - parts, axis=0)
            sums = parts.sum(axis=0)
            shares = parts / np.where(sums > 0, sums, 1)
        else:
            cloud_fields, total, shares = ["tcc"], cover("tcc"), np.ones((1, fine.size))
        height = 0.84 * total
        base = 0.5 - height / 2
        for field, share in zip(cloud_fields, shares, strict=True):
            top = base + height * share
            grey, opacity = CLOUD_STYLE[field]
            a.fill_between(fine, base, top, color=grey, alpha=opacity, lw=0, zorder=2)
            base = top
        if cloud_fields != ["tcc"]:
            for row, field in enumerate(cloud_fields):  # key: low at the bottom, like the bar
                y = 0.2 + 0.3 * row
                a.scatter(
                    [1.012],
                    [y],
                    s=40,
                    marker="s",
                    color=CLOUD_STYLE[field][0],
                    edgecolors="0.4",
                    lw=0.5,
                    transform=a.transAxes,
                    clip_on=False,
                )
                a.text(
                    1.024,
                    y,
                    {"lcc": "low", "mcc": "mid", "hcc": "high"}[field],
                    transform=a.transAxes,
                    fontsize=8,
                    va="center",
                )
        a.set_ylim(0, 1)
        a.set_yticks([])

    # Bins line up with midnight UTC when the bin divides a day.
    start = (
        t_valid[0].floor(bin_len) if pd.Timedelta("1D") % bin_len == pd.Timedelta(0) else t_valid[0]
    )
    n_bins = max(int(np.ceil((t_valid[-1] - start) / bin_len)), 1)
    edges = pd.DatetimeIndex([start + k * bin_len for k in range(n_bins + 1)])
    centres_local = _to_local(edges[:-1] + bin_len / 2, tz)

    # Rain: one row per threshold, opacity = share of members above it.
    if show_rain:
        a = ax["rain"]
        rain = found["tp"]
        amounts = rain["values"] * ((rain["end"] - rain["start"]) / pd.Timedelta("1D")).values
        binned = _bin_sum(rain["start"].values, rain["end"].values, amounts, edges.values)
        t_med = np.nanmedian(temp["values"], axis=0)
        t_bin = np.array(
            [
                np.nanmedian(t_med[(t_valid >= edges[k]) & (t_valid < edges[k + 1])])
                if ((t_valid >= edges[k]) & (t_valid < edges[k + 1])).any()
                else np.nan
                for k in range(len(edges) - 1)
            ]
        )
        snow = t_bin < 0
        drop, flake = _droplet(), _snowflake()

        def precip_marker(x, y, alpha, is_snow, transform=None, size=1.0):
            color = (*RAIN_COLOR, 1.0)
            colors = np.tile(color, (len(np.atleast_1d(x)), 1))
            colors[:, 3] = alpha
            kw = {"zorder": 3, "clip_on": transform is None}
            if transform is not None:
                kw["transform"] = transform
            if is_snow:
                a.scatter(
                    x,
                    y,
                    s=75 * size,
                    marker=flake,
                    facecolors="none",
                    edgecolors=colors,
                    linewidths=1.1,
                    **kw,
                )
            else:
                a.scatter(x, y, s=75 * size, marker=drop, c=colors, lw=0, **kw)

        for row, thr in enumerate(thresholds):
            prob = (binned >= thr).mean(axis=0)
            keep = prob >= MIN_ALPHA
            for is_snow in (False, True):
                sel = keep & (snow == is_snow)
                if sel.any():
                    precip_marker(centres_local[sel], np.full(sel.sum(), row), prob[sel], is_snow)
        a.set_ylim(-0.6, 2.6)
        a.set_yticks([])
        span = f"{hours:g} h" if hours < 48 else f"{hours / 24:g} days"
        a.set_ylabel(f"per {span}", fontsize=8)
        right = matplotlib.transforms.blended_transform_factory(a.transAxes, a.transData)
        for row, (name, thr) in enumerate(
            zip(("light", "medium", "heavy"), thresholds, strict=True)
        ):
            a.text(1.004, row, f"▸ {name} ≥{thr:g} mm", transform=right, fontsize=8, va="center")
        # Key: dark = very likely, faint = less likely.
        a.text(
            1.145,
            0.97,
            "snow or\nrainfall",
            transform=a.transAxes,
            fontsize=8,
            fontweight="bold",
            va="top",
        )
        for y, alpha, text in ((0.42, 1.0, "very likely"), (0.12, 0.3, "less likely")):
            precip_marker([1.152], [y], alpha, False, transform=a.transAxes)
            precip_marker([1.166], [y], alpha, True, transform=a.transAxes, size=0.9)
            a.text(1.178, y, text, transform=a.transAxes, fontsize=8, va="center")

    # Windsocks per bin: (bin, strength 0-2 from the 75th percentile of the members' peaks).
    socks = []
    if show_wind:
        wind = found["ws10"]
        peak = _bin_max(wind["valid"], wind["values"], edges)
        prob = np.mean(peak >= BEAUFORT[0], axis=0)
        p75 = np.nanpercentile(np.where(np.isnan(peak), 0, peak), 75, axis=0)
        socks = [
            (k, min(max(int(np.searchsorted(BEAUFORT, p75[k], side="right")) - 1, 0), 2))
            for k in np.flatnonzero(prob >= MIN_ALPHA)
        ]
    # The windsock strip under the fan is only made when there is something to put in it.
    strip = show_wind and (bool(socks) or "fg10" in found)

    # Temperature: coloured background, faded with white from the outermost members in.
    a = ax["temp"]
    members = np.sort(temp["values"], axis=0)
    sm = _smooth(t_num, members, fine_num)
    lo, hi = float(np.nanmin(sm)), float(np.nanmax(sm))
    pad = max(1.0, 0.08 * (hi - lo))
    ymin = lo - pad - (0.22 * (hi - lo + 2 * pad) if strip else 0)
    ymax = hi + 2.2 * pad  # room for the sunrise label
    n = sm.shape[0]
    if n == 1:  # a single run: a thick line coloured by temperature
        from matplotlib.collections import LineCollection

        pts = np.c_[fine_num, sm[0]]
        line = LineCollection(
            np.stack([pts[:-1], pts[1:]], axis=1),
            cmap="turbo",
            norm=matplotlib.colors.Normalize(*T_RANGE),
            linewidths=3.5,
            capstyle="round",
            zorder=2,
        )
        line.set_array((sm[0][:-1] + sm[0][1:]) / 2)
        a.add_collection(line)
    else:
        gradient = np.linspace(ymin, ymax, 256)[:, None]
        a.imshow(
            gradient,
            aspect="auto",
            cmap="turbo",
            origin="lower",
            vmin=T_RANGE[0],
            vmax=T_RANGE[1],
            extent=(fine_num[0], fine_num[-1], ymin, ymax),
            zorder=0,
        )
        a.fill_between(fine, ymin, sm[0], color="white", lw=0, zorder=1)
        a.fill_between(fine, sm[-1], ymax, color="white", lw=0, zorder=1)
        layers = n // 2
        alpha = 1 - 0.1 ** (1 / layers)  # the outer layers add up to 90 % white
        for k in range(layers):
            a.fill_between(fine, ymin, sm[k], color="white", alpha=alpha, lw=0, zorder=1)
            a.fill_between(fine, sm[n - 1 - k], ymax, color="white", alpha=alpha, lw=0, zorder=1)
    med = np.median(sm, axis=0)
    local_days = pd.Series(med, index=fine)
    for _day, vals in local_days.groupby(local_days.index.normalize()):
        if len(vals) < 20:
            continue
        a.annotate(
            f"{vals.max():.0f}°",
            (vals.idxmax(), vals.max()),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            zorder=3,
        )
        a.annotate(
            f"{vals.min():.0f}°",
            (vals.idxmin(), vals.min()),
            xytext=(0, -11),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="0.3",
            zorder=3,
        )
    a.set_ylim(ymin, ymax)
    if strip:  # no temperature ticks in the windsock strip
        a.set_yticks([t for t in a.get_yticks() if lo - pad <= t <= ymax])
        a.axhline(lo - pad, color="0.6", lw=0.6, zorder=2)
    a.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%g°C"))
    a.tick_params(axis="y", labelsize=8)
    a.grid(axis="y", color="0.8", lw=0.5)
    a.set_axisbelow(False)  # grid lines show over the white fades
    if nights:
        rise, sset = nights[0]
        a.text(
            0.005,
            0.98,
            f"☼ ↑{rise:%H:%M} ↓{sset:%H:%M}",
            transform=a.transAxes,
            fontsize=9,
            va="top",
            zorder=6,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1},
        )

    # Wind: windsocks, opacity = share of members reaching Beaufort 5.
    if show_wind:
        y = ymin + 0.4 * (lo - pad - ymin)
        for k, strength in socks:
            red, white = _windsock(strength)
            a.scatter(
                centres_local[k],
                y,
                marker=white,
                s=900,
                color=(1, 1, 1, prob[k]),
                edgecolors=[(*WIND_COLOR, prob[k])],
                lw=0.4,
                zorder=4,
            )
            a.scatter(
                centres_local[k], y, marker=red, s=900, color=(*WIND_COLOR, prob[k]), lw=0, zorder=4
            )
        legend = (
            "windsocks: chance of Bft 5+ (8 m/s); straighter = Bft 6/7"
            if socks
            else "no member reaches Bft 5 (8 m/s) mean wind"
        )
        if "fg10" in found:
            gust = found["fg10"]
            g = pd.DataFrame(gust["values"].T, index=_to_local(gust["valid"], tz))
            daily = g.groupby(g.index.normalize()).max().median(axis=1)
            for day, val in daily.items():
                if day + pd.Timedelta("12h") <= x1 and np.isfinite(val):
                    a.text(
                        day + pd.Timedelta("12h"),
                        ymin + 0.82 * (lo - pad - ymin),
                        f"gust {val:.0f}",
                        ha="center",
                        fontsize=7,
                        color=WIND_COLOR,
                        zorder=4,
                    )
            legend += "; gust = median daily max (m/s)"
        a.text(
            0.995,
            0.015,
            legend,
            transform=a.transAxes,
            ha="right",
            fontsize=7,
            color=WIND_COLOR,
            zorder=4,
        )

    # Time axis: midnight lines, weekday labels at noon (weekends in blue).
    span_days = (x1 - x0) / pd.Timedelta("1D")
    every = max(1, int(np.ceil(span_days / 15)))
    for a in axes[:, 0]:
        for day in days:
            if x0 <= day <= x1:
                a.axvline(day, color="0.55", lw=0.6, zorder=5)
        a.set_xlim(x0, x1)
        a.tick_params(axis="x", which="both", length=0)
    bottom = axes[-1, 0]
    bottom.set_xticks([])
    hour_labels = span_days <= 10
    if hour_labels:
        bottom.set_xticks(
            [t for t in pd.date_range(x0.ceil("6h"), x1, freq="6h") if t.hour], minor=True
        )
        bottom.xaxis.set_minor_formatter(mdates.DateFormatter("%Hh"))
        bottom.tick_params(axis="x", which="minor", labelsize=7, pad=2)
    for k, day in enumerate(days):
        noon = day + pd.Timedelta("12h")
        if k % every or not (x0 <= noon <= x1):
            continue
        bottom.text(
            day + pd.Timedelta("1h") if hour_labels else noon,
            -0.06 if hour_labels else -0.02,
            f"{noon:%a}\n{noon:%d %b}",
            transform=bottom.get_xaxis_transform(),
            ha="left" if hour_labels else "center",
            va="top",
            fontsize=9,
            color="tab:blue" if noon.weekday() >= 5 else "k",
        )

    init = temp["init"]
    place = label or "Meteogram"
    ns, ew = ("S" if lat < 0 else "N"), ("W" if lon < 0 else "E")
    left = axes[0, 0].get_position().x0
    fig.text(
        left,
        0.985,
        title
        or f"{'Meteogram ' if label else ''}{place} ({abs(lat):.1f}°{ns}, {abs(lon):.1f}°{ew})",
        fontsize=13,
        fontweight="bold",
        va="top",
    )
    attrs = datasets[0].attrs
    source = attrs.get("name") or attrs.get("weather_skills_source", "").split(":")[0]
    fig.text(
        axes[0, 0].get_position().x1,
        0.985,
        f"{source + ', ' if source else ''}init {init:%Y-%m-%d %H} UTC, "
        + (
            "single run (no ensemble)"
            if temp["values"].shape[0] == 1
            else f"{temp['values'].shape[0]} members"
        ),
        fontsize=8,
        color="0.35",
        ha="right",
        va="top",
    )
    fig.text(
        0.01,
        0.005,
        f"grid point {glat:.2f}, {glon:.2f} ({dist:.0f} km away); times {tz_label}. "
        "Layout after milankl/prob_meteogram (MIT).",
        fontsize=7,
        color="0.35",
    )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=130)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_meteogram()
