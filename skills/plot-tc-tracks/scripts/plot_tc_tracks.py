# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cartopy",
#   "cftime>=1.6",
#   # matplotlib<3.10: cartopy gridliner crash
#   "matplotlib>=3.8,<3.10",
#   "numpy",
#   "pandas",
#   "pillow",
#   "xarray",
#   "zarr",
# ]
# ///
"""Draw tropical cyclone tracks from tc-track: a track map, a strike-probability map, or Hart phase diagrams."""

from __future__ import annotations

import itertools
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

STRIKE_LEVELS = (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
# ECMWF's strike-probability chart classes: 5-10 % dark green through > 90 % violet.
STRIKE_COLORS = (
    "#1b7a4e",
    "#6b9e45",
    "#b3c45c",
    "#ffff00",
    "#f2c75c",
    "#d18a45",
    "#ff7f00",
    "#ff0000",
    "#990000",
    "#8a2be2",
)
STRIKE_LABELS = (
    "5-10",
    "10-20",
    "20-30",
    "30-40",
    "40-50",
    "50-60",
    "60-70",
    "70-80",
    "80-90",
    "> 90 %",
)
B_THRESHOLD = 10.0
# ECMWF-style intensity classes (tc-track's `category`, codes 0-4).
CATEGORY_LABELS = (
    "tropical depression (<34 kt)",
    "tropical storm (34-63 kt)",
    "Cat 1 (64-82 kt)",
    "Cat 2 (83-95 kt)",
    "Cat 3+ (>=96 kt)",
)
CATEGORY_COLORS = ("#737373", "#41ab5d", "#fec44f", "#f16913", "#a50f15")
# Ensemble mean track: member tracks within this mean distance of a seed track, over at
# least MATCH_POINTS shared times, are the same storm; the mean is drawn where at least
# MEAN_MIN_FRACTION of the ensemble (and at least 2 members) have that storm.
MATCH_KM = 500.0
MATCH_POINTS = 2
MEAN_MIN_FRACTION = 0.2
# Control and ensemble mean: drawn above coastlines, shading and member tracks.
REF_LW = 2.0
REF_ZORDER = 20
# Official tracks (--reference): best track and official forecast in blue, a-deck grey.
BEST_TRACK_COLOR = "#1f4e9e"
ADECK_COLOR = "#555555"
EARTH_RADIUS_KM = 6371.0


# Phases (tc-phase-space `phase`; tc-besttrack-fetch `phase` / `storm_type`).
POST_PHASES = ("extratropical", "remnant")
PRE_PHASES = ("pre-genesis", "non-tropical")
POST_COLOR = "#6e6e6e"
POST_STYLE = (0, (4, 2))
PRE_STYLE = (0, (1, 1.5))
TROPICAL_TYPES = {"TD", "TS", "HU", "TY", "ST", "TC", "SD", "SS", "MH", "TS/TY"}
EXTRATROPICAL_TYPES = {"EX", "ET"}


def phase_from_types(types):
    """pre-genesis / tropical / extratropical / remnant from official system types
    (as tc-besttrack-fetch)."""
    import numpy as np

    types = [str(t).strip().upper() for t in types]
    tropical = np.array([t in TROPICAL_TYPES for t in types])
    first = int(np.argmax(tropical)) if tropical.any() else len(types)
    out = []
    for k, t in enumerate(types):
        if not t:
            out.append("")
        elif t in TROPICAL_TYPES:
            out.append("tropical")
        elif t in EXTRATROPICAL_TYPES:
            out.append("extratropical")
        else:
            out.append("pre-genesis" if k < first else "remnant")
    return out


def point_phases(ds):
    """(track, obs) phase labels: ``phase``, else from ``storm_type``, else all tropical."""
    import numpy as np

    shape = (ds.sizes["track"], ds.sizes["obs"])
    if "phase" in ds:
        return ds["phase"].values.astype(str)
    if "storm_type" in ds:
        return np.array([phase_from_types(r) for r in ds["storm_type"].values.astype(str)])
    return np.full(shape, "tropical")


def segment_classes(ph):
    """Per segment between consecutive points: ``(post, pre)`` booleans (else tropical)."""
    import numpy as np

    a, b = np.asarray(ph[:-1]), np.asarray(ph[1:])
    post = np.isin(a, POST_PHASES) | np.isin(b, POST_PHASES)
    pre = ~post & (np.isin(a, PRE_PHASES) | np.isin(b, PRE_PHASES))
    return post, pre


def _segments(x, y):
    import numpy as np

    return np.stack([np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])], 1)


def _add_segments(ax, seg, mask, **style):
    """A LineCollection of the segments where ``mask`` is True (None when there are none)."""
    import cartopy.crs as ccrs
    from matplotlib.collections import LineCollection

    if not mask.any():
        return None
    lines = LineCollection(seg[mask], transform=ccrs.PlateCarree(), **style)
    ax.add_collection(lines)
    return lines


def _time_label(value):
    import pandas as pd

    try:
        return f"{pd.Timestamp(value):%d %b %H UTC}"
    except (TypeError, ValueError):
        return value.strftime("%d %b %H UTC")


def _extent(tracks, bbox, margin=5.0, extra=None):
    import numpy as np

    if bbox is not None:
        north, west, south, east = (float(v) for v in bbox)
        return west, east, south, north
    lat = tracks["latitude"].values.ravel()
    lon = tracks["longitude"].values.ravel()
    if extra is not None:
        lat = np.concatenate([lat, extra["latitude"].values.ravel()])
        lon = np.concatenate([lon, extra["longitude"].values.ravel()])
    if not np.isfinite(lat).any():
        raise UsageError("no tracks to draw; pass --bbox to draw an empty map")
    return (
        np.nanmin(lon) - margin,
        np.nanmax(lon) + margin,
        np.nanmin(lat) - margin,
        np.nanmax(lat) + margin,
    )


def _base_map(tracks, bbox, colorbar=True, extra=None):
    """Figure and map axes for the extent; returns ``(fig, ax, colorbar kwargs)``."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.pyplot as plt

    west, east, south, north = _extent(tracks, bbox, extra=extra)
    central = 0.5 * (west + east)
    proj = ccrs.PlateCarree(central_longitude=central if east - west < 360 else 0)
    wide = (east - west) > 2.2 * (north - south)
    bar = {"orientation": "horizontal", "shrink": 0.5, "pad": 0.04} if wide else {}
    bar = {"shrink": 0.7, "pad": 0.02} | bar
    # Wide maps: height from the extent's aspect plus room for the title and colour bar.
    extra = 2.2 if colorbar else 1.0
    size = (18, 18 * (north - south) / (east - west) + extra) if wide else (11, 8)
    fig = plt.figure(figsize=size, layout="constrained")
    ax = fig.add_subplot(projection=proj)
    ax.set_extent([west, east, south, north], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#e6e3dd", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="white", zorder=0)
    ax.coastlines(resolution="50m", linewidth=0.7, zorder=3)
    ax.add_feature(cfeature.BORDERS, linewidth=0.4, edgecolor="grey", zorder=3)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="grey", alpha=0.6)
    gl.top_labels = gl.right_labels = False
    return fig, ax, bar


def _xy(lat, lon):
    """Finite points of one track, longitudes unwrapped to stay continuous over the dateline."""
    import numpy as np

    ok = np.isfinite(lat) & np.isfinite(lon)
    return ok, lat[ok], np.rad2deg(np.unwrap(np.deg2rad(lon[ok])))


def _forecast_title(tracks, what):
    init = tracks.attrs.get("forecast_reference_time")
    members = tracks.attrs.get("ensemble_members")
    parts = [what]
    if members is not None:
        parts.append(f"{len(members)} members")
    if init:
        parts.append(f"forecast from {str(init)[:13].replace('T', ' ')} UTC")
    return ", ".join(parts)


def _map(tracks, color_by, bbox, title, reference=None):
    """Spaghetti map: every member's track, coloured by msl, wind or intensity class."""
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
    from matplotlib.lines import Line2D

    fig, ax, bar = _base_map(tracks, bbox, colorbar=color_by != "category", extra=reference)
    tracks, observed = split_observed(tracks)
    many = tracks.sizes["track"] > 40
    if color_by == "category":
        if "category" not in tracks:
            raise UsageError("--color-by category needs the category variable (tc-track output)")
        values = tracks["category"].values
        cmap = ListedColormap(CATEGORY_COLORS)
        norm, label = BoundaryNorm(np.arange(-0.5, len(CATEGORY_COLORS)), cmap.N), None
    elif color_by == "wind":
        if "wind_max" not in tracks:
            raise UsageError("--color-by wind needs wind_max in the tracks")
        values = tracks["wind_max"].values
        norm, cmap, label = Normalize(5, 50), plt.get_cmap("plasma"), "maximum wind (m s-1)"
    else:
        values = tracks["msl"].values / 100.0
        norm, cmap, label = Normalize(940, 1010), plt.get_cmap("viridis"), "central pressure (hPa)"
    lat = tracks["latitude"].values
    lon = tracks["longitude"].values
    numbers = tracks["number"].values if "number" in tracks.coords else None
    phases = point_phases(tracks)
    lines, drawn = None, set()
    lw = 0.7 if many else 1.2
    for k in range(tracks.sizes["track"]):
        ok, y, x = _xy(lat[k], lon[k])
        if not ok.any():
            continue
        v = values[k][ok]
        if x.size > 1:
            seg = _segments(x, y)
            post, pre = segment_classes(phases[k][ok])
            trop = ~post & ~pre
            if trop.any():
                lines = _add_segments(ax, seg, trop, cmap=cmap, norm=norm, linewidths=lw, zorder=5)
                # A category segment takes the stronger end's class; other fields the mean.
                arr = np.fmax(v[:-1], v[1:]) if color_by == "category" else 0.5 * (v[:-1] + v[1:])
                lines.set_array(arr[trop])
            if _add_segments(
                ax, seg, post, colors=POST_COLOR, linewidths=lw, linestyles=[POST_STYLE], zorder=5
            ):
                drawn.add("post")
            if _add_segments(
                ax, seg, pre, colors=POST_COLOR, linewidths=lw, linestyles=[PRE_STYLE], zorder=5
            ):
                drawn.add("pre")
        ax.plot(
            x[0],
            y[0],
            "o",
            ms=3 if many else 5,
            mfc="white",
            mec="k",
            mew=0.6 if many else 1.0,
            transform=ccrs.PlateCarree(),
            zorder=6,
        )
        if "ts_strength" in tracks and color_by != "category" and not many:
            ts = tracks["ts_strength"].values[k][ok] == 1
            ax.plot(x[ts], y[ts], ".", ms=4, color="k", transform=ccrs.PlateCarree(), zorder=6)
    ref_handles, ref_labels = _reference_tracks(ax, tracks)
    off_handles, off_labels = official_tracks(ax, reference, observed)
    ref_handles, ref_labels = ref_handles + off_handles, ref_labels + off_labels
    phase_handles, phase_labels = _phase_legend(drawn)
    ref_handles, ref_labels = ref_handles + phase_handles, ref_labels + phase_labels
    if color_by == "category":
        handles = [Line2D([], [], color=c, lw=3) for c in CATEGORY_COLORS] + ref_handles
        ax.legend(
            handles,
            [*CATEGORY_LABELS, *ref_labels],
            loc="lower left",
            fontsize=8,
            framealpha=0.9,
            title="10 m wind (model grid)",
            title_fontsize=8,
        )
    else:
        if lines is not None:
            fig.colorbar(lines, ax=ax, label=label, **bar)
        if ref_handles:
            ax.legend(ref_handles, ref_labels, loc="lower left", fontsize=8, framealpha=0.9)
    if title is None:
        n = tracks.sizes["track"]
        title = _forecast_title(tracks, f"{n} tropical cyclone track{'s' if n != 1 else ''}")
        if numbers is not None:
            title += "\no: first fix"
            if color_by != "category" and not many:
                title += "; dots: tropical-storm wind for this grid"
    ax.set_title(title, fontsize=11)
    return fig


def _unit(lat, lon):
    import numpy as np

    p, la = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(p) * np.cos(la), np.cos(p) * np.sin(la), np.sin(p)], axis=-1)


def _km(lat0, lon0, lat, lon):
    import numpy as np

    p0, l0, p, la = (np.radians(v) for v in (lat0, lon0, lat, lon))
    a = np.sin((p - p0) / 2) ** 2 + np.cos(p) * np.cos(p0) * np.sin((la - l0) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def storm_groups(tracks, match_km=MATCH_KM, match_points=MATCH_POINTS):
    """Group member tracks into storms.

    Seeds are taken control first, then longest first. A track from a member not yet in
    the group joins when it shares at least ``match_points`` valid times with the seed
    and its mean distance from the seed over those times is below ``match_km``.

    Returns:
        list of lists of track indices; the seed comes first.
    """
    import numpy as np

    n = tracks.sizes["track"]
    lat, lon = tracks["latitude"].values, tracks["longitude"].values
    times = tracks["time"].values
    numbers = tracks["number"].values if "number" in tracks.coords else np.arange(n)
    length = np.isfinite(lat).sum(axis=1)
    order = sorted(range(n), key=lambda k: (numbers[k] != 0, -length[k], k))
    index = []
    for k in range(n):
        ok = np.isfinite(lat[k])
        index.append(dict(zip(times[k][ok], np.flatnonzero(ok), strict=True)))
    assigned = np.zeros(n, bool)
    groups = []
    for s in order:
        if assigned[s] or length[s] == 0:
            continue
        assigned[s] = True
        group, used = [s], {numbers[s]}
        for c in order:
            if assigned[c] or numbers[c] in used:
                continue
            common = [t for t in index[c] if t in index[s]]
            if len(common) < match_points:
                continue
            js = [index[s][t] for t in common]
            jc = [index[c][t] for t in common]
            d = _km(lat[s][js], lon[s][js], lat[c][jc], lon[c][jc])
            if float(np.mean(d)) < match_km:
                group.append(c)
                used.add(numbers[c])
                assigned[c] = True
        groups.append(group)
    return groups


def ensemble_mean_track(tracks, group, min_members):
    """Mean position of a storm's member tracks at each valid time with ``min_members``.

    Returns ``(times, lat, lon)`` sorted by time (empty arrays when no time qualifies).
    Positions are averaged as unit vectors, so the dateline needs no special case.
    """
    import numpy as np

    lat, lon, times = tracks["latitude"].values, tracks["longitude"].values, tracks["time"].values
    by_time = {}
    for k in group:
        ok = np.isfinite(lat[k])
        for t, a, b in zip(times[k][ok], lat[k][ok], lon[k][ok], strict=True):
            by_time.setdefault(t, []).append((a, b))
    keep = sorted(t for t, pts in by_time.items() if len(pts) >= min_members)
    out_lat, out_lon = [], []
    for t in keep:
        pts = np.array(by_time[t])
        v = _unit(pts[:, 0], pts[:, 1]).mean(axis=0)
        out_lat.append(float(np.degrees(np.arcsin(v[2] / np.linalg.norm(v)))))
        out_lon.append(float(np.degrees(np.arctan2(v[1], v[0]))))
    return np.array(keep), np.array(out_lat), np.array(out_lon)


def _phase_legend(drawn):
    from matplotlib.lines import Line2D

    handles, labels = [], []
    if "post" in drawn:
        handles.append(Line2D([], [], color=POST_COLOR, lw=1.2, ls=POST_STYLE))
        labels.append("extratropical / post-tropical (no wind class)")
    if "pre" in drawn:
        handles.append(Line2D([], [], color=POST_COLOR, lw=1.2, ls=PRE_STYLE))
        labels.append("pre-genesis / not tropical")
    return handles, labels


def mean_track_post(tracks, group, times, phases):
    """True at the ensemble-mean times when most contributing members are post-tropical."""
    import numpy as np

    t_all = tracks["time"].values
    out = []
    for t in times:
        states = [
            phases[k][j]
            for k in group
            for j in np.flatnonzero(t_all[k] == t)
            if np.isfinite(tracks["latitude"].values[k][j])
        ]
        post = sum(1 for p in states if p in POST_PHASES)
        out.append(bool(states) and post > len(states) / 2)
    return np.array(out, bool)


def _reference_tracks(ax, tracks):
    """Control (solid) and ensemble mean (dashed) tracks in black, above everything else.

    A white edge keeps the black lines readable over dark shading and dense member tracks.
    After extratropical transition the control is dotted and the mean grey.

    Returns:
        ``(handles, labels)`` for the legend; empty without ensemble members.
    """
    import cartopy.crs as ccrs
    import matplotlib.patheffects as pe
    import numpy as np
    from matplotlib.lines import Line2D

    if "number" not in tracks.coords:
        return [], []
    numbers = tracks["number"].values
    lat, lon = tracks["latitude"].values, tracks["longitude"].values
    phases = point_phases(tracks)
    members = tracks.attrs.get("ensemble_members")
    n_members = len(members) if members is not None else len(np.unique(numbers))
    min_members = max(2, int(np.ceil(MEAN_MIN_FRACTION * n_members)))
    halo = [pe.Stroke(linewidth=REF_LW + 2.2, foreground="white"), pe.Normal()]
    mean_dash = (0, (4, 2))
    ctrl_post = (0, (1, 1.2))
    any_post = False
    for group in storm_groups(tracks):
        if len(group) < min_members:
            continue
        t, y, x = ensemble_mean_track(tracks, group, min_members)
        if y.size < 2:
            continue
        x = np.rad2deg(np.unwrap(np.deg2rad(x)))
        post_pt = mean_track_post(tracks, group, t, phases)
        post = post_pt[:-1] | post_pt[1:]
        seg = _segments(x, y)
        for mask, color in ((~post, "k"), (post, POST_COLOR)):
            lines = _add_segments(
                ax,
                seg,
                mask,
                colors=color,
                linewidths=REF_LW,
                linestyles=[mean_dash],
                zorder=REF_ZORDER,
            )
            if lines is not None:
                lines.set_path_effects(halo)
        any_post |= bool(post.any())
    for k in np.flatnonzero(numbers == 0):
        ok, y, x = _xy(lat[k], lon[k])
        if not ok.any():
            continue
        if x.size > 1:
            post, pre = segment_classes(phases[k][ok])
            seg = _segments(x, y)
            for mask, style in ((~post & ~pre, "solid"), (post, ctrl_post), (pre, PRE_STYLE)):
                lines = _add_segments(
                    ax,
                    seg,
                    mask,
                    colors="k",
                    linewidths=REF_LW,
                    linestyles=[style],
                    zorder=REF_ZORDER + 1,
                )
                if lines is not None:
                    lines.set_path_effects(halo)
            any_post |= bool(post.any())
        ax.plot(
            x[0],
            y[0],
            "o",
            ms=5,
            mfc="k",
            mec="white",
            mew=1.0,
            transform=ccrs.PlateCarree(),
            zorder=REF_ZORDER + 2,
        )
    handles = [
        Line2D([], [], color="k", lw=REF_LW, path_effects=halo),
        Line2D([], [], color="k", lw=REF_LW, ls=mean_dash, path_effects=halo),
    ]
    labels = [
        "control",
        f"ensemble mean (where >= {min_members} of {n_members} members have the storm)",
    ]
    if any_post:
        handles += [
            Line2D([], [], color="k", lw=REF_LW, ls=ctrl_post, path_effects=halo),
            Line2D([], [], color=POST_COLOR, lw=REF_LW, ls=mean_dash, path_effects=halo),
        ]
        labels += ["control, extratropical", "ensemble mean, extratropical"]
    return handles, labels


def _strike(tracks, strike, bbox, title, reference=None):
    """Strike-probability map with the control and ensemble mean tracks in black."""
    import cartopy.crs as ccrs
    import numpy as np
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.lines import Line2D

    if strike is None:
        raise UsageError("--kind strike needs --strike-prob (tc-strike-probability output)")
    fig, ax, bar = _base_map(tracks, bbox, extra=reference)
    tracks, observed = split_observed(tracks)
    sp = strike["strike_probability"]
    # A grid across the dateline is stored as two pieces in -180..180; on 0..360 it is one.
    lon = sp["longitude"].values.astype(float)
    lon360 = np.sort(lon % 360.0)
    if lon.size > 1 and np.diff(lon360).max() < np.diff(np.sort(lon)).max():
        sp = sp.assign_coords(longitude=lon % 360.0).sortby("longitude")
    cmap = ListedColormap(STRIKE_COLORS)
    mesh = ax.pcolormesh(
        sp["longitude"],
        sp["latitude"],
        np.where(sp.values >= STRIKE_LEVELS[0], sp.values, np.nan),
        cmap=cmap,
        norm=BoundaryNorm(STRIKE_LEVELS, cmap.N),
        transform=ccrs.PlateCarree(),
        zorder=1,
        shading="nearest",
    )
    radius = strike.attrs.get("strike_radius_km", 120)
    cbar = fig.colorbar(mesh, ax=ax, label=f"strike probability within {radius:g} km (%)", **bar)
    centres = [0.5 * (a + b) for a, b in itertools.pairwise(STRIKE_LEVELS)]
    cbar.set_ticks(centres)
    cbar.set_ticklabels(STRIKE_LABELS)
    cbar.ax.tick_params(length=0)

    handles, labels = _reference_tracks(ax, tracks)
    off_handles, off_labels = official_tracks(ax, reference, observed)
    handles, labels = handles + off_handles, labels + off_labels
    if reference is not None and np.isin(point_phases(reference), POST_PHASES).any():
        handles.append(Line2D([], [], color=BEST_TRACK_COLOR, lw=REF_LW, ls=POST_STYLE))
        labels.append("best track, extratropical / post-tropical")
    if handles:
        ax.legend(handles, labels, loc="lower left", fontsize=8, framealpha=0.9)
    if title is None:
        window = strike.attrs.get("strike_window")
        what = f"Tropical cyclone strike probability within {radius:g} km"
        if window:
            what += f" ({window})"
        title = _forecast_title(tracks, what)
    ax.set_title(title, fontsize=11)
    return fig


def _select_storm(ds, storm):
    """Tracks of one storm (id or name), or all when ``storm`` is None."""
    import numpy as np

    if storm is None or ds is None:
        return ds
    want = storm.upper()
    hits = np.zeros(ds.sizes["track"], bool)
    for name in ("storm", "storm_name"):
        if name in ds.coords:
            hits |= np.char.upper(ds[name].values.astype(str)) == want
    if not hits.any():
        raise UsageError(f"--storm {storm!r} is not in the input")
    return ds.isel(track=np.flatnonzero(hits))


def split_observed(tracks):
    """Model tracks with the observed (``segment``) points blanked, plus the observed history.

    Returns ``(forecast tracks, observed)``; ``observed`` is ``(lat, lon, category, phase)``
    of the longest observed segment, or None.
    """
    import numpy as np

    if "segment" not in tracks:
        return tracks, None
    obs = tracks["segment"].values == "observed"
    if not obs.any():
        return tracks, None
    k = int(np.argmax(obs.sum(axis=1)))
    lat = tracks["latitude"].values[k][obs[k]].astype(float)
    lon = tracks["longitude"].values[k][obs[k]].astype(float)
    cat = (
        tracks["category"].values[k][obs[k]].astype(float)
        if "category" in tracks
        else np.full(lat.size, np.nan)
    )
    phase = point_phases(tracks)[k][obs[k]]
    fc = tracks.copy()
    for name in ("latitude", "longitude"):
        fc = fc.assign_coords({name: fc[name].where(~obs)})
    return fc, (lat, lon, cat, phase)


def _best_track_line(ax, lat, lon, cat, label, phase=None):
    """One official best track: thick blue line with a white edge and class-coloured dots;
    dashed with hollow dots where it is extratropical or a remnant, dotted before genesis."""
    import cartopy.crs as ccrs
    import matplotlib.patheffects as pe
    import numpy as np
    from matplotlib.lines import Line2D

    ok = np.isfinite(lat) & np.isfinite(lon)
    if not ok.any():
        return None
    y, x = lat[ok], np.rad2deg(np.unwrap(np.deg2rad(lon[ok])))
    c = cat[ok] if cat is not None else np.full(y.size, np.nan)
    ph = np.asarray(phase)[ok] if phase is not None else np.full(y.size, "tropical")
    halo = [pe.Stroke(linewidth=REF_LW + 3.0, foreground="white"), pe.Normal()]
    if x.size > 1:
        post, pre = segment_classes(ph)
        seg = _segments(x, y)
        for mask, style in ((~post & ~pre, "solid"), (post, POST_STYLE), (pre, PRE_STYLE)):
            lines = _add_segments(
                ax,
                seg,
                mask,
                colors=BEST_TRACK_COLOR,
                linewidths=REF_LW + 0.8,
                linestyles=[style],
                zorder=REF_ZORDER + 5,
            )
            if lines is not None:
                lines.set_path_effects(halo)
    tropical = ~np.isin(ph, POST_PHASES + PRE_PHASES)
    colors = [
        CATEGORY_COLORS[int(v)] if np.isfinite(v) and t else "white"
        for v, t in zip(c, tropical, strict=True)
    ]
    ax.scatter(
        x,
        y,
        s=16,
        c=colors,
        edgecolors=BEST_TRACK_COLOR,
        linewidths=0.8,
        transform=ccrs.PlateCarree(),
        zorder=REF_ZORDER + 6,
    )
    return Line2D(
        [], [], color=BEST_TRACK_COLOR, lw=REF_LW + 0.8, marker="o", mfc="white", label=label
    )


def official_tracks(ax, reference, observed=None):
    """Best tracks, official forecasts and a-deck model tracks on top of everything.

    Returns ``(handles, labels)`` for the legend.
    """
    import cartopy.crs as ccrs
    import matplotlib.patheffects as pe
    import numpy as np
    from matplotlib.lines import Line2D

    handles, labels = [], []
    if observed is not None and reference is None:
        h = _best_track_line(ax, *observed, "observed (best track)")
        if h is not None:
            handles.append(h)
            labels.append("observed (best track)")
    if reference is None:
        return handles, labels
    kinds = (
        reference["kind"].values
        if "kind" in reference.coords
        else np.array(["best_track"] * reference.sizes["track"])
    )
    lat = reference["latitude"].values.astype(float)
    lon = reference["longitude"].values.astype(float)
    cat = reference["category"].values.astype(float) if "category" in reference else None
    ref_phases = point_phases(reference)
    halo = [pe.Stroke(linewidth=REF_LW + 2.2, foreground="white"), pe.Normal()]
    seen = set()
    for k, kind in enumerate(kinds):
        if kind == "best_track":
            h = _best_track_line(
                ax, lat[k], lon[k], cat[k] if cat is not None else None, "", ref_phases[k]
            )
            if h is not None and "best" not in seen:
                handles.append(h)
                labels.append("official best track")
                seen.add("best")
            continue
        ok = np.isfinite(lat[k]) & np.isfinite(lon[k])
        if ok.sum() < 2:
            continue
        y, x = lat[k][ok], np.rad2deg(np.unwrap(np.deg2rad(lon[k][ok])))
        if kind == "official_forecast":
            ax.plot(
                x,
                y,
                ls=(0, (3, 2)),
                color=BEST_TRACK_COLOR,
                lw=REF_LW,
                path_effects=halo,
                transform=ccrs.PlateCarree(),
                zorder=REF_ZORDER + 4,
            )
            if "official" not in seen:
                handles.append(Line2D([], [], color=BEST_TRACK_COLOR, lw=REF_LW, ls=(0, (3, 2))))
                labels.append("official forecast")
                seen.add("official")
        else:  # a-deck model tracks: thin, labelled at the end
            model = str(kind).split(":", 1)[-1]
            ax.plot(
                x,
                y,
                color=ADECK_COLOR,
                lw=0.9,
                alpha=0.8,
                transform=ccrs.PlateCarree(),
                zorder=REF_ZORDER + 3,
            )
            if np.sum(kinds == kind) == 1:
                ax.text(
                    x[-1],
                    y[-1],
                    f" {model}",
                    fontsize=7,
                    color=ADECK_COLOR,
                    transform=ccrs.PlateCarree(),
                    zorder=REF_ZORDER + 3,
                )
            if "adeck" not in seen:
                handles.append(Line2D([], [], color=ADECK_COLOR, lw=0.9))
                labels.append("operational model tracks (a-deck)")
                seen.add("adeck")
    return handles, labels


def _errors(ds, storm, title):
    """tc-verify output: position error and along/cross-track error by lead time."""
    import matplotlib.pyplot as plt
    import numpy as np

    need = {"position_error_km", "lead_hours", "mean_position_error_km"}
    if not need <= set(ds.variables):
        raise UsageError("--kind errors needs tc-verify output (--mode track)")
    ids = [str(s) for s in ds["storm_id"].values]
    if storm is None:
        sid = ids[0]
    else:
        names = (
            [str(s).upper() for s in ds["storm_id_name"].values] if "storm_id_name" in ds else []
        )
        if storm.upper() in [i.upper() for i in ids]:
            sid = ids[[i.upper() for i in ids].index(storm.upper())]
        elif storm.upper() in names:
            sid = ids[names.index(storm.upper())]
        else:
            raise UsageError(f"--storm {storm!r} is not in the input; it has {', '.join(ids)}")
    s = ds.sel(storm_id=sid)
    lead = ds["lead"].values / 24.0
    rows = (
        np.flatnonzero(ds["storm"].values.astype(str) == sid)
        if "storm" in ds.coords
        else np.arange(ds.sizes["track"])
    )
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained")
    ax = axes[0]
    for k in rows:
        lh, e = ds["lead_hours"].values[k] / 24.0, ds["position_error_km"].values[k]
        ok = np.isfinite(lh) & np.isfinite(e)
        ax.plot(lh[ok], e[ok], color="0.75", lw=0.7, zorder=1)
    ax.plot(lead, s["mean_position_error_km"], color="k", lw=1.2, label="member mean", zorder=3)
    ax.plot(
        lead,
        s["ensemble_mean_position_error_km"],
        color="k",
        lw=2.0,
        ls=(0, (4, 2)),
        label="ensemble mean track",
        zorder=4,
    )
    ax.plot(
        lead, s["control_position_error_km"], color="tab:red", lw=1.6, label="control", zorder=4
    )
    ax.plot(lead, s["spread_km"], color="tab:blue", lw=1.2, ls=":", label="spread", zorder=3)
    ax.set(xlabel="lead time (days)", ylabel="km", title="position error")
    ax.legend(fontsize=8)
    ax = axes[1]
    ax.axhline(0, color="grey", lw=0.8)
    ax.plot(
        lead,
        s["mean_along_track_error_km"],
        color="tab:purple",
        lw=1.6,
        label="along-track (+ ahead, too fast)",
    )
    ax.plot(
        lead,
        s["mean_cross_track_error_km"],
        color="tab:green",
        lw=1.6,
        label="cross-track (+ right of motion)",
    )
    ax.set(xlabel="lead time (days)", ylabel="km", title="mean along- and cross-track error")
    ax.legend(fontsize=8)
    name = str(s["storm_id_name"].values) if "storm_id_name" in s.coords else ""
    fig.suptitle(title or f"Track errors against the best track: {sid} {name}".strip(), fontsize=11)
    return fig


def _phase(tracks, track_id, title):
    import matplotlib.pyplot as plt
    import numpy as np

    need = {"B", "neg_vtl", "neg_vtu"}
    if not need <= set(tracks.data_vars):
        raise UsageError("--kind phase needs tc-phase-space output (B, neg_vtl, neg_vtu)")
    if tracks.sizes["track"] == 0:
        raise UsageError("no tracks to draw")
    if track_id is None:
        counts = np.isfinite(tracks["neg_vtl"].values).sum(axis=1)
        k = int(np.argmax(counts))
    else:
        ids = [str(t) for t in tracks["track"].values]
        if track_id not in ids:
            raise UsageError(f"--track {track_id!r} not in the input; have {', '.join(ids[:10])}")
        k = ids.index(track_id)
    b, vl, vu = (tracks[n].values[k] for n in ("B", "neg_vtl", "neg_vtu"))
    times = tracks["time"].values[k]
    ok = np.isfinite(b) & np.isfinite(vl) & np.isfinite(vu)
    if ok.sum() < 1:
        raise UsageError("this track has no phase-space values")
    b, vl, vu, times = b[ok], vl[ok], vu[ok], times[ok]
    color = np.arange(b.size)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    lim_v = max(200.0, float(np.nanmax(np.abs([vl, vu]))) * 1.1)
    lim_b = max(30.0, float(np.nanmax(np.abs(b))) * 1.1)
    panels = (
        (
            axes[0],
            vl,
            b,
            "-VTL (900-600 hPa thermal wind)",
            "B (900-600 hPa thickness asymmetry, m)",
            (-lim_v, lim_v),
            (-lim_b, lim_b),
        ),
        (
            axes[1],
            vu,
            vl,
            "-VTU (600-300 hPa thermal wind)",
            "-VTL (900-600 hPa thermal wind)",
            (-lim_v, lim_v),
            (-lim_v, lim_v),
        ),
    )
    for ax, x, y, xl, yl, xlim, ylim in panels:
        ax.axvline(0, color="grey", lw=0.8)
        ax.axhline(B_THRESHOLD if ax is axes[0] else 0, color="grey", lw=0.8)
        ax.plot(x, y, color="lightgrey", lw=1, zorder=1)
        sc = ax.scatter(x, y, c=color, cmap="viridis", s=28, zorder=2)
        ax.annotate("A", (x[0], y[0]), xytext=(5, 5), textcoords="offset points", fontweight="bold")
        ax.annotate(
            "Z", (x[-1], y[-1]), xytext=(5, 5), textcoords="offset points", fontweight="bold"
        )
        ax.set(xlim=xlim, ylim=ylim, xlabel=xl, ylabel=yl)
    kw = {"fontsize": 9, "color": "dimgrey", "ha": "center"}
    axes[0].text(0.75 * lim_v, 0.9 * lim_b, "asymmetric warm core", **kw)
    axes[0].text(-0.75 * lim_v, 0.9 * lim_b, "asymmetric cold core", **kw)
    axes[0].text(0.75 * lim_v, -0.9 * lim_b, "symmetric warm core", **kw)
    axes[0].text(-0.75 * lim_v, -0.9 * lim_b, "symmetric cold core", **kw)
    axes[1].text(0.75 * lim_v, 0.9 * lim_v, "deep warm core", **kw)
    axes[1].text(-0.75 * lim_v, -0.9 * lim_v, "deep cold core", **kw)
    axes[1].text(-0.75 * lim_v, 0.9 * lim_v, "shallow warm core", **kw)
    cbar = fig.colorbar(sc, ax=axes, shrink=0.8, pad=0.02)
    ticks = np.linspace(0, b.size - 1, min(6, b.size)).round().astype(int)
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([_time_label(times[t]) for t in ticks])
    track = str(tracks["track"].values[k])
    fig.suptitle(
        title
        or f"Cyclone phase space (Hart 2003), track {track}: "
        f"{_time_label(times[0])} (A) to {_time_label(times[-1])} (Z)",
        fontsize=11,
    )
    return fig


@weather_skill(name="plot-tc-tracks", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    required=True,
    dest="ds",
    help="tc-track (or tc-phase-space) output Zarr.",
)
@weather_skill.argument(
    "--kind",
    choices=["map", "strike", "phase", "errors"],
    default="map",
    help="map: every member's track (spaghetti, default); strike: strike probability with "
    "the control and ensemble mean tracks; phase: Hart phase diagrams; errors: tc-verify "
    "track errors by lead time.",
)
@weather_skill.argument(
    "--strike-prob",
    type=Dataset("any"),
    default=None,
    help="tc-strike-probability output, for --kind strike.",
)
@weather_skill.argument(
    "--color-by",
    choices=["msl", "wind", "category"],
    default="msl",
    help="Colour tracks by central pressure (default), maximum wind, or intensity class "
    "(TD/TS/Cat 1/Cat 2/Cat 3+ from the wind in knots).",
)
@weather_skill.argument(
    "--reference",
    type=Dataset("any"),
    default=None,
    help="tc-besttrack-fetch output drawn on top: best track, official forecast, a-deck tracks.",
)
@weather_skill.argument("--storm", default=None, help="Draw one storm (id or name).")
@weather_skill.argument("--bbox", default=None, help="N/W/S/E map extent (default: the tracks).")
@weather_skill.argument(
    "--track", default=None, help="Track id for --kind phase (default: the longest)."
)
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_tc_tracks(
    ds, kind, strike_prob, color_by, reference, storm, bbox, track, title, output, **kwargs
):
    """Draw tropical cyclone tracks from tc-track: a track map, a strike-probability map, or Hart phase diagrams."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from weather_skills_core.units import dequantify_dataset

    tracks = dequantify_dataset(ds)
    if not {"track", "obs"} <= set(tracks.dims):
        raise UsageError("--input is not tc-track output (no track/obs dims)")
    strike = dequantify_dataset(strike_prob) if strike_prob is not None else None
    ref = dequantify_dataset(reference) if reference is not None else None
    if kind == "errors":
        fig = _errors(tracks, storm, title)
    elif kind == "phase":
        fig = _phase(_select_storm(tracks, storm), track, title)
    else:
        tracks, ref = _select_storm(tracks, storm), _select_storm(ref, storm)
        if kind == "strike":
            fig = _strike(tracks, strike, bbox, title, ref)
        else:
            if strike is not None:
                raise UsageError("--strike-prob is drawn with --kind strike, not on the track map")
            fig = _map(tracks, color_by, bbox, title, ref)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_tc_tracks()
