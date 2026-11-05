# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Verify model cyclone tracks against official best tracks: track and intensity errors by
lead time, or detection hit rate and false alarms over a period."""

from __future__ import annotations

import math
import sys

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

EARTH_RADIUS_KM = 6371.0
KT_TO_MS = 1852.0 / 3600.0
MEAN_MIN_FRACTION = 0.2  # ensemble mean where at least this share of members has the storm
MIN_OVERLAP_HOURS = 12.0
MATCH_HOURS = 48.0
MAX_KM = 400.0
MIN_WIND_KT = 34.0


def km(lat0, lon0, lat, lon):
    """Great-circle distance in km (broadcasting)."""
    import numpy as np

    p0, l0, p, la = (np.radians(np.asarray(v, dtype=float)) for v in (lat0, lon0, lat, lon))
    a = np.sin((p - p0) / 2) ** 2 + np.cos(p) * np.cos(p0) * np.sin((la - l0) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _hours(times):
    import numpy as np

    t = np.asarray(times, dtype="datetime64[ns]")
    out = (t - np.datetime64("1970-01-01", "ns")) / np.timedelta64(1, "h")
    return np.where(np.isnat(t), np.nan, out)


def _sorted_finite(h, *cols):
    import numpy as np

    ok = np.isfinite(h)
    for c in cols:
        ok &= np.isfinite(c)
    order = np.argsort(h[ok])
    return (h[ok][order], *(c[ok][order] for c in cols))


def interp_track(ref_hours, ref_lat, ref_lon, hours):
    """Reference position at ``hours`` (NaN outside its span), interpolated on the sphere."""
    import numpy as np

    h, la, lo = _sorted_finite(ref_hours, ref_lat, ref_lon)
    out_lat = np.full(np.shape(hours), np.nan)
    out_lon = np.full(np.shape(hours), np.nan)
    if h.size == 0:
        return out_lat, out_lon
    la, lo = np.radians(la), np.radians(lo)
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

    h, v = _sorted_finite(ref_hours, values)
    out = np.full(np.shape(hours), np.nan)
    if h.size == 0:
        return out
    hours = np.asarray(hours, dtype=float)
    inside = np.isfinite(hours) & (hours >= h[0] - 1e-6) & (hours <= h[-1] + 1e-6)
    out[inside] = np.interp(hours[inside], h, v)
    return out


def local_km(lat0, lon0, lat, lon):
    """East and north displacement (km) of (lat, lon) from (lat0, lon0), locally flat."""
    import numpy as np

    dlon = ((np.asarray(lon, float) - lon0 + 180.0) % 360.0) - 180.0
    east = np.radians(dlon) * EARTH_RADIUS_KM * np.cos(np.radians(0.5 * (lat + lat0)))
    north = np.radians(np.asarray(lat, float) - lat0) * EARTH_RADIUS_KM
    return east, north


def along_cross(ref, hours, lat, lon):
    """Along- and cross-track error (km) of model positions against the reference track.

    Along is positive ahead of the official storm (model too fast), cross positive to the
    right of its motion. The motion is the reference's centred 6 h difference.
    """
    import numpy as np

    rlat, rlon = interp_track(*ref, hours)
    alat, alon = interp_track(*ref, np.asarray(hours) - 3.0)
    blat, blon = interp_track(*ref, np.asarray(hours) + 3.0)
    # One-sided at the ends of the reference.
    alat = np.where(np.isfinite(alat), alat, rlat)
    alon = np.where(np.isfinite(alon), alon, rlon)
    blat = np.where(np.isfinite(blat), blat, rlat)
    blon = np.where(np.isfinite(blon), blon, rlon)
    me, mn = local_km(alat, alon, blat, blon)
    speed = np.hypot(me, mn)
    with np.errstate(invalid="ignore", divide="ignore"):
        ue, un = me / speed, mn / speed
    ee, en = local_km(rlat, rlon, lat, lon)
    along = ee * ue + en * un
    cross = ee * un - en * ue  # right of motion is (un, -ue)
    return along, cross


def match_score(ref, hours, lat, lon, min_overlap, match_hours):
    """Mean distance (km) over the first ``match_hours`` of overlap, or None."""
    import numpy as np

    rlat, rlon = interp_track(*ref, hours)
    ok = np.isfinite(rlat) & np.isfinite(lat)
    if ok.sum() < 2:
        return None
    h = hours[ok]
    if h.max() - h.min() < min_overlap - 1e-6:
        return None
    early = h <= h.min() + match_hours + 1e-6
    return float(km(rlat[ok][early], rlon[ok][early], lat[ok][early], lon[ok][early]).mean())


def assign(scores, max_km):
    """Greedy one-to-one assignment of ``(score, ref, model, member key)``, best first."""
    taken_model, taken_member, out = set(), set(), {}
    for score, r, m, key in sorted(scores, key=lambda s: s[0]):
        if score > max_km or m in taken_model or (r, key) in taken_member:
            continue
        taken_model.add(m)
        taken_member.add((r, key))
        out[m] = (r, score)
    return out


def _init_hours(tracks):
    """Init time in hours per track (NaN for reanalysis)."""
    import numpy as np

    n = tracks.sizes["track"]
    if "forecast_reference_time" in tracks.coords:
        frt = tracks["forecast_reference_time"]
        values = frt.values if frt.dims else np.full(n, frt.values)
        return _hours(values)
    attr = tracks.attrs.get("forecast_reference_time")
    if attr:
        return np.full(n, _hours(np.array([str(attr)[:19]], dtype="datetime64[ns]"))[0])
    return np.full(n, np.nan)


def _refs(reference):
    import numpy as np

    if "kind" in reference.coords:
        reference = reference.isel(track=np.flatnonzero(reference["kind"].values == "best_track"))
    ids = reference["storm"].values if "storm" in reference.coords else reference["track"].values
    names = (
        reference["storm_name"].values
        if "storm_name" in reference.coords
        else np.array([""] * reference.sizes["track"])
    )
    return reference, [str(i) for i in ids], [str(n) for n in names]


def _point(ds, name, k):
    import numpy as np

    if name not in ds:
        return np.full(ds.sizes["obs"], np.nan)
    return ds[name].values[k].astype(float)


def track_errors(tracks, reference):
    """Per-point errors plus a per-storm, per-lead summary (see SKILL.md)."""
    import numpy as np

    reference, ref_ids, ref_names = _refs(reference)
    index = {sid: r for r, sid in enumerate(ref_ids)}
    n, n_obs = tracks.sizes["track"], tracks.sizes["obs"]
    if "storm" in tracks.coords:
        storms = [str(s) for s in tracks["storm"].values]
    elif len(ref_ids) == 1:
        storms = [ref_ids[0]] * n
    else:
        raise UsageError("--tracks has no `storm` (run tc-match) and --reference has several")
    unknown = sorted({s for s in storms if s not in index})
    if unknown:
        raise UsageError(f"--tracks storms {', '.join(unknown)} are not in --reference")

    refs = [
        (
            _hours(reference["time"].values[r]),
            reference["latitude"].values[r].astype(float),
            reference["longitude"].values[r].astype(float),
        )
        for r in range(reference.sizes["track"])
    ]
    t_hours = np.stack([_hours(t) for t in tracks["time"].values])
    lat = tracks["latitude"].values.astype(float)
    lon = tracks["longitude"].values.astype(float)
    if "segment" in tracks:
        forecast = tracks["segment"].values == "forecast"
    else:
        forecast = np.ones((n, n_obs), bool)
    init = _init_hours(tracks)
    shape = (n, n_obs)
    out = {k: np.full(shape, np.nan) for k in ("lead", "dpe", "along", "cross", "msl", "wind")}
    for k in range(n):
        r = index[storms[k]]
        ref = refs[r]
        h = np.where(forecast[k], t_hours[k], np.nan)
        rlat, rlon = interp_track(*ref, h)
        ok = np.isfinite(rlat) & np.isfinite(lat[k])
        if not ok.any():
            continue
        out["dpe"][k] = np.where(ok, km(rlat, rlon, lat[k], lon[k]), np.nan)
        along, cross = along_cross(ref, h, lat[k], lon[k])
        out["along"][k] = np.where(ok, along, np.nan)
        out["cross"][k] = np.where(ok, cross, np.nan)
        lead = h - init[k] if np.isfinite(init[k]) else h - np.nanmin(h)
        out["lead"][k] = np.where(ok, lead, np.nan)
        for key, name in (("msl", "msl"), ("wind", "wind_max")):
            obs = interp_values(ref[0], _point(reference, name, r), h)
            out[key][k] = np.where(ok, _point(tracks, name, k) - obs, np.nan)

    dims = ("track", "obs")
    res = tracks.copy()
    for key, name, units, long in (
        ("lead", "lead_hours", "hours", "hours since the forecast init"),
        ("dpe", "position_error_km", "km", "great-circle distance to the official centre"),
        ("along", "along_track_error_km", "km", "along-track error, positive ahead (too fast)"),
        ("cross", "cross_track_error_km", "km", "cross-track error, positive right of motion"),
        ("msl", "msl_error", "Pa", "central pressure, model minus official"),
        ("wind", "wind_error", "m s-1", "maximum wind, model minus official"),
    ):
        res[name] = (dims, out[key], {"units": units, "long_name": long})

    # Summary per storm and lead.
    leads = np.unique(out["lead"][np.isfinite(out["lead"])])
    numbers = tracks["number"].values if "number" in tracks.coords else np.full(n, -1)
    members = tracks.attrs.get("ensemble_members")
    n_members = len(members) if members is not None else len(np.unique(numbers))
    min_members = max(2, math.ceil(MEAN_MIN_FRACTION * n_members))
    summary = {
        k: np.full((len(ref_ids), leads.size), np.nan)
        for k in ("mean_dpe", "mean_along", "mean_cross", "mean_msl", "mean_wind", "count")
    }
    summary.update(
        {
            k: np.full((len(ref_ids), leads.size), np.nan)
            for k in ("ens_mean_dpe", "control_dpe", "spread")
        }
    )
    for s, sid in enumerate(ref_ids):
        rows = [k for k in range(n) if storms[k] == sid]
        if not rows:
            continue
        for j, lead in enumerate(leads):
            sel = [
                (k, int(np.flatnonzero(np.isclose(out["lead"][k], lead))[0]))
                for k in rows
                if np.isclose(out["lead"][k], lead).any()
            ]
            if not sel:
                continue
            summary["count"][s, j] = len(sel)
            for key, src in (
                ("mean_dpe", "dpe"),
                ("mean_along", "along"),
                ("mean_cross", "cross"),
                ("mean_msl", "msl"),
                ("mean_wind", "wind"),
            ):
                vals = np.array([out[src][k, o] for k, o in sel])
                if np.isfinite(vals).any():
                    summary[key][s, j] = np.nanmean(vals)
            ctrl = [(k, o) for k, o in sel if numbers[k] == 0]
            if ctrl:
                summary["control_dpe"][s, j] = out["dpe"][ctrl[0]]
            if len(sel) >= min_members:
                plat = np.array([lat[k, o] for k, o in sel])
                plon = np.array([lon[k, o] for k, o in sel])
                p, la = np.radians(plat), np.radians(plon)
                v = np.array([np.cos(p) * np.cos(la), np.cos(p) * np.sin(la), np.sin(p)]).mean(1)
                mlat = math.degrees(math.asin(v[2] / np.linalg.norm(v)))
                mlon = math.degrees(math.atan2(v[1], v[0]))
                h = t_hours[sel[0][0], sel[0][1]]
                rlat, rlon = interp_track(*refs[index[sid]], np.array([h]))
                summary["ens_mean_dpe"][s, j] = float(km(rlat[0], rlon[0], mlat, mlon))
                summary["spread"][s, j] = float(km(mlat, mlon, plat, plon).mean())
    sdims = ("storm_id", "lead")
    for key, name, units, long in (
        ("count", "n_tracks", "1", "tracks verified at this lead"),
        ("mean_dpe", "mean_position_error_km", "km", "mean over members of the position error"),
        ("mean_along", "mean_along_track_error_km", "km", "mean along-track error"),
        ("mean_cross", "mean_cross_track_error_km", "km", "mean cross-track error"),
        ("mean_msl", "mean_msl_error", "Pa", "mean central pressure error"),
        ("mean_wind", "mean_wind_error", "m s-1", "mean maximum wind error"),
        (
            "ens_mean_dpe",
            "ensemble_mean_position_error_km",
            "km",
            f"position error of the ensemble mean track (>= {min_members} members)",
        ),
        ("control_dpe", "control_position_error_km", "km", "position error of the control"),
        ("spread", "spread_km", "km", "mean distance of the members to the ensemble mean"),
    ):
        res[name] = (sdims, summary[key], {"units": units, "long_name": long})
    res = res.assign_coords(
        storm_id=("storm_id", np.array(ref_ids)),
        lead=("lead", leads, {"units": "hours", "long_name": "lead time"}),
        storm_id_name=("storm_id", np.array(ref_names)),
    )
    res.attrs["tc_verify_mode"] = "track"
    for s, sid in enumerate(ref_ids):
        valid = np.isfinite(summary["mean_dpe"][s])
        if not np.isfinite(init).any():  # reanalysis: no lead times
            rows = [k for k in range(n) if storms[k] == sid]
            d = out["dpe"][rows]
            if np.isfinite(d).any():
                print(
                    f"{sid} {ref_names[s]}: mean position error {np.nanmean(d):.0f} km over "
                    f"{int(np.isfinite(d).sum())} fixes",
                    file=sys.stderr,
                )
            continue
        if valid.any():
            text = ", ".join(
                f"{leads[j]:g} h {summary['mean_dpe'][s, j]:.0f} km"
                for j in np.flatnonzero(valid)
                if leads[j] % 24 == 0
            )
            print(f"{sid} {ref_names[s]}: mean position error {text}", file=sys.stderr)
    return res


def detection(tracks, reference, bbox, min_wind_kt, min_overlap, match_hours, max_km):
    """Hit rate and false alarms of model tracks against every official storm in the window."""
    import numpy as np
    import xarray as xr

    reference, ref_ids, ref_names = _refs(reference)
    n = tracks.sizes["track"]
    t_hours = np.stack([_hours(t) for t in tracks["time"].values]) if n else np.zeros((0, 1))
    lat = tracks["latitude"].values.astype(float)
    lon = tracks["longitude"].values.astype(float)
    span = (np.nanmin(t_hours), np.nanmax(t_hours)) if n else (np.nan, np.nan)
    numbers = tracks["number"].values if "number" in tracks.coords else np.full(n, -1)
    members = tracks.attrs.get("ensemble_members") or sorted({int(x) for x in numbers})
    keys = [int(x) for x in numbers]

    counted, refs = [], []
    for r in range(reference.sizes["track"]):
        h = _hours(reference["time"].values[r])
        la = reference["latitude"].values[r].astype(float)
        lo = reference["longitude"].values[r].astype(float)
        inside = np.isfinite(h) & (h >= span[0]) & (h <= span[1])
        if bbox is not None:
            north, west, south, east = bbox
            lon_ok = (lo >= west) & (lo <= east) if west <= east else (lo >= west) | (lo <= east)
            inside &= (la <= north) & (la >= south) & lon_ok
        wind = _point(reference, "wind_max", r)
        strong = np.isfinite(wind) & (wind >= min_wind_kt * KT_TO_MS)
        if inside.any() and (not np.isfinite(wind).any() or (strong & inside).any()):
            counted.append(r)
            refs.append((h, la, lo))
    scores = []
    for i, ref in enumerate(refs):
        for m in range(n):
            s = match_score(ref, t_hours[m], lat[m], lon[m], min_overlap, match_hours)
            if s is not None:
                scores.append((s, i, m, keys[m]))
    matched = assign(scores, max_km)

    hit = np.zeros((len(refs), len(members)))
    dist = np.full((len(refs), len(members)), np.nan)
    col = {int(mm): j for j, mm in enumerate(members)}
    track_storm = np.array([""] * n, dtype=object)
    for m, (i, score) in matched.items():
        j = col.get(keys[m], 0)
        hit[i, j], dist[i, j] = 1.0, score
        track_storm[m] = ref_ids[counted[i]]
    pod = float(hit.mean()) if hit.size else float("nan")
    far = float((track_storm == "").mean()) if n else float("nan")
    res = xr.Dataset(
        {
            "hit": (("storm_id", "member"), hit, {"long_name": "1 where the member has the storm"}),
            "match_distance_km": (("storm_id", "member"), dist, {"units": "km"}),
            "hit_fraction": (
                "storm_id",
                hit.mean(axis=1) if hit.size else np.zeros(len(refs)),
                {"long_name": "share of members that detect the storm"},
            ),
            "track_storm": (
                "track",
                track_storm.astype(str),
                {"long_name": "official storm of each model track ('' = false alarm)"},
            ),
        },
        coords={
            "storm_id": ("storm_id", np.array([ref_ids[r] for r in counted], dtype=str)),
            "storm_id_name": ("storm_id", np.array([ref_names[r] for r in counted], dtype=str)),
            "member": ("member", np.array([int(x) for x in members])),
            "track": tracks["track"].values,
        },
        attrs={
            "tc_verify_mode": "detection",
            "tc_verify_hit_rate": pod,
            "tc_verify_false_alarm_ratio": far,
            "tc_verify_min_wind_kt": min_wind_kt,
            "tc_verify_max_km": max_km,
            "tc_verify_storms": len(refs),
            "tc_verify_model_tracks": n,
        },
    )
    missed = [
        f"{ref_ids[counted[i]]} {ref_names[counted[i]]}".strip()
        for i in range(len(refs))
        if hit[i].sum() == 0
    ]
    print(
        f"hit rate {pod:.2f} over {len(refs)} official storms; false-alarm ratio {far:.2f} "
        f"over {n} model tracks" + (f"; missed: {', '.join(missed)}" if missed else ""),
        file=sys.stderr,
    )
    return res


@weather_skill(name="tc-verify", version=_SKILL_VERSION)
@weather_skill.argument(
    "--tracks",
    type=Dataset("any"),
    required=True,
    help="Model tracks: tc-match output (track mode) or tc-track output (detection mode).",
)
@weather_skill.argument(
    "--reference", type=Dataset("any"), required=True, help="tc-besttrack-fetch output."
)
@weather_skill.argument(
    "--mode",
    default="track",
    choices=["track", "detection"],
    help="track: errors by lead time; detection: hit rate and false alarms.",
)
@weather_skill.argument(
    "--bbox", default=None, help="N/W/S/E: detection mode counts official storms inside it only."
)
@weather_skill.argument(
    "--min-wind-kt",
    type=float,
    default=MIN_WIND_KT,
    help=f"Detection mode counts storms reaching this wind (default {MIN_WIND_KT:g} kt).",
)
@weather_skill.argument("--min-overlap", type=float, default=MIN_OVERLAP_HOURS, help="As tc-match.")
@weather_skill.argument("--match-hours", type=float, default=MATCH_HOURS, help="As tc-match.")
@weather_skill.argument("--max-km", type=float, default=MAX_KM, help="As tc-match.")
def tc_verify(
    tracks, reference, mode, bbox, min_wind_kt, min_overlap, match_hours, max_km, **kwargs
):
    """Verify model cyclone tracks against official best tracks: track and intensity errors
    by lead time, or detection hit rate and false alarms over a period."""
    from weather_skills_core.units import dequantify_dataset

    tracks, reference = dequantify_dataset(tracks), dequantify_dataset(reference)
    for name, ds in (("--tracks", tracks), ("--reference", reference)):
        if not {"track", "obs"} <= set(ds.dims) or "latitude" not in ds.coords:
            raise UsageError(f"{name} is not a track file (no track/obs dims)")
    if tracks.sizes["track"] == 0:
        raise DataError("--tracks has no tracks")
    if mode == "track":
        return track_errors(tracks, reference)
    bbox = tuple(float(v) for v in bbox) if bbox is not None else None
    return detection(tracks, reference, bbox, min_wind_kt, min_overlap, match_hours, max_km)


if __name__ == "__main__":
    tc_verify()
