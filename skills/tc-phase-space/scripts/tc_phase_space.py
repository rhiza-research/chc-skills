# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "numpy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Add Hart (2003) cyclone phase space parameters (B, -VTL, -VTU) to tc-track tracks."""
# For each track point, within 500 km of the centre (Hart 2003, MWR 131:585):
#   B    = h * [(Z600 - Z900) right of motion - (Z600 - Z900) left of motion], h = +1 north, -1 south
#   -VTL = d(dZ)/d(ln p) over 900-600 hPa, -VTU over 600-300 hPa, dZ = max(Z) - min(Z)
# then a 24 h running mean along each track.

from __future__ import annotations

import math
import re
import sys

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

G = 9.80665
EARTH_RADIUS_KM = 6371.0
RADIUS_KM = 500.0
SMOOTH_HOURS = 24.0
B_THRESHOLD = 10.0  # m: Hart's frontal/non-frontal boundary
Z_NAMES = ("gh", "zg", "geopotential_height", "z", "geopotential", "hgt")
LEVEL_NAME = re.compile(
    r"^(gh|z|zg|hgt|geopotential_height|geopotential)_?(?P<level>\d{3,4})(hpa)?$", re.IGNORECASE
)
LEVEL_DIMS = ("level", "pressure_level", "plev", "isobaricInhPa", "vertical", "pressure", "lev")
MEMBER_DIMS = ("number", "member", "realization", "ensemble_member")
STEP_DIMS = ("step", "lead_time", "prediction_timedelta")
INIT_NAMES = ("time", "init_time", "forecast_reference_time")
LEVEL_TOL = 25.0  # hPa: 925 can stand in for 900, 575/625 for 600 and so on
LOWER_TOP_RANGE = (450.0, 700.0)  # hPa: approximate top of the lower layer when 600 is missing
MIN_TROPICAL_HOURS = 24.0
MAX_NON_TROPICAL_GAP_HOURS = 12.0  # longer non-tropical spells end the track


def _height(da):
    """Geopotential (m2 s-2) or geopotential height as height in m."""
    from weather_skills_core.units import convert_values

    units = da.attrs.get("units")
    units = units.strip() if isinstance(units, str) and units.strip() else None
    if units is None:
        units = "m2 s-2" if da.name in ("z", "geopotential") else "m"
    try:
        values, _ = convert_values(da.values, units, "m")
    except Exception:  # noqa: BLE001 - geopotential has no length unit
        try:
            values, _ = convert_values(da.values, units, "m2 s-2")
        except Exception as exc:
            raise UsageError(f"{da.name!r}: units {units!r} are not geopotential (height)") from exc
        values = values / G
    return da.copy(data=values)


def _level_hpa(coord):
    import numpy as np

    values = np.asarray(coord.values, dtype=float)
    units = (coord.attrs.get("units") or "").strip().lower()
    if units == "pa" or (not units and np.nanmax(values) > 2000.0):
        values = values / 100.0
    return values


def gather_heights(datasets):
    """Geopotential height in m on a ``level`` dim (hPa), from one variable or per-level names."""
    import numpy as np
    import xarray as xr
    from weather_skills_core.units import dequantify_dataset

    pieces = {}
    for ds in datasets:
        ds = dequantify_dataset(ds)
        for name in ds.data_vars:
            da = ds[name]
            dim = next((d for d in LEVEL_DIMS if d in da.dims), None)
            if name in Z_NAMES and dim is not None:
                levels = _level_hpa(da[dim])
                da = _height(da)
                for k, p in enumerate(levels):
                    pieces.setdefault(float(p), da.isel({dim: k}, drop=True))
            elif (m := LEVEL_NAME.match(name)) and dim is None:
                pieces.setdefault(float(m["level"]), _height(da))
    if not pieces:
        raise UsageError(f"no geopotential in the input; expected one of {', '.join(Z_NAMES)}")
    levels = sorted(pieces)
    z = xr.concat([pieces[p] for p in levels], dim="level", join="override")
    return z.assign_coords(level=np.array(levels))


def pick_levels(levels):
    """Levels for the lower (900-600) and upper (600-300 hPa) layers, Hart's 900/600/300 ends.

    Sources without a level near 600 hPa (dynamical's IFS-ENS has 925/850/500) get an
    approximate lower layer up to the nearest level in ``LOWER_TOP_RANGE``; without 300 hPa
    the upper layer is empty and -VTU is left NaN.

    Returns:
        ``(p_bottom, p_top, p300 or None, lower levels, upper levels, approximate)``.

    Raises:
        DataError: when no lower layer can be formed.
    """
    import numpy as np

    levels = np.asarray(levels, dtype=float)

    def nearest(p, lo=None, hi=None):
        cand = levels if lo is None else levels[(levels >= lo) & (levels <= hi)]
        if not cand.size:
            return None
        k = int(np.argmin(np.abs(cand - p)))
        if lo is None and abs(cand[k] - p) > LEVEL_TOL:
            return None
        return float(cand[k])

    p_bot, p_top, p300 = nearest(900.0), nearest(600.0), nearest(300.0)
    approximate = False
    if p_top is None:
        p_top = nearest(600.0, *LOWER_TOP_RANGE)
        approximate = p_top is not None
    if p_bot is None or p_top is None:
        raise DataError(
            f"phase space needs geopotential near 900 hPa (within {LEVEL_TOL:g} hPa) and near "
            f"600 hPa ({LOWER_TOP_RANGE[0]:g}-{LOWER_TOP_RANGE[1]:g} hPa accepted); the input "
            f"has {', '.join(f'{p:g}' for p in levels)} hPa"
        )
    lower = levels[(levels <= p_bot) & (levels >= p_top)]
    upper = levels[(levels <= p_top) & (levels >= p300)] if p300 is not None else levels[:0]
    return p_bot, p_top, p300, lower, upper, approximate


def b_threshold(p_bot, p_top):
    """Hart's 10 m B threshold scaled to the thickness layer's depth in ln p."""
    return B_THRESHOLD * math.log(p_bot / p_top) / math.log(900.0 / 600.0)


def tropical_mask(b, neg_vtl, threshold):
    """Hart's symmetric warm core: B below the threshold and -VTL above zero."""
    import numpy as np

    with np.errstate(invalid="ignore"):
        return (b < threshold) & (neg_vtl > 0)


def keep_tropical(mask, hours, min_hours, max_gap_hours=MAX_NON_TROPICAL_GAP_HOURS):
    """Keep a track's tropical spell: from genesis to extratropical transition.

    Tropical points less than ``max_gap_hours`` of non-tropical time apart form one spell.
    The first spell that is tropical for at least ``min_hours`` (tropical points times the
    time step) is kept, from its first to its last tropical point; a later spell, such as
    a transitioned low that turns symmetric and warm again, is dropped.

    Returns a boolean mask of the points kept (all False when no spell is long enough).
    """
    import numpy as np

    kept = np.zeros(mask.size, bool)
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return kept
    steps = np.diff(hours[np.isfinite(hours)])
    dt = float(np.median(steps)) if steps.size else 0.0
    # Split the tropical points where the non-tropical time between them exceeds the gap.
    gaps = hours[idx[1:]] - hours[idx[:-1]] - dt
    spells = np.split(idx, np.flatnonzero(gaps > max_gap_hours + 1e-6) + 1)
    for spell in spells:
        if spell.size * dt >= min_hours - 1e-6:
            kept[spell[0] : spell[-1] + 1] = True
            break
    return kept


def _layout(z):
    member = next((d for d in MEMBER_DIMS if d in z.dims), None)
    step = next((d for d in STEP_DIMS if d in z.dims), None)
    if step is not None:
        init = next((n for n in INIT_NAMES if n in z.coords), None)
        if init is None:
            raise UsageError(f"forecast input has a {step!r} dim but no init time coordinate")
        return {
            "member": member,
            "init": init if init in z.dims else None,
            "init_name": init,
            "tdim": step,
        }
    tdim = next((d for d in ("time", "valid_time") if d in z.dims), None)
    if tdim is None:
        raise UsageError("geopotential input has no time or step dimension")
    return {"member": member, "init": None, "init_name": None, "tdim": tdim}


def _valid_times(z, layout):
    import numpy as np

    if layout["tdim"] in STEP_DIMS:
        return np.asarray(z[layout["init_name"]].values) + np.asarray(z[layout["tdim"]].values)
    return np.asarray(z[layout["tdim"]].values)


def _hours(times):
    import numpy as np

    times = np.asarray(times)
    if np.issubdtype(times.dtype, np.datetime64):
        return (times - times[0]) / np.timedelta64(1, "h")
    return np.array([(t - times[0]).total_seconds() / 3600.0 for t in times])


def _gc_km(lat0, lon0, lat, lon):
    import numpy as np

    p0, l0 = np.radians(lat0), np.radians(lon0)
    p, la = np.radians(lat), np.radians(lon)
    a = np.sin((p - p0) / 2) ** 2 + np.cos(p) * np.cos(p0) * np.sin((la - l0) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def point_parameters(zmap, levels, lat, lon, lat0, lon0, heading, pick):
    """B, -VTL and -VTU at one centre.

    Args:
        zmap: (level, lat, lon) geopotential height in m.
        levels: level values in hPa (ascending order of ``zmap``'s first axis).
        lat, lon: 1-D grid coordinates (lon in [-180, 180)).
        lat0, lon0: storm centre.
        heading: (east, north) motion vector, or None when unknown (B is then NaN).
        pick: output of ``pick_levels``.

    Returns:
        ``(B, neg_vtl, neg_vtu)`` in m.
    """
    import numpy as np

    p900, p600, _, lower, upper, _ = pick
    dlat = RADIUS_KM / 111.2 + 1.0
    rows = np.flatnonzero(np.abs(lat - lat0) <= dlat)
    if not rows.size:
        return (np.nan, np.nan, np.nan)
    LA = np.broadcast_to(lat[rows][:, None], (rows.size, lon.size))
    LO = np.broadcast_to(lon[None, :], LA.shape)
    r = _gc_km(lat0, lon0, LA, LO)
    mask = r <= RADIUS_KM
    if not mask.any():
        return (np.nan, np.nan, np.nan)
    sub = zmap[:, rows, :]
    idx = {float(p): k for k, p in enumerate(levels)}

    def dz(p):
        vals = sub[idx[float(p)]][mask]
        vals = vals[np.isfinite(vals)]
        return float(vals.max() - vals.min()) if vals.size else np.nan

    def slope(layer):
        ln_p = np.log(np.asarray(layer, dtype=float))
        d = np.array([dz(p) for p in layer])
        ok = np.isfinite(d)
        if ok.sum() < 2:
            return np.nan
        return float(np.polyfit(ln_p[ok], d[ok], 1)[0])

    neg_vtl, neg_vtu = slope(lower), slope(upper)
    b = np.nan
    if heading is not None and np.hypot(*heading) > 0:
        thick = sub[idx[p600]] - sub[idx[p900]]
        east = (
            np.radians(((LO - lon0 + 180) % 360) - 180)
            * EARTH_RADIUS_KM
            * math.cos(math.radians(lat0))
        )
        north = np.radians(LA - lat0) * EARTH_RADIUS_KM
        cross = heading[0] * north - heading[1] * east  # > 0 left of motion, < 0 right
        right = mask & (cross < 0) & np.isfinite(thick)
        left = mask & (cross > 0) & np.isfinite(thick)
        if right.any() and left.any():
            h = 1.0 if lat0 >= 0 else -1.0
            b = h * float(thick[right].mean() - thick[left].mean())
    return (b, neg_vtl, neg_vtu)


def _headings(lat, lon):
    """Motion vector (east km, north km) at each point: centred difference, one-sided at the ends."""
    import numpy as np

    n = lat.size
    out = [None] * n
    for k in range(n):
        a, b = max(k - 1, 0), min(k + 1, n - 1)
        if a == b or not (np.isfinite(lat[a]) and np.isfinite(lat[b])):
            continue
        dlon = ((lon[b] - lon[a] + 180) % 360) - 180
        east = (
            math.radians(dlon) * EARTH_RADIUS_KM * math.cos(math.radians(0.5 * (lat[a] + lat[b])))
        )
        north = math.radians(lat[b] - lat[a]) * EARTH_RADIUS_KM
        out[k] = (east, north)
    return out


def running_mean(values, hours, window=SMOOTH_HOURS):
    """Centred running mean over +-window/2 hours, ignoring NaN."""
    import numpy as np

    out = np.full_like(values, np.nan)
    for k in range(values.size):
        if not np.isfinite(values[k]):
            continue
        near = (np.abs(hours - hours[k]) <= window / 2 + 1e-6) & np.isfinite(values)
        out[k] = values[near].mean()
    return out


@weather_skill(name="tc-phase-space", version=_SKILL_VERSION)
@weather_skill.argument(
    "--tracks", type=Dataset("any"), required=True, help="tc-track output Zarr."
)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("spatial"),
    action="append",
    required=True,
    dest="ds",
    help="Geopotential (height) on pressure levels 900 to 300 hPa, same source as the tracks.",
)
@weather_skill.argument(
    "--no-smooth", action="store_true", help="Leave out Hart's 24 h running mean."
)
@weather_skill.argument(
    "--tropical-only",
    action="store_true",
    help="Keep each track's tropical spell (symmetric warm core: B below the threshold, "
    "-VTL > 0), ending it at extratropical transition (more than 12 h non-tropical), and "
    "drop tracks tropical for less than --min-tropical-hours.",
)
@weather_skill.argument(
    "--min-tropical-hours",
    type=float,
    default=MIN_TROPICAL_HOURS,
    help=f"Hours a track must be tropical to be kept with --tropical-only "
    f"(default {MIN_TROPICAL_HOURS:g}).",
)
@weather_skill.argument(
    "--keep-transition",
    action="store_true",
    help="With --tropical-only, keep each track after its tropical spell ends (labelled "
    "extratropical in `phase`) instead of cutting it there.",
)
def tc_phase_space(
    tracks, ds, no_smooth, tropical_only, min_tropical_hours, keep_transition, **kwargs
):
    """Add Hart (2003) cyclone phase space parameters (B, -VTL, -VTU) to tc-track tracks."""
    import numpy as np
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.standard_utils import ensure_normalized_longitude
    from weather_skills_core.units import dequantify_dataset

    if keep_transition and not tropical_only:
        raise UsageError("--keep-transition goes with --tropical-only")
    tracks = dequantify_dataset(tracks)
    if not {"track", "obs"} <= set(tracks.dims) or "latitude" not in tracks.coords:
        raise UsageError("--tracks is not tc-track output (no track/obs dims)")
    z = gather_heights(ds if isinstance(ds, list) else [ds])
    lat_dim, lon_dim = detect_spatial_dims(z.to_dataset(name="z"))
    z = ensure_normalized_longitude(z, lon_dim).sortby([lat_dim, lon_dim])
    pick = pick_levels(z["level"].values)
    p_bot, p_top, p300, _, _, approximate = pick
    threshold = b_threshold(p_bot, p_top)
    if approximate:
        print(
            f"note: no geopotential near 600 hPa; lower layer is {p_bot:g}-{p_top:g} hPa "
            f"(approximate), B threshold scaled to {threshold:.1f} m",
            file=sys.stderr,
        )
    if p300 is None:
        print("note: no geopotential near 300 hPa; -VTU left NaN", file=sys.stderr)
    used = sorted({*pick[3].tolist(), *pick[4].tolist()})
    z = z.sel(level=used)
    layout = _layout(z)
    lat = z[lat_dim].values.astype(float)
    lon = z[lon_dim].values.astype(float)

    n_track, n_obs = tracks.sizes["track"], tracks.sizes["obs"]
    out = {k: np.full((n_track, n_obs), np.nan) for k in ("B", "neg_vtl", "neg_vtu")}
    t_lat = tracks["latitude"].values.astype(float)
    t_lon = tracks["longitude"].values.astype(float)
    t_time = tracks["time"].values
    members = tracks["number"].values if "number" in tracks.coords else [None] * n_track
    inits = (
        tracks["forecast_reference_time"].values
        if "forecast_reference_time" in tracks.coords
        else [None] * n_track
    )
    missing = 0
    cache = {}
    for k in range(n_track):
        sel = {}
        if layout["member"]:
            if members[k] is None:
                raise UsageError("geopotential has ensemble members but the tracks do not")
            sel[layout["member"]] = members[k]
        if layout["init"]:
            if inits[k] is None:
                raise UsageError("geopotential has several inits but the tracks do not")
            sel[layout["init"]] = inits[k]
        key = tuple(sel.items())
        if key not in cache:
            series = z.sel(sel) if sel else z
            times = _valid_times(series, layout)
            cache = {
                key: (
                    series.transpose(layout["tdim"], "level", lat_dim, lon_dim),
                    {t: j for j, t in enumerate(times)},
                )
            }
        series, index = cache[key]
        ok = np.isfinite(t_lat[k])
        heads = _headings(t_lat[k], t_lon[k])
        for j in np.flatnonzero(ok):
            tj = t_time[k, j]
            if tj not in index:
                missing += 1
                continue
            zmap = series.isel({layout["tdim"]: index[tj]}).values
            b, vl, vu = point_parameters(
                zmap, used, lat, lon, t_lat[k, j], t_lon[k, j], heads[j], pick
            )
            out["B"][k, j], out["neg_vtl"][k, j], out["neg_vtu"][k, j] = b, vl, vu
        if not no_smooth and ok.sum() > 1:
            hours = np.full(n_obs, np.nan)
            hours[ok] = _hours(t_time[k][ok])
            for values in out.values():
                row = values[k].copy()
                row[ok] = running_mean(row[ok], hours[ok])
                values[k] = row
    if missing:
        print(
            f"note: {missing} track points have no geopotential at their time (left NaN)",
            file=sys.stderr,
        )

    dims = ("track", "obs")
    lower_layer = f"{p_bot:g}-{p_top:g} hPa" + (", approximating 900-600" if approximate else "")
    res = tracks.copy()
    res["B"] = (
        dims,
        out["B"],
        {
            "units": "m",
            "long_name": f"Hart thermal asymmetry B ({lower_layer} thickness, right minus "
            "left of motion, within 500 km)",
        },
    )
    res["neg_vtl"] = (
        dims,
        out["neg_vtl"],
        {
            "units": "m",
            "long_name": f"Hart -VTL: lower-troposphere ({lower_layer}) thermal wind; "
            "positive is warm core",
        },
    )
    res["neg_vtu"] = (
        dims,
        out["neg_vtu"],
        {
            "units": "m",
            "long_name": "Hart -VTU: upper-troposphere (600-300 hPa) thermal wind; "
            "positive is warm core"
            + ("" if p300 is not None else " (NaN: no 300 hPa geopotential in the input)"),
        },
    )
    tropical = tropical_mask(out["B"], out["neg_vtl"], threshold)
    res["tropical"] = (
        dims,
        np.where(np.isfinite(out["B"]) & np.isfinite(out["neg_vtl"]), tropical, np.nan),
        {
            "units": "1",
            "long_name": f"1 where the cyclone is a symmetric warm core (B < {threshold:.3g} m "
            "and -VTL > 0, Hart 2003)",
        },
    )
    for name in ("B", "neg_vtl", "neg_vtu", "tropical"):
        res[name].attrs["coordinates"] = "time latitude longitude"
    res.attrs.update(
        phase_space_radius_km=RADIUS_KM,
        phase_space_levels_hpa=[float(p) for p in used],
        phase_space_lower_layer_hpa=[p_bot, p_top],
        phase_space_lower_layer_approximate=int(approximate),
        phase_space_smoothing_hours=0.0 if no_smooth else SMOOTH_HOURS,
        phase_space_b_threshold_m=threshold,
        phase_space_reference="Hart 2003 (MWR 131:585)",
    )
    spells = tropical_spells(tropical, t_time, min_tropical_hours)
    res["phase"] = (
        dims,
        phase_labels(spells, t_time),
        {
            "long_name": "cyclone phase: pre-genesis, tropical (the tropical spell), "
            "extratropical (after it ends), non-tropical (a track never tropical long enough)",
            "coordinates": "time latitude longitude",
        },
    )
    if tropical_only:
        res = _trim_tropical(res, spells, keep_transition)
        res.attrs["phase_space_tropical_only_min_hours"] = min_tropical_hours
        res.attrs["phase_space_keep_transition"] = int(keep_transition)
        what = "tropical spell and transition" if keep_transition else "tropical spell"
        print(
            f"kept {res.sizes['track']} of {n_track} tracks with at least "
            f"{min_tropical_hours:g} h as a symmetric warm core ({what})",
            file=sys.stderr,
        )
    return res


def _ok_times(t_row):
    import numpy as np

    if np.issubdtype(np.asarray(t_row).dtype, np.datetime64):
        return ~np.isnat(t_row)
    return np.array([t is not None for t in t_row])


def tropical_spells(tropical, t_time, min_hours):
    """Per track, the boolean mask of its tropical spell (see ``keep_tropical``)."""
    import numpy as np

    spells = np.zeros(tropical.shape, bool)
    for k in range(tropical.shape[0]):
        ok = _ok_times(t_time[k])
        if ok.sum() == 0:
            continue
        hours = np.full(ok.size, np.nan)
        hours[ok] = _hours(t_time[k][ok])
        spells[k] = keep_tropical(np.asarray(tropical[k], bool) & ok, hours, min_hours)
    return spells


def phase_labels(spells, t_time):
    """pre-genesis / tropical / extratropical / non-tropical per point ('' without a fix)."""
    import numpy as np

    labels = np.full(spells.shape, "", dtype="<U13")
    for k in range(spells.shape[0]):
        ok = _ok_times(t_time[k])
        idx = np.flatnonzero(spells[k])
        if idx.size == 0:
            labels[k][ok] = "non-tropical"
            continue
        pos = np.arange(ok.size)
        labels[k][ok & (pos < idx[0])] = "pre-genesis"
        labels[k][ok & (pos >= idx[0]) & (pos <= idx[-1])] = "tropical"
        labels[k][ok & (pos > idx[-1])] = "extratropical"
    return labels


def _trim_tropical(res, spells, keep_transition=False):
    """Blank the points outside each track's tropical spell (and, with ``keep_transition``,
    keep what follows it) and drop tracks without a spell."""
    import numpy as np

    keep = spells.copy()
    if keep_transition:
        for k in range(keep.shape[0]):
            idx = np.flatnonzero(spells[k])
            if idx.size:
                keep[k, idx[0] :] = _ok_times(res["time"].values[k])[idx[0] :]
    res = res.copy()
    for name, var in list(res.variables.items()):
        if var.dims != ("track", "obs"):
            continue
        values = var.values.copy()
        if np.issubdtype(values.dtype, np.datetime64):
            values[~keep] = np.datetime64("NaT", "ns")
        elif values.dtype == object:
            values[~keep] = None
        elif values.dtype.kind in "US":
            values[~keep] = ""
        else:
            values = np.where(keep, values, np.nan)
        if name in res.coords:
            res = res.assign_coords({name: var.copy(data=values)})
        else:
            res[name] = var.copy(data=values)
    return res.isel(track=np.flatnonzero(keep.any(axis=1)))


if __name__ == "__main__":
    tc_phase_space()
