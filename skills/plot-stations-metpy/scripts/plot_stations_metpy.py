# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "matplotlib>=3.8",
#   "numpy",
#   "pyproj",
#   "xarray",
# ]
# ///
"""Draw a station-model map (MetPy symbols and layout) of station observations at one time."""

from __future__ import annotations

import json
import sys
import warnings
from importlib.resources import files
from pathlib import Path

import numpy as np
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import names_for
from weather_skills_core.units import dequantify_dataset

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

# role -> (CF standard names, variable names), tried in that order.
ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "temperature": (
        ("air_temperature",),
        ("air_temperature", "t2m", "tavg", "temperature", "tas", "tmax", "te"),
    ),
    "dewpoint": (("dew_point_temperature",), ("dew_point_temperature", "d2m", "dewpoint", "td")),
    "pressure": (
        ("air_pressure_at_mean_sea_level", "air_pressure_at_sea_level"),
        ("qnh", "msl", "mslp", "pressure_at_sea_level", "slp"),
    ),
    "u": (("eastward_wind",), ("u", "u10", "eastward_wind")),
    "v": (("northward_wind",), ("v", "v10", "northward_wind")),
    "speed": (("wind_speed",), ("wind_speed", "ws", "si10", "wind_speed_10m")),
    "direction": (("wind_from_direction",), ("wind_from_direction", "wind_direction", "wd")),
    "cloud_cover": ((), ("cloud_cover", "cloud_cover_okta")),
    "weather_code": ((), ("present_weather_code",)),
}


def _match(ds, role, given):
    if role in given:
        if given[role] not in ds.data_vars:
            raise UsageError(f"--var {role}={given[role]}: not in the input")
        return given[role]
    standard_names, names = ROLES[role]
    by_std = [
        n for n, da in ds.data_vars.items() if da.attrs.get("standard_name") in standard_names
    ]
    found = list(dict.fromkeys(by_std or [n for n in names if n in ds.data_vars]))
    return found[0] if found else None


def _as(da, units, mu):
    src = da.attrs.get("units")
    if not src:
        raise UsageError(f"{da.name} has no units attr")
    try:
        return mu.Quantity(np.asarray(da.values, dtype="float64"), src).to(units).magnitude
    except Exception as exc:
        raise UsageError(f"{da.name} units {src!r} do not convert to {units}: {exc}") from exc


def _borders(ax, extent):
    """Country outlines from core's bundled Natural Earth countries."""
    w, e, s, n = extent
    data = json.loads(files("weather_skills_core.data").joinpath("countries.geojson").read_text())
    for feature in data["features"]:
        geom = feature.get("geometry") or {}
        polys = (
            [geom["coordinates"]]
            if geom.get("type") == "Polygon"
            else geom.get("coordinates", [])
            if geom.get("type") == "MultiPolygon"
            else []
        )
        for poly in polys:
            for ring in poly:
                xy = np.asarray(ring, dtype=float)
                if (
                    xy[:, 0].max() < w
                    or xy[:, 0].min() > e
                    or xy[:, 1].max() < s
                    or xy[:, 1].min() > n
                ):
                    continue
                ax.plot(xy[:, 0], xy[:, 1], color="0.55", lw=0.7, zorder=1)


@weather_skill(name="plot-stations-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("point_obs"),
    required=True,
    dest="ds",
    help="Station Zarr (point_id or station_id, time): metar-fetch, ghcn-daily-fetch, tahmo-fetch, ...",
)
@weather_skill.argument(
    "--time",
    help="Time to plot (nearest, e.g. 2026-04-15T12:00). Default: the latest time with reports.",
)
@weather_skill.argument("--bbox", help="Map extent N/W/S/E. Default: the stations plus a margin.")
@weather_skill.argument(
    "--min-spacing",
    type=float,
    default=0.0,
    help="Thin stations closer than this (km), keeping those with the most data. Default: 0.",
)
@weather_skill.argument("--labels", action="store_true", help="Write each station's id under it.")
@weather_skill.argument(
    "--var",
    action="append",
    default=[],
    metavar="ROLE=NAME",
    help=f"Use variable NAME for ROLE ({', '.join(ROLES)}).",
)
@weather_skill.argument("--title", default=None, help="Figure title (default: the time).")
def plot_stations(ds, time, bbox, min_spacing, labels, var, title, output, **kwargs):
    """Draw a station-model map (MetPy symbols and layout) of station observations at one time.

    Each station shows temperature (upper left), dewpoint (lower left),
    sea-level pressure in the coded three digits (upper right), a wind barb
    in knots, cloud cover in the circle and the present-weather symbol on the
    left, as far as the data has them, over country borders.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import metpy
    from metpy.calc import reduce_point_density, wind_components
    from metpy.plots import current_weather, sky_cover
    from metpy.plots.wx_symbols import wx_symbol_font
    from metpy.units import units as mu
    from pyproj import Transformer

    given = {}
    for item in var:
        role, sep, name = item.partition("=")
        if not sep or role not in ROLES or not name:
            raise UsageError(
                f"--var {item!r} must be ROLE=NAME with ROLE one of {', '.join(ROLES)}"
            )
        given[role] = name

    ds = dequantify_dataset(ds)
    sdim = next((d for d in names_for("point_id") if d in ds.dims), None)
    if sdim is None:
        raise UsageError("input must be station data with a point_id or station_id dim")
    other = [d for d in ds.dims if d not in (sdim, "time")]
    if other:
        raise UsageError(
            f"reduce {', '.join(other)} first (e.g. select one member); dims: {dict(ds.sizes)}"
        )
    roles = {r: _match(ds, r, given) for r in ROLES}
    fields = [n for n in roles.values() if n]
    if not fields:
        raise UsageError(f"no plottable variables found; variables: {', '.join(ds.data_vars)}")

    if "time" in ds.dims:
        if time:
            try:
                ds = ds.sel(time=np.datetime64(time), method="nearest")
            except (ValueError, TypeError) as exc:
                raise UsageError(f"--time {time!r}: {exc}") from exc
        else:
            have = ds[fields].to_array().notnull().any(("variable", sdim))
            if not bool(have.any()):
                raise UsageError("no reports at any time")
            ds = ds.isel(time=int(np.flatnonzero(have.values)[-1]))
    when = ""
    if "time" in ds.coords and ds["time"].ndim == 0:
        when = np.datetime_as_string(ds["time"].values, unit="m").replace("T", " ") + " UTC"

    lat = np.asarray(ds["latitude"].values, dtype=float)
    lon = np.asarray(ds["longitude"].values, dtype=float)
    present = ds[fields].to_array().notnull().sum("variable").values
    keep = np.isfinite(lat) & np.isfinite(lon) & (present > 0)
    if bbox is not None:
        n, w, s, e = bbox
        keep &= (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
    if not keep.any():
        raise UsageError(f"no station has data at {when or 'this time'} inside the map")
    if min_spacing > 0:
        to_xy = Transformer.from_crs(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={np.nanmean(lat[keep])} +lon_0={np.nanmean(lon[keep])} +units=m",
            always_xy=True,
        )
        x, y = to_xy.transform(lon, lat)
        pts = np.column_stack([np.where(keep, x, np.nan), np.where(keep, y, np.nan)])
        pts[~keep] = 1e12  # far away: never thins a kept station
        thin = reduce_point_density(pts, min_spacing * 1000.0, priority=present.astype(float))
        keep &= thin
    sel = ds.isel({sdim: np.flatnonzero(keep)})
    lat, lon = lat[keep], lon[keep]

    if bbox is not None:
        n, w, s, e = bbox
    else:
        pad = max(0.5, 0.08 * max(np.ptp(lat), np.ptp(lon)))
        n, w, s, e = lat.max() + pad, lon.min() - pad, lat.min() - pad, lon.max() + pad
    aspect = 1.0 / np.cos(np.radians((n + s) / 2))
    width = 11.0
    height = float(np.clip(width * (n - s) * aspect / (e - w), 5, 14))
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(aspect)
    _borders(ax, (w, e, s, n))
    ax.grid(color="0.9", lw=0.5, zorder=0)
    ax.set_xlabel("longitude (°E)")
    ax.set_ylabel("latitude (°N)")

    # The station model in MetPy's layout, symbols and symbol font, drawn with plain
    # annotations: MetPy 1.7's StationPlot text does not draw on Matplotlib 3.11.
    spacing = 11.0  # points per layout step, as StationPlot

    def put(where, texts, **style):
        dx, dy = where
        for x0, y0, text in zip(lon, lat, texts, strict=True):
            if text:
                ax.annotate(
                    text,
                    (x0, y0),
                    xytext=(dx * spacing, dy * spacing),
                    textcoords="offset points",
                    ha="center",
                    va="center",
                    annotation_clip=True,
                    zorder=4,
                    **style,
                )

    def numbers(values, fmt):
        return [fmt(v) if np.isfinite(v) else "" for v in values]

    shown = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if roles["temperature"]:
            t = _as(sel[roles["temperature"]], "degC", mu)
            put((-1, 1), numbers(t, lambda v: f"{v:.0f}"), color="tab:red", fontsize=9)
            shown.append("temperature (°C, upper left)")
        if roles["dewpoint"]:
            td = _as(sel[roles["dewpoint"]], "degC", mu)
            put((-1, -1), numbers(td, lambda v: f"{v:.0f}"), color="tab:green", fontsize=9)
            shown.append("dewpoint (°C, lower left)")
        if roles["pressure"]:
            p = _as(sel[roles["pressure"]], "hPa", mu)
            put((1, 1), numbers(p, lambda v: format(10 * v, ".0f")[-3:]), fontsize=9)
            shown.append("pressure (last 3 digits of tenths of hPa, upper right)")
        u = v = None
        if roles["u"] and roles["v"]:
            u, v = _as(sel[roles["u"]], "knot", mu), _as(sel[roles["v"]], "knot", mu)
        elif roles["speed"] and roles["direction"]:
            spd = _as(sel[roles["speed"]], "knot", mu)
            drc = _as(sel[roles["direction"]], "degree", mu)
            uq, vq = wind_components(spd * mu.knot, drc * mu.degree)
            u, v = uq.m_as("knot"), vq.m_as("knot")
        if u is not None:
            calm = np.hypot(u, v) < 0.5
            moving = ~calm & np.isfinite(u) & np.isfinite(v)
            ax.barbs(lon[moving], lat[moving], u[moving], v[moving], length=6.5, zorder=3)
            # Calm: the standard circle around the station symbol.
            ax.scatter(
                lon[calm], lat[calm], s=260, facecolor="none", edgecolor="black", lw=0.9, zorder=5
            )
            shown.append("wind barb (kt; ring = calm)")
        if roles["cloud_cover"]:
            cc = np.asarray(sel[roles["cloud_cover"]].values, dtype=float)
            codes = np.where(np.isfinite(cc), cc, 10).astype(int)
            put(
                (0, 0),
                [sky_cover(int(c)) for c in codes],
                fontproperties=wx_symbol_font,
                fontsize=13,
            )
            shown.append("cloud cover (oktas, circle)")
        else:
            ax.scatter(lon, lat, s=14, facecolor="white", edgecolor="black", zorder=3)
        if roles["weather_code"]:
            wx = np.asarray(sel[roles["weather_code"]].values, dtype=float)
            codes = np.where(np.isfinite(wx), wx, 0).astype(int)
            put(
                (-2, 0),
                [current_weather(int(c)) for c in codes],
                fontproperties=wx_symbol_font,
                fontsize=13,
            )
            shown.append("present weather (left)")
        if labels:
            put((0, -2.4), [str(x) for x in sel[sdim].values], fontsize=7, color="0.35")

    ax.set_title(title or f"Station observations {when}".strip(), fontsize=12)
    fig.text(
        0.01,
        0.01,
        f"{len(lat)} stations; " + "; ".join(shown) + f". MetPy {metpy.__version__}",
        fontsize=7,
        color="0.4",
    )
    print(
        f"plot-stations-metpy: {len(lat)} stations at {when or 'the selected time'}",
        file=sys.stderr,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_stations()
