# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Match model cyclone tracks (tc-track) to official storms (tc-besttrack-fetch), and optionally
put the observed history in front of each matched forecast."""

from __future__ import annotations

import json
import sys

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

EARTH_RADIUS_KM = 6371.0
KT_TO_MS = 1852.0 / 3600.0
# Intensity classes on the wind in knots: same edges as tc-track's `category`.
CATEGORY_KT = (34.0, 64.0, 83.0, 96.0)
MIN_OVERLAP_HOURS = 12.0
MATCH_HOURS = 48.0
MAX_KM = 400.0
MAX_GAP_HOURS = 36.0
# Tropical cyclones rarely move faster than ~15 m/s equatorward of 30 degrees; faster steps
# there are the stitcher hopping to another low (typically heat lows after landfall).
MAX_SPEED_MS = 20.0
SPEED_LAT_LIMIT = 30.0
ADJUST_HOURS = 24.0
INTENSITY_ADJUST_HOURS = 48.0


def wind_category(wind_ms):
    """0 TD (<34 kt), 1 TS (34-63), 2 Cat 1 (64-82), 3 Cat 2 (83-95), 4 Cat 3+ (>=96); NaN if NaN."""
    import numpy as np

    wind_ms = np.asarray(wind_ms, dtype=float)
    cat = np.digitize(wind_ms / KT_TO_MS, CATEGORY_KT).astype(float)
    return np.where(np.isfinite(wind_ms), cat, np.nan)


def km(lat0, lon0, lat, lon):
    """Great-circle distance in km (broadcasting)."""
    import numpy as np

    p0, l0, p, la = (np.radians(np.asarray(v, dtype=float)) for v in (lat0, lon0, lat, lon))
    a = np.sin((p - p0) / 2) ** 2 + np.cos(p) * np.cos(p0) * np.sin((la - l0) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _hours(times):
    """Hours since 1970 for datetime64 (NaT -> NaN)."""
    import numpy as np

    t = np.asarray(times, dtype="datetime64[ns]")
    out = (t - np.datetime64("1970-01-01", "ns")) / np.timedelta64(1, "h")
    return np.where(np.isnat(t), np.nan, out)


def interp_track(ref_hours, ref_lat, ref_lon, hours):
    """Reference position at ``hours`` (NaN outside its span), interpolated on the sphere."""
    import numpy as np

    ok = np.isfinite(ref_hours) & np.isfinite(ref_lat) & np.isfinite(ref_lon)
    h, la, lo = ref_hours[ok], np.radians(ref_lat[ok]), np.radians(ref_lon[ok])
    out_lat = np.full(np.shape(hours), np.nan)
    out_lon = np.full(np.shape(hours), np.nan)
    if h.size == 0:
        return out_lat, out_lon
    order = np.argsort(h)
    h, la, lo = h[order], la[order], lo[order]
    xyz = [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]
    hours = np.asarray(hours, dtype=float)
    inside = np.isfinite(hours) & (hours >= h[0] - 1e-6) & (hours <= h[-1] + 1e-6)
    v = np.stack([np.interp(hours[inside], h, c) for c in xyz])
    v /= np.linalg.norm(v, axis=0)
    out_lat[inside] = np.degrees(np.arcsin(v[2]))
    out_lon[inside] = np.degrees(np.arctan2(v[1], v[0]))
    return out_lat, out_lon


def interp_values(ref_hours, values, hours):
    import numpy as np

    ok = np.isfinite(ref_hours) & np.isfinite(values)
    out = np.full(np.shape(hours), np.nan)
    if ok.sum() == 0:
        return out
    h, v = ref_hours[ok], values[ok]
    order = np.argsort(h)
    h, v = h[order], v[order]
    hours = np.asarray(hours, dtype=float)
    inside = np.isfinite(hours) & (hours >= h[0] - 1e-6) & (hours <= h[-1] + 1e-6)
    out[inside] = np.interp(hours[inside], h, v)
    return out


def extrapolate(ref, hours, motion_hours=12.0):
    """Reference positions at ``hours`` after its last fix, along its last ``motion_hours`` of
    motion (great circle, constant speed)."""
    import numpy as np

    h, la, lo = ref
    ok = np.isfinite(h) & np.isfinite(la) & np.isfinite(lo)
    h, la, lo = h[ok], la[ok], lo[ok]
    order = np.argsort(h)
    h, la, lo = h[order], la[order], lo[order]
    if h.size < 2:
        return np.full(np.shape(hours), np.nan), np.full(np.shape(hours), np.nan)
    back = int(np.searchsorted(h, h[-1] - motion_hours))
    back = min(back, h.size - 2)
    dt = h[-1] - h[back]
    vlat = (la[-1] - la[back]) / dt
    vlon = (((lo[-1] - lo[back] + 180.0) % 360.0) - 180.0) / dt
    lead = np.asarray(hours, float) - h[-1]
    return la[-1] + vlat * lead, ((lo[-1] + vlon * lead + 180.0) % 360.0) - 180.0


def match_score(ref, model_hours, model_lat, model_lon, min_overlap, match_hours, max_gap=0.0):
    """How close a model track is to a reference track.

    With time overlap: the mean distance (km) over the first ``match_hours`` of overlap.
    When the reference ends before the model track starts (real time: the official record
    lags the forecast init) by at most ``max_gap`` hours: the distance from the model's first
    fix to the reference extrapolated along its last motion. Only the first fix is used,
    since a straight extrapolation misses recurvature.

    ``ref`` is ``(hours, lat, lon)`` of the reference track.

    Returns:
        ``(km, gap hours)``, gap 0 for an overlap, or None when there is no usable pairing.
    """
    import numpy as np

    rlat, rlon = interp_track(*ref, model_hours)
    ok = np.isfinite(rlat) & np.isfinite(model_lat)
    if ok.sum() >= 2:
        h = model_hours[ok]
        if h.max() - h.min() < min_overlap - 1e-6:
            return None
        early = h <= h.min() + match_hours + 1e-6
        d = km(rlat[ok][early], rlon[ok][early], model_lat[ok][early], model_lon[ok][early])
        return float(d.mean()), 0.0
    mok = np.isfinite(model_hours) & np.isfinite(model_lat)
    rh = ref[0][np.isfinite(ref[0])]
    if max_gap <= 0 or not mok.any() or rh.size < 2:
        return None
    gap = float(model_hours[mok].min() - rh.max())
    if gap <= 0 or gap > max_gap + 1e-6:
        return None
    first = int(np.flatnonzero(mok)[np.argmin(model_hours[mok])])
    elat, elon = extrapolate(ref, np.array([model_hours[first]]))
    d = km(elat[0], elon[0], model_lat[first], model_lon[first])
    return float(d), gap


def assign(scores, max_km):
    """Greedy one-to-one assignment.

    ``scores``: list of ``(score, ref index, model index, member key[, extra...])``. A model track joins
    one storm at most, and each storm takes one track per member key, best scores first.
    """
    taken_model, taken_member, out = set(), set(), {}
    for score, r, m, key, *rest in sorted(scores, key=lambda s: s[0]):
        if score > max_km or m in taken_model or (r, key) in taken_member:
            continue
        taken_model.add(m)
        taken_member.add((r, key))
        out[m] = (r, score, *rest)
    return out


def _member_keys(tracks):
    import numpy as np

    n = tracks.sizes["track"]
    number = tracks["number"].values if "number" in tracks.coords else np.full(n, -1)
    if "forecast_reference_time" in tracks.coords and tracks["forecast_reference_time"].dims:
        frt = tracks["forecast_reference_time"].values
    else:
        frt = np.full(n, np.datetime64("NaT", "ns"))
    return [(int(number[k]), str(frt[k])) for k in range(n)]


def _init_time(tracks, k):
    """Forecast init of model track k: its coord, the global attr, else its first fix."""
    import numpy as np

    if "forecast_reference_time" in tracks.coords:
        frt = tracks["forecast_reference_time"]
        value = frt.values[k] if frt.dims else frt.values
        if not np.isnat(np.datetime64(value, "ns")):
            return np.datetime64(value, "ns")
    attr = tracks.attrs.get("forecast_reference_time")
    if attr:
        return np.datetime64(str(attr)[:19], "ns")
    t = tracks["time"].values[k]
    return t[~np.isnat(t)][0]


def blend_track(model, ref, init, mode, adjust_hours, intensity_hours):
    """Observed history plus one forecast.

    Args:
        model: dict of 1-D arrays for one model track (``time`` datetime64, ``latitude``,
            ``longitude``, ``msl``, ``wind_max`` and any other per-point variables).
        ref: the same for the reference track (``msl``/``wind_max`` may be all NaN).
        init: forecast init (datetime64).
        mode: ``append`` or ``adjust``.

    Returns:
        ``(dict of arrays with a ``segment`` entry, offsets dict, note or None)``.
    """
    import numpy as np

    mt = model["time"]
    mok = ~np.isnat(mt)
    m = {k: np.asarray(v)[mok] for k, v in model.items()}
    m_hours = _hours(m["time"])
    r_hours = _hours(ref["time"])
    offsets = {"km": np.nan, "msl": np.nan, "wind": np.nan}
    note = None
    if mode == "adjust" and m_hours.size:
        h0 = m_hours[0]
        olat, olon = interp_track(r_hours, ref["latitude"], ref["longitude"], np.array([h0]))
        if np.isfinite(olat[0]):
            dlat = olat[0] - m["latitude"][0]
            dlon = ((olon[0] - m["longitude"][0] + 180.0) % 360.0) - 180.0
            offsets["km"] = float(km(olat[0], olon[0], m["latitude"][0], m["longitude"][0]))
            lead = m_hours - h0
            w = np.clip(1.0 - lead / adjust_hours, 0.0, 1.0) if adjust_hours > 0 else lead * 0
            m["latitude"] = m["latitude"] + w * dlat
            m["longitude"] = ((m["longitude"] + w * dlon + 180.0) % 360.0) - 180.0
            wi = (
                np.clip(1.0 - lead / intensity_hours, 0.0, 1.0) if intensity_hours > 0 else lead * 0
            )
            for key in ("msl", "wind_max"):
                if key not in m or key not in ref:
                    continue
                obs = interp_values(r_hours, np.asarray(ref[key], float), np.array([h0]))[0]
                if np.isfinite(obs) and np.isfinite(m[key][0]):
                    delta = obs - m[key][0]
                    offsets["msl" if key == "msl" else "wind"] = float(delta)
                    m[key] = m[key] + wi * delta
            if "wind_max" in m and "category" in m:
                m["category"] = wind_category(m["wind_max"])
        else:
            note = "no observed fix at the init; observed history appended without adjustment"
    # Observed fixes before the forecast's first point (or before the init).
    start = min(m["time"][0], init) if m["time"].size else init
    rok = ~np.isnat(ref["time"]) & (ref["time"] < start)
    out = {}
    for key, values in m.items():
        values = np.asarray(values)
        if key in ref:
            prefix = np.asarray(ref[key])[rok]
        elif np.issubdtype(values.dtype, np.datetime64):
            prefix = np.full(rok.sum(), np.datetime64("NaT", "ns"))
        elif values.dtype.kind in "US":
            prefix = np.full(rok.sum(), "", dtype=values.dtype)
        else:
            prefix = np.full(rok.sum(), np.nan)
        out[key] = np.concatenate([prefix.astype(values.dtype, copy=False), values])
    out["segment"] = np.array(["observed"] * int(rok.sum()) + ["forecast"] * len(m["time"]))
    return out, offsets, note


def cut_fast(row, max_speed, lat_limit=SPEED_LAT_LIMIT):
    """Blank a track from its first implausibly fast step on (equatorward of ``lat_limit``).

    Returns ``(row, number of points removed)``.
    """
    import numpy as np

    if not max_speed or max_speed <= 0:
        return row, 0
    t, lat, lon = row["time"], row["latitude"].astype(float), row["longitude"].astype(float)
    ok = np.flatnonzero(~np.isnat(t) & np.isfinite(lat) & np.isfinite(lon))
    if ok.size < 2:
        return row, 0
    h = _hours(t[ok])
    d = km(lat[ok][:-1], lon[ok][:-1], lat[ok][1:], lon[ok][1:]) * 1000.0
    speed = d / (np.diff(h) * 3600.0)
    mid_lat = 0.5 * (np.abs(lat[ok][:-1]) + np.abs(lat[ok][1:]))
    fast = np.flatnonzero((speed > max_speed) & (mid_lat < lat_limit))
    if fast.size == 0:
        return row, 0
    first_bad = ok[fast[0] + 1]
    out = {}
    for key, values in row.items():
        v = np.array(values, copy=True)
        if np.issubdtype(v.dtype, np.datetime64):
            v[first_bad:] = np.datetime64("NaT", "ns")
        elif v.dtype.kind in "US":
            v[first_bad:] = ""
        else:
            v = v.astype(float)
            v[first_bad:] = np.nan
        out[key] = v
    removed = int(np.sum(~np.isnat(t[first_bad:])))
    return out, removed


def _point_vars(ds):
    return [n for n, v in ds.variables.items() if v.dims == ("track", "obs")]


def _row(ds, k, names):
    return {n: ds[n].values[k] for n in names}


def _pack(rows, like):
    """Per-track dicts of 1-D arrays back into a (track, obs) dataset shaped like ``like``."""
    import numpy as np
    import xarray as xr

    n_obs = max((len(r["time"]) for r in rows), default=1)
    base = like.drop_dims("obs", errors="ignore")  # per-track coords and variables
    data = {}
    for name in rows[0] if rows else []:
        sample = np.asarray(rows[0][name])
        if np.issubdtype(sample.dtype, np.datetime64):
            arr = np.full((len(rows), n_obs), np.datetime64("NaT", "ns"))
        elif sample.dtype.kind in "US":
            arr = np.full((len(rows), n_obs), "", dtype=object)
        else:
            arr = np.full((len(rows), n_obs), np.nan)
        for k, r in enumerate(rows):
            v = np.asarray(r[name])
            arr[k, : v.size] = v
        attrs = like[name].attrs if name in like.variables else {}
        if arr.dtype == object:
            arr = arr.astype(str)
        data[name] = (("track", "obs"), arr, attrs)
    coords = {n: data.pop(n) for n in ("time", "latitude", "longitude") if n in data}
    res = xr.Dataset(data, coords={**coords, "obs": np.arange(n_obs)})
    res = res.assign_coords({n: v for n, v in base.coords.items()})
    for name, var in base.data_vars.items():
        res[name] = var
    res.attrs = dict(like.attrs)
    return res


@weather_skill(name="tc-match", version=_SKILL_VERSION)
@weather_skill.argument(
    "--tracks",
    type=Dataset("any"),
    required=True,
    help="Model tracks: tc-track output (ensemble, forecast, reanalysis or several inits).",
)
@weather_skill.argument(
    "--reference",
    type=Dataset("any"),
    required=True,
    help="Official tracks: tc-besttrack-fetch output (its best_track tracks are used).",
)
@weather_skill.argument("--storm", default=None, help="Keep one official storm (id or name).")
@weather_skill.argument(
    "--min-overlap",
    type=float,
    default=MIN_OVERLAP_HOURS,
    help=f"Hours a model track must overlap the official one (default {MIN_OVERLAP_HOURS:g}).",
)
@weather_skill.argument(
    "--match-hours",
    type=float,
    default=MATCH_HOURS,
    help=f"Hours of early overlap the match distance is averaged over (default {MATCH_HOURS:g}).",
)
@weather_skill.argument(
    "--max-km",
    type=float,
    default=MAX_KM,
    help=f"Largest mean distance for a match, km (default {MAX_KM:g}).",
)
@weather_skill.argument(
    "--max-gap",
    type=float,
    default=MAX_GAP_HOURS,
    help=f"Hours the official track may end before a model track starts (real time); the "
    f"official track is extrapolated along its last motion (default {MAX_GAP_HOURS:g}, 0 "
    f"turns it off).",
)
@weather_skill.argument(
    "--max-speed",
    type=float,
    default=MAX_SPEED_MS,
    help=f"m s-1: cut a matched track at its first faster step equatorward of "
    f"{SPEED_LAT_LIMIT:g} degrees (the stitcher hopping between lows after landfall; default "
    f"{MAX_SPEED_MS:g}, 0 turns it off).",
)
@weather_skill.argument(
    "--blend",
    default="none",
    choices=["none", "append", "adjust"],
    help="Observed history in front of each forecast: append, or adjust (also shift the "
    "forecast onto the observed position and intensity at the init, fading out).",
)
@weather_skill.argument(
    "--adjust-hours",
    type=float,
    default=ADJUST_HOURS,
    help=f"Hours over which the position offset fades with --blend adjust "
    f"(default {ADJUST_HOURS:g}).",
)
@weather_skill.argument(
    "--intensity-adjust-hours",
    type=float,
    default=INTENSITY_ADJUST_HOURS,
    help=f"Hours over which the msl and wind offsets fade with --blend adjust "
    f"(default {INTENSITY_ADJUST_HOURS:g}).",
)
def tc_match(
    tracks,
    reference,
    storm,
    min_overlap,
    match_hours,
    max_km,
    max_gap,
    max_speed,
    blend,
    adjust_hours,
    intensity_adjust_hours,
    **kwargs,
):
    """Match model cyclone tracks (tc-track) to official storms (tc-besttrack-fetch), and
    optionally put the observed history in front of each matched forecast."""
    import numpy as np
    from weather_skills_core.units import dequantify_dataset

    tracks, reference = dequantify_dataset(tracks), dequantify_dataset(reference)
    for name, ds in (("--tracks", tracks), ("--reference", reference)):
        if not {"track", "obs"} <= set(ds.dims) or "latitude" not in ds.coords:
            raise UsageError(f"{name} is not a track file (no track/obs dims)")
    if "kind" in reference.coords:
        reference = reference.isel(track=np.flatnonzero(reference["kind"].values == "best_track"))
    if reference.sizes["track"] == 0:
        raise UsageError("--reference has no best_track tracks")
    ref_ids = (
        reference["storm"].values if "storm" in reference.coords else reference["track"].values
    )
    ref_names = (
        reference["storm_name"].values
        if "storm_name" in reference.coords
        else np.array([""] * reference.sizes["track"])
    )
    if storm is not None:
        want = storm.upper()
        keep = [
            k
            for k in range(reference.sizes["track"])
            if str(ref_ids[k]).upper() == want or str(ref_names[k]).upper() == want
        ]
        if not keep:
            have = ", ".join(f"{i} {n}".strip() for i, n in zip(ref_ids, ref_names, strict=True))
            raise UsageError(f"--storm {storm!r} is not in --reference; it has {have}")
        reference, ref_ids, ref_names = (
            reference.isel(track=keep),
            ref_ids[keep],
            ref_names[keep],
        )

    refs = [
        (
            _hours(reference["time"].values[r]),
            reference["latitude"].values[r].astype(float),
            reference["longitude"].values[r].astype(float),
        )
        for r in range(reference.sizes["track"])
    ]
    keys = _member_keys(tracks)
    m_lat = tracks["latitude"].values.astype(float)
    m_lon = tracks["longitude"].values.astype(float)
    m_hours = np.stack([_hours(t) for t in tracks["time"].values]) if tracks.sizes["track"] else []
    scores = []
    for r, ref in enumerate(refs):
        for m in range(tracks.sizes["track"]):
            s = match_score(ref, m_hours[m], m_lat[m], m_lon[m], min_overlap, match_hours, max_gap)
            if s is not None:
                scores.append((s[0], r, m, keys[m], s[1]))
    matched = assign(scores, max_km)
    if not matched:
        best = min(scores, key=lambda s: s[0])[0] if scores else None
        hint = f"; the closest overlapping track is {best:.0f} km away" if best else ""
        raise DataError(f"no model track matches {', '.join(map(str, ref_ids))}{hint}")

    order = sorted(matched, key=lambda m: (matched[m][0], keys[m]))
    names = _point_vars(tracks)
    rows, offsets, notes, cuts = [], [], set(), []
    ref_names_pt = _point_vars(reference)
    for m in order:
        r = matched[m][0]
        row, removed = cut_fast(_row(tracks, m, names), max_speed)
        cuts.append(removed)
        if blend != "none":
            ref_row = _row(reference, r, [n for n in ref_names_pt if n in names or n == "time"])
            row, off, note = blend_track(
                row,
                ref_row,
                _init_time(tracks, m),
                blend,
                adjust_hours,
                intensity_adjust_hours,
            )
            offsets.append(off)
            if note:
                notes.add(note)
        rows.append(row)
    if blend == "none" and not any(cuts):
        out = tracks.isel(track=order)
    elif blend == "none":
        out = _pack(rows, tracks.isel(track=order))
    else:
        out = _pack(rows, tracks.isel(track=order))
        out["segment"].attrs = {"long_name": "observed (best track) or forecast"}
        for key, name, units in (
            ("km", "blend_offset_km", "km"),
            ("msl", "blend_offset_msl", "Pa"),
            ("wind", "blend_offset_wind", "m s-1"),
        ):
            out[name] = (
                "track",
                np.array([o[key] for o in offsets]),
                {"units": units, "long_name": f"observed minus model at the init ({key})"},
            )
    out = out.assign_coords(
        storm=("track", np.array([str(ref_ids[matched[m][0]]) for m in order])),
        storm_name=("track", np.array([str(ref_names[matched[m][0]]) for m in order])),
    )
    out["points_cut"] = (
        "track",
        np.array(cuts),
        {"long_name": f"points removed after a step faster than {max_speed:g} m s-1"},
    )
    out["match_gap_hours"] = (
        "track",
        np.array([matched[m][2] for m in order]),
        {
            "units": "hours",
            "long_name": "hours the official track ended before this track started "
            "(0: they overlap; >0: matched to its extrapolation)",
        },
    )
    out["match_distance_km"] = (
        "track",
        np.array([matched[m][1] for m in order]),
        {
            "units": "km",
            "long_name": f"mean distance to the official track, first {match_hours:g} h",
        },
    )
    missing = {}
    members = tracks.attrs.get("ensemble_members")
    if members is not None:
        for r, sid in enumerate(ref_ids):
            have = {keys[m][0] for m in matched if matched[m][0] == r}
            missing[str(sid)] = [int(x) for x in members if int(x) not in have]
    out.attrs.update(
        tc_match_reference=", ".join(
            f"{i} {n}".strip() for i, n in zip(ref_ids, ref_names, strict=True)
        ),
        tc_match_min_overlap_hours=min_overlap,
        tc_match_hours=match_hours,
        tc_match_max_km=max_km,
        tc_match_max_gap_hours=max_gap,
        tc_match_max_speed_ms=max_speed,
        tc_match_blend=blend,
        tc_match_missing_members=json.dumps(missing),
    )
    if blend == "adjust":
        out.attrs.update(
            tc_match_adjust_hours=adjust_hours,
            tc_match_intensity_adjust_hours=intensity_adjust_hours,
        )
    for note in sorted(notes):
        print(f"note: {note}", file=sys.stderr)
    for r, sid in enumerate(ref_ids):
        n = sum(1 for m in matched if matched[m][0] == r)
        total = f" of {len(members)} members" if members is not None else ""
        print(f"{sid} {ref_names[r]}: {n} matched track(s){total}", file=sys.stderr)
    if any(cuts):
        print(
            f"note: {sum(1 for c in cuts if c)} track(s) cut at a step faster than "
            f"{max_speed:g} m s-1 ({sum(cuts)} points removed)",
            file=sys.stderr,
        )
    return out


if __name__ == "__main__":
    tc_match()
