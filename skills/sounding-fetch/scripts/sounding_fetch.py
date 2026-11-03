# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "pandas",
#   "requests",
#   "siphon>=0.10",
#   "xarray",
# ]
# ///
"""Fetch observed radiosonde soundings from NOAA's IGRA2 archive (via Siphon) as a station Zarr."""

from __future__ import annotations

import sys
import time
import warnings
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import requests
import xarray as xr
from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_dsg, verify_cf_dsg
from weather_skills_core.units import stamp_data_interval

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside functions, after weather_skills_core: importing core
# (cf_xarray's units) after MetPy puts MetPy's constants in a different pint
# registry.

STATION_LIST = (
    "https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive/doc/igra2-station-list.txt"
)
MANDATORY = (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100)

# Source-to-output transform (IGRA2 via siphon IGRAUpperAir.request_data; units
# from the DataFrame's units dict, converted where noted):
#   temperature, dewpoint (degC)       -> t, dewpoint on levels (degree_Celsius)
#   u_wind, v_wind (m/s)               -> u, v on levels (m s-1)
#   height (m, mandatory levels only)  -> gh on levels (m)
#   the surface level (lvltyp2 == 1)   -> sp (hPa), t2m, d2m (degree_Celsius)
#   every launch                       -> log-pressure interpolation to the --levels grid
VAR_ATTRS = {
    "t": {
        "units": "degree_Celsius",
        "standard_name": "air_temperature",
        "long_name": "temperature",
    },
    "dewpoint": {
        "units": "degree_Celsius",
        "standard_name": "dew_point_temperature",
        "long_name": "dew point temperature",
    },
    "u": {"units": "m s-1", "standard_name": "eastward_wind", "long_name": "eastward wind"},
    "v": {"units": "m s-1", "standard_name": "northward_wind", "long_name": "northward wind"},
    "gh": {
        "units": "m",
        "standard_name": "geopotential_height",
        "long_name": "geopotential height (reported at standard levels, interpolated between)",
    },
    "sp": {
        "units": "hPa",
        "standard_name": "surface_air_pressure",
        "long_name": "pressure at the launch site",
    },
    "t2m": {
        "units": "degree_Celsius",
        "standard_name": "air_temperature",
        "long_name": "temperature at the launch site",
    },
    "d2m": {
        "units": "degree_Celsius",
        "standard_name": "dew_point_temperature",
        "long_name": "dew point at the launch site",
    },
}
LEVEL_VARS = {
    "t": "temperature",
    "dewpoint": "dewpoint",
    "u": "u_wind",
    "v": "v_wind",
    "gh": "height",
}
TARGET_UNITS = {"t": "degC", "dewpoint": "degC", "u": "m/s", "v": "m/s", "gh": "m"}


def _station_list() -> pd.DataFrame:
    """IGRA2's fixed-width station list: id, position, name and years of record."""
    try:
        resp = requests.get(STATION_LIST, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DataError(f"cannot read the IGRA2 station list ({exc}); retry later") from exc
    rows = []
    for line in resp.text.splitlines():
        if len(line) < 81:
            continue
        try:
            rows.append(
                {
                    "station_id": line[0:11].strip(),
                    "latitude": float(line[12:20]),
                    "longitude": float(line[21:30]),
                    "elevation": float(line[31:37]),
                    "name": line[41:71].strip(),
                    "first_year": int(line[72:76]),
                    "last_year": int(line[77:81]),
                }
            )
        except ValueError:
            continue
    return pd.DataFrame(rows)


def _resolve(stations: pd.DataFrame, wanted: list[str], bbox, start, end) -> pd.DataFrame:
    """IGRA2 rows for --station (IGRA2 id or WMO number) and --bbox."""
    picked = []
    for item in wanted:
        key = item.strip().upper()
        if len(key) == 11:
            hit = stations[stations["station_id"] == key]
        else:
            wmo = key.zfill(5)
            hit = stations[
                (stations["station_id"].str[2] == "M") & (stations["station_id"].str[-5:] == wmo)
            ]
        if hit.empty:
            raise UsageError(
                f"--station {item}: not in the IGRA2 station list ({STATION_LIST}); pass the "
                "11-character IGRA2 id or the 5-digit WMO number"
            )
        picked.append(hit)
    if bbox is not None:
        n, w, s, e = bbox
        lon = stations["longitude"]
        inside = (stations["latitude"].between(s, n)) & (
            lon.between(w, e) if w <= e else ((lon >= w) | (lon <= e))
        )
        active = (stations["first_year"] <= end.year) & (stations["last_year"] >= start.year)
        picked.append(stations[inside & active])
    out = pd.concat(picked).drop_duplicates("station_id") if picked else stations.iloc[:0]
    return out.reset_index(drop=True)


def _request(igra_id: str, start, end):
    """All launches of one station in the window: (levels, header), or None."""
    from siphon.simplewebservice.igra2 import IGRAUpperAir

    # Siphon takes naive datetimes, read as UTC.
    window = [
        pd.Timestamp(start).to_pydatetime(),
        (pd.Timestamp(end) + pd.Timedelta(hours=23)).to_pydatetime(),
    ]
    last = None
    for attempt in range(3):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return IGRAUpperAir.request_data(window, igra_id)
        except ValueError as exc:
            # Siphon's "nothing in this window": not a transport error, so no retry.
            if any(text in str(exc).lower() for text in ("no dates match", "no data")):
                return None
            last = exc
        except Exception as exc:  # noqa: BLE001 — transport errors are retried
            last = exc
        time.sleep(5 * (attempt + 1))
    raise DataError(f"IGRA2 download failed for {igra_id} after 3 tries ({last}); retry later")


def _to_levels(df: pd.DataFrame, units: dict, grid: np.ndarray, mu, log_interpolate_1d):
    """One launch on the pressure grid, per variable over its own valid levels."""
    p = mu.Quantity(df["pressure"].to_numpy(float), units.get("pressure", "hPa")).m_as("hPa")
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, col in LEVEL_VARS.items():
            vals = mu.Quantity(df[col].to_numpy(float), units.get(col)).m_as(TARGET_UNITS[name])
            ok = np.isfinite(p) & np.isfinite(vals) & (p > 0)
            if ok.sum() < 2:
                out[name] = np.full(grid.size, np.nan)
                continue
            pp, vv = p[ok], vals[ok]
            pp, first = np.unique(pp, return_index=True)  # one value per reported pressure
            vv = vv[first]
            res = np.asarray(log_interpolate_1d(grid, pp, vv), dtype=float)
            res[(grid > pp.max()) | (grid < pp.min())] = np.nan  # no extrapolation
            out[name] = res
    sfc = df[df["lvltyp2"] == 1] if "lvltyp2" in df else df.iloc[:0]
    if not sfc.empty:
        row = sfc.iloc[0]
        out["sp"] = mu.Quantity(float(row["pressure"]), units.get("pressure", "hPa")).m_as("hPa")
        out["t2m"] = mu.Quantity(float(row["temperature"]), units.get("temperature")).m_as("degC")
        out["d2m"] = mu.Quantity(float(row["dewpoint"]), units.get("dewpoint")).m_as("degC")
    else:
        out["sp"] = out["t2m"] = out["d2m"] = np.nan
    return out


def _grid(levels: str) -> np.ndarray:
    if levels == "mandatory":
        return np.array(MANDATORY, dtype=float)
    try:
        values = [float(x) for x in levels.split(",") if x.strip()]
    except ValueError as exc:
        raise UsageError(
            "--levels takes a step in hPa (10), 'mandatory', or a list (850,700,500)"
        ) from exc
    if len(values) == 1:
        step = values[0]
        if step <= 0:
            raise UsageError("--levels step must be positive")
        return np.arange(1050.0, 100.0 - 1e-9, -step)
    return np.array(sorted(values, reverse=True), dtype=float)


@weather_skill(name="sounding-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--station",
    action="append",
    default=[],
    metavar="ID",
    help="WMO number (63741) or IGRA2 id (KEM00063741); repeatable. With --bbox, both are fetched.",
)
@weather_skill.argument(
    "--levels",
    default="10",
    help="Pressure grid: a step in hPa from 1050 to 100 (default 10), 'mandatory', or a list.",
)
def fetch(start_time, end_time, bbox, station, levels, **kwargs):
    """Fetch observed radiosonde soundings from NOAA's IGRA2 archive (via Siphon) as a station Zarr.

    Every launch of the chosen stations (WMO numbers, IGRA2 ids, or all
    stations in --bbox) in the date range: temperature, dewpoint, wind and
    geopotential height on a common pressure grid, plus the launch-site
    pressure, temperature and dewpoint. The layout feeds
    sounding-indices-metpy and plot-skewt-metpy directly.
    """
    import metpy
    import siphon
    from metpy.interpolate import log_interpolate_1d
    from metpy.units import units as mu

    if not station and bbox is None:
        raise UsageError(
            "pass --station (WMO number or IGRA2 id, repeatable) and/or --bbox N/W/S/E"
        )
    if end_time < start_time:
        raise UsageError("--end-time is before --start-time")
    grid = _grid(levels)

    picked = _resolve(_station_list(), station, bbox, start_time, end_time)
    if picked.empty:
        raise DataError(
            f"no IGRA2 stations with records in {start_time.year}-{end_time.year} inside --bbox"
        )
    print(
        f"sounding-fetch: {len(picked)} station(s): "
        + ", ".join(
            f"{r.station_id} {r.name} ({r.first_year}-{r.last_year})" for r in picked.itertuples()
        ),
        file=sys.stderr,
    )

    launches = {}  # (station_id, time) -> profile dict
    for rec in picked.itertuples():
        if rec.last_year < start_time.year or rec.first_year > end_time.year:
            print(
                f"sounding-fetch: {rec.station_id} {rec.name} has records only for "
                f"{rec.first_year}-{rec.last_year}; skipped",
                file=sys.stderr,
            )
            continue
        res = _request(rec.station_id, start_time, end_time)
        if res is None:
            continue
        df, _header = res
        units = getattr(df, "units", {}) or {}
        for when, one in df.groupby("date"):
            launches[(rec.station_id, pd.Timestamp(when))] = _to_levels(
                one, units, grid, mu, log_interpolate_1d
            )
    if not launches:
        raise DataError(
            f"no launches for {', '.join(picked['station_id'])} in {start_time}..{end_time}. Many "
            "stations launch irregularly or have stopped (IGRA2's list gives each station's last "
            "year); widen the window or try another station"
        )

    have = sorted({sid for sid, _ in launches})
    times = sorted({t for _, t in launches})
    meta = picked.set_index("station_id").loc[have]
    shape3 = (len(have), len(times), grid.size)
    data = {name: np.full(shape3, np.nan) for name in LEVEL_VARS}
    data |= {name: np.full(shape3[:2], np.nan) for name in ("sp", "t2m", "d2m")}
    s_index = {s: i for i, s in enumerate(have)}
    t_index = {t: j for j, t in enumerate(times)}
    for (sid, when), prof in launches.items():
        i, j = s_index[sid], t_index[when]
        for name in LEVEL_VARS:
            data[name][i, j] = prof[name]
        for name in ("sp", "t2m", "d2m"):
            data[name][i, j] = prof[name]

    ds = xr.Dataset(
        {
            **{n: (("station_id", "time", "level"), data[n]) for n in LEVEL_VARS},
            **{n: (("station_id", "time"), data[n]) for n in ("sp", "t2m", "d2m")},
        },
        coords={
            "station_id": have,
            "time": pd.DatetimeIndex(times).values.astype("datetime64[ns]"),
            "level": (
                "level",
                grid,
                {
                    "units": "hPa",
                    "standard_name": "air_pressure",
                    "long_name": "pressure",
                    "positive": "down",
                    "axis": "Z",
                },
            ),
            "latitude": ("station_id", meta["latitude"].to_numpy(float)),
            "longitude": ("station_id", meta["longitude"].to_numpy(float)),
            "elevation": (
                "station_id",
                meta["elevation"].to_numpy(float),
                {
                    "units": "m",
                    "standard_name": "height_above_mean_sea_level",
                    "long_name": "station elevation",
                },
            ),
            "name": ("station_id", meta["name"].astype(str).to_numpy()),
        },
    )
    stamp_cf_dsg(
        ds,
        VAR_ATTRS,
        station_id_long_name="IGRA2 station identifier",
        name_long_name="station name",
    )
    ds.attrs.update(
        Conventions="CF-1.13",
        featureType="timeSeriesProfile",
        title="Radiosonde soundings (IGRA2)",
        source="NOAA NCEI Integrated Global Radiosonde Archive v2 (IGRA2), read with Siphon; "
        "interpolated in log-pressure with MetPy",
        institution="NOAA National Centers for Environmental Information",
        references="https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive",
        history=f"{datetime.now(UTC).isoformat()} sounding-fetch {start_time}..{end_time}",
        weather_skills_source="igra2",
        siphon_version=siphon.__version__,
        metpy_version=metpy.__version__,
    )
    verify_cf_dsg(ds)
    per_station = ", ".join(
        f"{sid} {int(ds['t'].sel(station_id=sid).notnull().any('level').sum())}" for sid in have
    )
    print(
        f"sounding-fetch: launches per station: {per_station}; {grid.size} levels", file=sys.stderr
    )
    # Even launch spacing gets data_interval, irregular spacing CF time bounds.
    ds = stamp_data_interval(ds)
    return ds


if __name__ == "__main__":
    fetch()
