# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "tropycal==1.5.2",
#   # tropycal imports matplotlib; <3.10 matches the plot skills
#   "matplotlib>=3.8,<3.10",
#   # tropycal imports shapely and cartopy at module level (tropycal.utils) but does not
#   # declare them, so `import tropycal.tracks` / `tropycal.realtime` fails without these
#   "shapely>=2.0",
#   "cartopy>=0.22",
#   "numpy",
#   "pandas",
#   "xarray",
#   "zarr",
# ]
# ///
"""Fetch official tropical cyclone tracks (best tracks, official forecasts, a-deck model tracks)
with tropycal, in the tc-track trajectory layout."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

KT_TO_MS = 1852.0 / 3600.0
# Intensity classes on the wind in knots: same edges as tc-track's `category`.
CATEGORY_KT = (34.0, 64.0, 83.0, 96.0)
CATEGORY_NAMES = (
    "tropical_depression",
    "tropical_storm",
    "category_1",
    "category_2",
    "category_3_or_more",
)

BASINS = (
    "north_atlantic",
    "east_pacific",
    "west_pacific",
    "north_indian",
    "south_indian",
    "south_pacific",
    "south_atlantic",
)
HURDAT_BASINS = ("north_atlantic", "east_pacific")
IBTRACS_CODE = {
    "north_atlantic": "NA",
    "east_pacific": "EP",
    "west_pacific": "WP",
    "north_indian": "NI",
    "south_indian": "SI",
    "south_pacific": "SP",
    "south_atlantic": "SA",
    "active": "ACTIVE",
}
IBTRACS_URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-"
    "ibtracs/v04r01/access/csv/ibtracs.{code}.list.v04r01.csv"
)
# ATCF id prefix -> basins to search (SH covers both southern basins).
ID_BASINS = {
    "AL": ("north_atlantic",),
    "EP": ("east_pacific",),
    "CP": ("east_pacific",),
    "WP": ("west_pacific",),
    "IO": ("north_indian",),
    "SH": ("south_indian", "south_pacific"),
    "SL": ("south_atlantic",),
}
# Rough basin boxes (S, N, W, E; W > E crosses the dateline) to pick basins from --bbox.
BASIN_BOXES = {
    "north_atlantic": (0.0, 70.0, -110.0, 20.0),
    "east_pacific": (0.0, 60.0, -180.0, -75.0),
    "west_pacific": (0.0, 60.0, 95.0, 180.0),
    "north_indian": (0.0, 35.0, 30.0, 100.0),
    "south_indian": (-50.0, 0.0, 10.0, 135.0),
    "south_pacific": (-50.0, 0.0, 135.0, -120.0),
    "south_atlantic": (-50.0, 0.0, -70.0, 20.0),
}
# a-deck model groups; ensemble members get `number` (control 0).
MODEL_GROUPS = {
    "ecmwf-ens": ["EC00", *[f"EE{i:02d}" for i in range(1, 51)]],
    "ecmwf-mean": ["EEMN"],
    "ecmwf": ["ECMO", "EMX"],
    "gefs-ens": ["AC00", *[f"AP{i:02d}" for i in range(1, 31)]],
    "gefs-mean": ["AEMN"],
    "gfs": ["AVNO"],
    "official": ["OFCL"],
}
# Official system types -> the `phase` labels tc-phase-space writes for model tracks.
TROPICAL_TYPES = {"TD", "TS", "HU", "TY", "ST", "TC", "SD", "SS", "MH", "TS/TY"}
EXTRATROPICAL_TYPES = {"EX", "ET"}
CACHE_DAYS = 7.0
ACTIVE_DAYS = 7.0  # an IBTrACS ACTIVE storm counts as current when seen this recently
USER_AGENT = "chc-skills tc-besttrack-fetch"


def wind_category(wind_ms):
    """0 TD (<34 kt), 1 TS (34-63), 2 Cat 1 (64-82), 3 Cat 2 (83-95), 4 Cat 3+ (>=96); NaN if NaN."""
    import numpy as np

    wind_ms = np.asarray(wind_ms, dtype=float)
    cat = np.digitize(wind_ms / KT_TO_MS, CATEGORY_KT).astype(float)
    return np.where(np.isfinite(wind_ms), cat, np.nan)


def phase_from_types(types):
    """pre-genesis / tropical / extratropical / remnant per fix from official system types.

    Lows, disturbances and waves (LO, DB, WV, ...) before the first tropical fix are
    pre-genesis, after it remnant; EX/ET are extratropical. A track without a tropical
    fix is labelled by type alone.
    """
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


# --------------------------------------------------------------------------- cache


def cache_dir() -> Path:
    root = os.environ.get("TC_BESTTRACK_CACHE")
    path = Path(root) if root else Path.home() / ".cache" / "chc-skills" / "tc-besttrack"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fresh(path: Path, days: float) -> bool:
    return path.is_file() and (time.time() - path.stat().st_mtime) < days * 86400


def _download(url: str, path: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise DataError(f"could not download {url}: {exc}") from exc
    tmp.replace(path)
    return path


def cached_ibtracs(code: str, refresh: bool, days: float = CACHE_DAYS) -> Path:
    path = cache_dir() / f"ibtracs.{code}.list.v04r01.csv"
    if refresh or not _fresh(path, days):
        print(f"downloading IBTrACS {code} to {path}", file=sys.stderr)
        _download(IBTRACS_URL.format(code=code), path)
    return path


def cached_hurdat(basin: str, refresh: bool) -> Path:
    path = cache_dir() / f"hurdat2.{basin}.txt"
    if refresh or not _fresh(path, CACHE_DAYS):
        from tropycal.tracks.dataset import find_latest_hurdat_files

        try:
            atlantic, pacific = find_latest_hurdat_files()
        except Exception as exc:
            raise DataError(f"could not find the latest HURDAT2 files: {exc}") from exc
        url = atlantic if basin == "north_atlantic" else pacific
        print(f"downloading HURDAT2 {basin} ({url}) to {path}", file=sys.stderr)
        _download(url, path)
    return path


@contextlib.contextmanager
def _quiet():
    """tropycal prints progress lines on stdout; keep stdout clean."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield
    for line in buf.getvalue().splitlines():
        if line.strip():
            print(line, file=sys.stderr)


def load_dataset(basin: str, source: str, ibtracs_mode: str, refresh: bool):
    """A tropycal TrackDataset read from the local cache."""
    import tropycal.tracks as tt

    with _quiet():
        if source == "hurdat":
            path = str(cached_hurdat(basin, refresh))
            key = "atlantic_url" if basin == "north_atlantic" else "pacific_url"
            return tt.TrackDataset(basin=basin, source="hurdat", **{key: path})
        code = IBTRACS_CODE[basin]
        path = str(cached_ibtracs(code, refresh))
        return tt.TrackDataset(
            basin="all" if basin == "active" else basin,
            source="ibtracs",
            ibtracs_mode=ibtracs_mode,
            ibtracs_url=path,
        )


# --------------------------------------------------------------------------- tracks


def _row(storm_id, name, kind, times, lat, lon, vmax, mslp, types, init=None, number=None):
    return {
        "storm": storm_id,
        "name": name,
        "kind": kind,
        "times": list(times),
        "lat": list(lat),
        "lon": list(lon),
        "vmax": list(vmax),
        "mslp": list(mslp),
        "types": [str(t) for t in types],
        "init": init,
        "number": number,
    }


def storm_row(storm, kind="best_track"):
    """One tropycal Storm (or RealtimeStorm) as a track row."""
    a = storm.attrs
    return _row(
        a.get("id"),
        a.get("name"),
        kind,
        storm.time,
        storm.lat,
        storm.lon,
        storm.vmax,
        storm.mslp,
        storm.type,
    )


def forecast_row(storm_id, name, kind, fc, number=None):
    """An a-deck or official forecast dict (init, fhr, lat, lon, vmax, mslp, type) as a row."""
    import numpy as np

    init = fc["init"]
    times = [init + timedelta(hours=float(h)) for h in fc["fhr"]]
    n = len(times)

    def get(key, fill):
        vals = list(fc.get(key) or [])
        return (vals + [fill] * n)[:n]

    return _row(
        storm_id,
        name,
        kind,
        times,
        get("lat", np.nan),
        get("lon", np.nan),
        get("vmax", np.nan),
        get("mslp", np.nan),
        get("type", ""),
        init=init,
        number=number,
    )


def _clean(values, positive=True):
    import numpy as np

    out = np.array([np.nan if v is None else v for v in values], dtype=float)
    if positive:
        out[out <= 0] = np.nan
    return out


def to_dataset(rows, attrs):
    """Track rows as the tc-track trajectory layout on (track, obs), padded with NaN."""
    import numpy as np
    import xarray as xr

    rows = [r for r in rows if len(r["times"])]
    n, n_obs = len(rows), max([len(r["times"]) for r in rows], default=1)
    shape = (n, n_obs)
    times = np.full(shape, np.datetime64("NaT", "ns"))
    arrays = {k: np.full(shape, np.nan) for k in ("lat", "lon", "vmax", "mslp")}
    types = np.full(shape, "", dtype=object)
    for k, r in enumerate(rows):
        m = len(r["times"])
        times[k, :m] = np.array(
            [
                np.datetime64(t.replace(tzinfo=None) if hasattr(t, "tzinfo") else t, "ns")
                for t in r["times"]
            ]
        )
        arrays["lat"][k, :m] = _clean(r["lat"], positive=False)
        lon = _clean(r["lon"], positive=False)
        arrays["lon"][k, :m] = ((lon + 180.0) % 360.0) - 180.0
        arrays["vmax"][k, :m] = _clean(r["vmax"])
        arrays["mslp"][k, :m] = _clean(r["mslp"])
        types[k, :m] = r["types"][:m]
    # Points without a position are not track points.
    bad = ~np.isfinite(arrays["lat"]) | ~np.isfinite(arrays["lon"])
    for arr in arrays.values():
        arr[bad] = np.nan
    times[bad] = np.datetime64("NaT", "ns")
    types[bad] = ""

    wind = arrays["vmax"] * KT_TO_MS
    ids = []
    for r in rows:
        tag = r["storm"] or "unknown"
        if r["kind"] != "best_track":
            tag += f":{r['kind'].split(':', 1)[-1]}"
            if r["init"] is not None:
                tag += f":{r['init']:%Y%m%d%H}"
        ids.append(tag)
    dims = ("track", "obs")
    ds = xr.Dataset(
        {
            "msl": (
                dims,
                arrays["mslp"] * 100.0,
                {
                    "units": "Pa",
                    "long_name": "central mean sea level pressure",
                    "standard_name": "air_pressure_at_mean_sea_level",
                },
            ),
            "wind_max": (
                dims,
                wind,
                {
                    "units": "m s-1",
                    "long_name": "maximum sustained wind (official estimate)",
                    "comment": attrs.get("tc_wind_averaging", ""),
                },
            ),
            "category": (
                dims,
                wind_category(wind),
                {
                    "units": "1",
                    "long_name": "intensity class from wind_max in knots",
                    "flag_values": list(range(len(CATEGORY_NAMES))),
                    "flag_meanings": " ".join(CATEGORY_NAMES),
                    "category_edges_kt": list(CATEGORY_KT),
                },
            ),
            "storm_type": (
                dims,
                types.astype(str),
                {"long_name": "official system type (TD, TS, HU, TY, EX, SS, LO, DB, ...)"},
            ),
            "phase": (
                dims,
                np.array([phase_from_types(row) for row in types.astype(str)], dtype="<U13")
                if n
                else np.full(shape, "", dtype="<U13"),
                {
                    "long_name": "phase from the official type: pre-genesis, tropical, "
                    "extratropical or remnant",
                },
            ),
        },
        coords={
            "track": ("track", np.array(ids, dtype=str), {"cf_role": "trajectory_id"}),
            "obs": ("obs", np.arange(n_obs)),
            "time": (dims, times, {"standard_name": "time", "long_name": "valid time"}),
            "latitude": (
                dims,
                arrays["lat"],
                {"standard_name": "latitude", "units": "degrees_north"},
            ),
            "longitude": (
                dims,
                arrays["lon"],
                {"standard_name": "longitude", "units": "degrees_east"},
            ),
            "storm": ("track", np.array([r["storm"] or "" for r in rows], dtype=str)),
            "storm_name": ("track", np.array([r["name"] or "" for r in rows], dtype=str)),
            "kind": ("track", np.array([r["kind"] for r in rows], dtype=str)),
        },
        attrs={"featureType": "trajectory", **attrs},
    )
    if any(r["init"] is not None for r in rows):
        ds = ds.assign_coords(
            forecast_reference_time=(
                "track",
                np.array(
                    [
                        np.datetime64(r["init"], "ns") if r["init"] else np.datetime64("NaT", "ns")
                        for r in rows
                    ]
                ),
            )
        )
    if rows and all(r["number"] is not None for r in rows):
        ds = ds.assign_coords(number=("track", np.array([int(r["number"]) for r in rows])))
        ds.attrs["ensemble_members"] = sorted({int(r["number"]) for r in rows})
    return ds


# --------------------------------------------------------------------------- selection


def _bbox_basins(bbox):
    north, west, south, east = bbox

    def lon_ranges(w, e):
        return [(w, e)] if w <= e else [(w, 180.0), (-180.0, e)]

    out = []
    for basin, (s, n, w, e) in BASIN_BOXES.items():
        if s > north or n < south:
            continue
        if any(a < d and c < b for a, b in lon_ranges(west, east) for c, d in lon_ranges(w, e)):
            out.append(basin)
    return out


def _in_bbox(lat, lon, bbox):
    import numpy as np

    north, west, south, east = bbox
    lat, lon = np.asarray(lat, float), ((np.asarray(lon, float) + 180) % 360) - 180
    inside_lon = (lon >= west) & (lon <= east) if west <= east else (lon >= west) | (lon <= east)
    return (lat <= north) & (lat >= south) & inside_lon


def _sources(basin, source):
    if source == "auto":
        return ["hurdat", "ibtracs"] if basin in HURDAT_BASINS else ["ibtracs"]
    if source == "hurdat" and basin not in HURDAT_BASINS:
        raise UsageError(f"HURDAT2 covers {', '.join(HURDAT_BASINS)} only, not {basin}")
    return [source]


def find_storm(storm, year, storm_id, basin, source, ibtracs_mode, refresh):
    """The tropycal Storm for a name and year or an id; searches basins in turn."""
    if storm_id:
        basins = [basin] if basin != "auto" else list(ID_BASINS.get(storm_id[:2].upper(), BASINS))
    else:
        basins = [basin] if basin != "auto" else list(BASINS)
    misses = []
    for b in basins:
        for src in _sources(b, source):
            ds = load_dataset(b, src, ibtracs_mode, refresh)
            if storm_id:
                if storm_id.upper() in ds.data:
                    return ds.get_storm(storm_id.upper()), src
                continue
            try:
                key = ds.get_storm_id((storm, int(year)))
            except Exception:  # noqa: BLE001 - tropycal raises RuntimeError / ValueError
                key = None
            if isinstance(key, list):
                raise UsageError(
                    f"{storm} {year} is ambiguous in {b}: {', '.join(key)}; pass --storm-id"
                )
            if key:
                return ds.get_storm(key), src
            with contextlib.suppress(Exception):
                years = ds.search_name(storm)
                if years:
                    misses.append(f"{b}: {', '.join(str(y) for y in years)}")
    what = storm_id or f"{storm} {year}"
    hint = f"; the name is used in {'; '.join(misses)}" if misses else ""
    raise UsageError(f"no storm {what} in {', '.join(basins)}{hint}")


def storms_in_window(start, end, basins, bbox, source, ibtracs_mode, refresh):
    rows, used = [], set()
    t0 = datetime.combine(start, datetime.min.time())
    t1 = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
    for b in basins:
        srcs = _sources(b, source)
        # HURDAT2 lags a season; take IBTrACS for windows it does not reach.
        for src in srcs:
            ds = load_dataset(b, src, ibtracs_mode, refresh)
            found = 0
            for key, d in ds.data.items():
                if key in used:
                    continue
                times = d["time"]
                if not times or times[-1] < t0 or times[0] >= t1:
                    continue
                if bbox is not None:
                    import numpy as np

                    sel = np.array([t0 <= t < t1 for t in times])
                    if not _in_bbox(
                        np.asarray(d["lat"])[sel], np.asarray(d["lon"])[sel], bbox
                    ).any():
                        continue
                rows.append(storm_row(ds.get_storm(key)))
                used.add(key)
                found += 1
            last = max((d["time"][-1] for d in ds.data.values() if d["time"]), default=None)
            if last is not None and last >= t1:
                break  # this source covers the window
    return rows


def model_rows(storm, models, init, name):
    """a-deck tracks of ``models`` (ids or groups) for one init."""
    with _quiet():
        try:
            fc = storm.get_operational_forecasts()
        except Exception as exc:
            raise DataError(f"no operational forecasts for {storm.attrs.get('id')}: {exc}") from exc
    ids, numbers = [], {}
    for token in models:
        group = MODEL_GROUPS.get(token.lower())
        if group is None:
            ids.append(token.upper())
            continue
        ids += group
        if token.lower().endswith("-ens"):
            for k, mid in enumerate(group):
                numbers[mid] = k
    key = f"{init:%Y%m%d%H}"
    rows, missing = [], []
    for mid in ids:
        runs = fc.get(mid, {})
        if key not in runs:
            missing.append(mid)
            continue
        rows.append(
            forecast_row(storm.attrs["id"], name, f"adeck:{mid}", runs[key], numbers.get(mid))
        )
    if not rows:
        have = sorted({k for runs in fc.values() for k in runs})
        near = [k for k in have if abs((_atcf_time(k) - init).days) <= 1]
        raise UsageError(
            f"none of {', '.join(ids[:6])}{'...' if len(ids) > 6 else ''} has an init {key}; "
            f"inits within a day: {', '.join(near) or 'none'}. Models with runs: "
            f"{', '.join(sorted(fc)[:40])}"
        )
    if missing:
        print(f"note: no {key} run for {', '.join(missing)}", file=sys.stderr)
    if numbers and any(r["number"] is None for r in rows):
        for r in rows:  # mixed ensemble and single models: no member numbers
            r["number"] = None
    return rows


def active_rows(refresh):
    """Current storms: NHC (and JTWC when reachable) with official forecasts; other basins
    from IBTrACS ACTIVE."""
    from tropycal import realtime

    rows, notes, seen = [], [], set()
    rt = None
    for kwargs in ({"jtwc": True, "jtwc_source": "ucar"}, {"jtwc": False}):
        try:
            with _quiet():
                rt = realtime.Realtime(**kwargs)
            if not kwargs["jtwc"]:
                notes.append("JTWC realtime data unreachable; NHC basins only from realtime")
            break
        except Exception as exc:  # noqa: BLE001 - network/SSL failures
            print(f"note: realtime {kwargs} failed: {exc}", file=sys.stderr)
    if rt is not None:
        for sid in rt.list_active_storms():
            s = rt.get_storm(sid)
            row = storm_row(s)
            row["kind"] = "best_track"
            rows.append(row)
            seen.add(sid)
            if s.attrs.get("invest"):
                continue
            try:
                with _quiet():
                    fc = s.get_forecast_realtime()
                rows.append(forecast_row(sid, s.attrs.get("name"), "official_forecast", fc))
            except Exception as exc:  # noqa: BLE001
                print(f"note: no official forecast for {sid}: {exc}", file=sys.stderr)
    ds = load_dataset(
        "active",
        "ibtracs",
        "jtwc",
        refresh or not _fresh(cache_dir() / "ibtracs.ACTIVE.list.v04r01.csv", 0.5),
    )
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=ACTIVE_DAYS)
    for key, d in ds.data.items():
        if key in seen or not d["time"] or d["time"][-1] < cutoff:
            continue
        rows.append(storm_row(ds.get_storm(key)))
        notes.append(
            f"{key} {d.get('name', '')} from IBTrACS ACTIVE (last fix {d['time'][-1]:%Y-%m-%d %H} UTC)"
        )
    return rows, notes


# --------------------------------------------------------------------------- skill


def _atcf_time(key):
    """An a-deck init key YYYYMMDDHH as a naive UTC datetime (tropycal's convention)."""
    import pandas as pd

    return pd.Timestamp(f"{key[:8]}T{key[8:10]}").to_pydatetime()


def _parse_init(value):
    """--init as a naive UTC datetime; accepts YYYY-MM-DDTHH[:MM], YYYYMMDDHH or a date."""
    import pandas as pd

    if value is None:
        return None
    text = value.strip()
    if text.isdigit() and len(text) == 10:
        return _atcf_time(text)
    try:
        ts = pd.Timestamp(text if ":" in text or len(text) <= 10 else f"{text}:00")
    except ValueError as exc:
        raise UsageError(f"--init {value!r}: use YYYY-MM-DDTHH") from exc
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def _parse_date(value, flag):
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise UsageError(f"{flag} {value!r}: use YYYY-MM-DD") from exc


@weather_skill(name="tc-besttrack-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--storm", default=None, help="Storm name, with --year (e.g. Freddy).")
@weather_skill.argument("--year", type=int, default=None, help="Season year for --storm.")
@weather_skill.argument(
    "--storm-id", default=None, help="Official id instead of a name: AL122005, SH112023, ..."
)
@weather_skill.argument(
    "--start", default=None, help="YYYY-MM-DD: every storm active from this day (with --end)."
)
@weather_skill.argument("--end", default=None, help="YYYY-MM-DD: last day of the window.")
@weather_skill.argument(
    "--active",
    action="store_true",
    help="Current storms with their official forecasts (NHC/JTWC realtime; other basins "
    "from IBTrACS ACTIVE).",
)
@weather_skill.argument(
    "--basin",
    default="auto",
    choices=["auto", *BASINS],
    help="Basin to search. auto: from --storm-id, --bbox, or every basin in turn.",
)
@weather_skill.argument(
    "--bbox", default=None, help="N/W/S/E: with --start/--end, storms passing it."
)
@weather_skill.argument(
    "--source",
    default="auto",
    choices=["auto", "hurdat", "ibtracs"],
    help="auto: HURDAT2 for the North Atlantic and East Pacific (IBTrACS when HURDAT2 lacks "
    "the storm), IBTrACS elsewhere.",
)
@weather_skill.argument(
    "--ibtracs-mode",
    default="jtwc",
    choices=["jtwc", "wmo"],
    help="IBTrACS values: jtwc (US agencies, 1-minute winds; default) or wmo (the RSMCs, "
    "mostly 10-minute winds).",
)
@weather_skill.argument(
    "--models",
    default=None,
    help="Comma list of a-deck models instead of the best track: ids (EMX, OFCL, AVNO, ...) "
    "or groups ecmwf-ens, ecmwf-mean, ecmwf, gefs-ens, gefs-mean, gfs, official. Needs "
    "--init and one storm.",
)
@weather_skill.argument("--init", default=None, help="a-deck init YYYY-MM-DDTHH for --models.")
@weather_skill.argument(
    "--refresh", action="store_true", help="Download the best-track files again."
)
def fetch(
    storm,
    year,
    storm_id,
    start,
    end,
    active,
    basin,
    bbox,
    source,
    ibtracs_mode,
    models,
    init,
    refresh,
    **kwargs,
):
    """Fetch official tropical cyclone tracks with tropycal (HURDAT2, IBTrACS, NHC/JTWC
    realtime, a-deck model forecasts) in the tc-track trajectory layout (track, obs)."""
    modes = [bool(storm or storm_id), bool(start or end), bool(active)]
    if sum(modes) != 1:
        raise UsageError("give exactly one of --storm/--storm-id, --start/--end or --active")
    if storm and storm_id:
        raise UsageError("give --storm with --year, or --storm-id, not both")
    if storm and year is None:
        raise UsageError("--storm needs --year (names are reused)")
    if models and not (storm or storm_id):
        raise UsageError("--models needs one storm (--storm/--year or --storm-id)")
    if models and init is None:
        raise UsageError("--models needs --init YYYY-MM-DDTHH")
    bbox = tuple(float(v) for v in bbox) if bbox is not None else None

    winds = (
        "1-minute sustained (NHC/JTWC)"
        if ibtracs_mode == "jtwc" or source == "hurdat"
        else "agency averaging (10-minute for most RSMCs, 1-minute for NHC/JTWC)"
    )
    attrs = {"tc_wind_averaging": winds, "tc_source_tool": "tropycal 1.5.2"}
    notes = []
    if storm or storm_id:
        st, src = find_storm(storm, year, storm_id, basin, source, ibtracs_mode, refresh)
        a = st.attrs
        attrs.update(
            tc_source=src,
            tc_source_info=str(a.get("source_info", "")),
            storm_id=str(a.get("id")),
            storm_name=str(a.get("name")),
            storm_year=int(a.get("year")),
        )
        if models:
            rows = model_rows(
                st,
                [m.strip() for m in models.split(",") if m.strip()],
                _parse_init(init),
                a.get("name"),
            )
            attrs["tc_models"] = models
            attrs["tc_wind_averaging"] = "model output (a-deck)"
        else:
            rows = [storm_row(st)]
    elif active:
        rows, notes = active_rows(refresh)
        attrs.update(
            tc_source="realtime+ibtracs_active", tc_fetched=f"{datetime.now(UTC):%Y-%m-%dT%H:%M}Z"
        )
    else:
        t0, t1 = _parse_date(start, "--start"), _parse_date(end, "--end")
        if t0 is None or t1 is None or t1 < t0:
            raise UsageError("--start and --end are both needed, with --end on or after --start")
        if basin != "auto":
            basins = [basin]
        elif bbox is not None:
            basins = _bbox_basins(bbox)
        else:
            raise UsageError("--start/--end need --basin or --bbox")
        rows = storms_in_window(t0, t1, basins, bbox, source, ibtracs_mode, refresh)
        attrs.update(tc_source=source, tc_window=f"{t0} to {t1}", tc_basins=", ".join(basins))
        if bbox is not None:
            attrs["tc_bbox"] = "/".join(f"{v:g}" for v in bbox)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if notes:
        attrs["tc_notes"] = "; ".join(notes)
    if not rows:
        raise DataError("no tracks found")
    ds = to_dataset(rows, attrs)
    summary = ", ".join(
        sorted(
            {
                f"{s} {n}".strip()
                for s, n in zip(ds["storm"].values, ds["storm_name"].values, strict=True)
            }
        )
    )
    print(f"{ds.sizes['track']} tracks: {summary}", file=sys.stderr)
    return ds


if __name__ == "__main__":
    fetch()
