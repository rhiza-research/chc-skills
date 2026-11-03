# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "pandas",
#   "requests",
#   "xarray",
# ]
# ///
"""Fetch METAR airport weather reports (decoded with MetPy) as a weather-skills station Zarr."""

from __future__ import annotations

import io
import re
import sys
import time
import warnings
from datetime import UTC, datetime, timedelta

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

IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
IEM_NETWORK = "https://mesonet.agron.iastate.edu/geojson/network/{cc}__ASOS.geojson"
INTERVALS = {"30min": "30 minute", "1h": "1 hour", "3h": "3 hour"}
INHG_TO_HPA = 33.8639

# Source-to-output transform (raw METAR text from IEM, decoded by
# metpy.io.metar.parse_metar; everything else passes through):
#   temperature / dewpoint (degC)      -> air_temperature, dew_point_temperature (degree_Celsius)
#   wind_direction (deg, VRB = NaN)    -> wind_from_direction (degree)
#   wind_speed / wind_gust (knots)     -> wind_speed, wind_speed_of_gust (knot)
#   visibility (m, 9999 = 10 km+)      -> visibility (m)
#   altimeter (MetPy gives inHg)       -> qnh in hPa: value conversion back to the Q group's hPa
#   cloudcover (oktas, 10 = missing)   -> cloud_cover (oktas 0-8, 9 = sky obscured; 10 -> NaN)
#   skylev1 (ft)                       -> cloud_base_height (ft), lowest reported layer
#   current_wx1..3                     -> present_weather (text, space-separated)
#   current_wx1_symbol                 -> present_weather_code (MetPy's current_weather symbol code)
#   derived from all reports in a slot -> thunderstorm, precipitation, cumulonimbus (0/1)
#   the chosen report                  -> metar (text)
VAR_ATTRS = {
    "air_temperature": {
        "units": "degree_Celsius",
        "standard_name": "air_temperature",
        "long_name": "air temperature",
    },
    "dew_point_temperature": {
        "units": "degree_Celsius",
        "standard_name": "dew_point_temperature",
        "long_name": "dew point temperature",
    },
    "wind_from_direction": {
        "units": "degree",
        "standard_name": "wind_from_direction",
        "long_name": "wind direction (from); NaN when variable",
    },
    "wind_speed": {"units": "knot", "standard_name": "wind_speed", "long_name": "wind speed"},
    "wind_speed_of_gust": {
        "units": "knot",
        "standard_name": "wind_speed_of_gust",
        "long_name": "wind gust (NaN when none reported)",
    },
    "visibility": {
        "units": "m",
        "standard_name": "visibility_in_air",
        "long_name": "prevailing visibility (9999 means 10 km or more)",
    },
    "qnh": {"units": "hPa", "long_name": "QNH altimeter setting (sea-level pressure, ICAO)"},
    "cloud_cover": {
        "units": "1",
        "long_name": "total cloud cover in oktas (0-8; 9 = sky obscured)",
    },
    "cloud_base_height": {
        "units": "ft",
        "long_name": "base of the lowest reported cloud layer above the airfield",
    },
    "present_weather": {"long_name": "present weather groups (e.g. -TSRA, BR)"},
    "present_weather_code": {
        "units": "1",
        "long_name": "present weather code for MetPy's current_weather symbols",
    },
    "thunderstorm": {
        "units": "1",
        "long_name": "thunderstorm (TS, incl. in the vicinity) in any report in the slot",
    },
    "precipitation": {
        "units": "1",
        "long_name": "precipitation (DZ, RA, SN, GR, ...) in any report in the slot",
    },
    "cumulonimbus": {
        "units": "1",
        "long_name": "cumulonimbus (CB) cloud in any report in the slot",
    },
    "metar": {"long_name": "the METAR report used for the slot"},
}
_PRECIP = re.compile(r"DZ|RA|SN|SG|PL|GR|GS|UP")
_CB = re.compile(r"\b(?:FEW|SCT|BKN|OVC|VV)\d{3}CB\b")


def _bbox_stations(bbox) -> pd.DataFrame:
    """ICAO stations inside the bbox: countries from MetPy's station table, then IEM's lists."""
    from metpy.io import station_info

    n, w, s, e = bbox
    # MetPy keeps its station tables as a ChainMap (internal, hence the guard).
    tables = getattr(getattr(station_info, "tables", None), "maps", None)
    if not tables:
        raise DataError("cannot read MetPy's station table to search --bbox; pass --station ICAO")
    countries = set()
    for table in tables:
        for rec in table.values():
            lat, lon = getattr(rec, "latitude", None), getattr(rec, "longitude", None)
            if lat is None or lon is None:
                continue
            inside_lon = w <= lon <= e if w <= e else (lon >= w or lon <= e)
            if s <= lat <= n and inside_lon and getattr(rec, "country", None):
                countries.add(rec.country)
    rows = []
    for cc in sorted(countries):
        try:
            resp = requests.get(IEM_NETWORK.format(cc=cc), timeout=60)
            resp.raise_for_status()
            features = resp.json().get("features", [])
        except (requests.RequestException, ValueError):
            continue
        for f in features:
            lon, lat = f["geometry"]["coordinates"][:2]
            inside_lon = w <= lon <= e if w <= e else (lon >= w or lon <= e)
            if s <= lat <= n and inside_lon:
                rows.append(
                    {"station_id": f["properties"]["sid"], "name": f["properties"].get("sname")}
                )
    return pd.DataFrame(rows, columns=["station_id", "name"]).drop_duplicates("station_id")


def _download(station: str, start, end) -> pd.DataFrame:
    """Raw METARs for one station from IEM (UTC, routine and special reports)."""
    stop = end + timedelta(days=1)  # IEM's end date is exclusive
    params = {
        "station": station,
        "data": "metar",
        "year1": start.year,
        "month1": start.month,
        "day1": start.day,
        "year2": stop.year,
        "month2": stop.month,
        "day2": stop.day,
        "tz": "Etc/UTC",
        "format": "onlycomma",
        "latlon": "yes",
        "elev": "yes",
        "missing": "empty",
        "direct": "no",
        "report_type": ["3", "4"],
    }
    last = None
    for attempt in range(4):
        try:
            resp = requests.get(IEM_ASOS, params=params, timeout=300)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return pd.read_csv(io.StringIO(resp.text), dtype={"metar": str})
        except (requests.RequestException, pd.errors.ParserError) as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise DataError(
        f"IEM METAR archive unreachable for {station} after 4 tries ({last}); "
        "retry later, it is a shared free service"
    )


def _decode(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Decode each report with MetPy's parse_metar; returns (rows, reports skipped)."""
    import metpy.io.metar as mm

    rows, skipped = [], 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for rec in raw.itertuples(index=False):
            text = str(rec.metar)
            valid = pd.Timestamp(rec.valid)
            try:
                m = mm.parse_metar(text, valid.year, valid.month)
            except Exception:  # noqa: BLE001 — a malformed report is skipped, not fatal
                skipped += 1
                continue
            wx = " ".join(
                str(x)
                for x in (m.current_wx1, m.current_wx2, m.current_wx3)
                if isinstance(x, str) and x
            )
            altimeter = float(np.ravel(m.altimeter)[0]) if m.altimeter is not None else np.nan
            cover = float(m.cloudcover) if m.cloudcover is not None else np.nan
            rows.append(
                {
                    "station_id": rec.station,
                    "valid": valid,
                    "latitude": rec.lat,
                    "longitude": rec.lon,
                    "elevation": rec.elevation,
                    "air_temperature": m.temperature,
                    "dew_point_temperature": m.dewpoint,
                    "wind_from_direction": m.wind_direction,
                    "wind_speed": m.wind_speed,
                    "wind_speed_of_gust": m.wind_gust,
                    "visibility": m.visibility,
                    "qnh": altimeter * INHG_TO_HPA,
                    "cloud_cover": np.nan if cover >= 10 else cover,
                    "cloud_base_height": m.skylev1,
                    "present_weather": wx,
                    "present_weather_code": m.current_wx1_symbol,
                    "thunderstorm": float("TS" in wx),
                    "precipitation": float(bool(_PRECIP.search(wx))),
                    "cumulonimbus": float(bool(_CB.search(text))),
                    "metar": text,
                }
            )
    return pd.DataFrame(rows), skipped


@weather_skill(name="metar-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--station",
    action="append",
    default=[],
    metavar="ICAO",
    help="ICAO airport code, e.g. HKJK; repeatable. With --bbox, both sets are fetched.",
)
@weather_skill.argument(
    "--interval",
    choices=tuple(INTERVALS),
    default="1h",
    help="Time step of the output: the report nearest each step. Default: 1h.",
)
def fetch(start_time, end_time, bbox, station, interval, **kwargs):
    """Fetch METAR airport weather reports (decoded with MetPy) as a weather-skills station Zarr.

    Downloads the raw half-hourly METARs of the given airports (or every
    airport in --bbox) from the Iowa Environmental Mesonet archive, decodes
    them with MetPy, and writes temperature, dewpoint, wind and gusts,
    visibility, QNH, cloud cover and base, present weather, and
    thunderstorm / precipitation / cumulonimbus flags on a regular time axis.
    """
    if not station and bbox is None:
        raise UsageError("pass --station ICAO (repeatable) and/or --bbox N/W/S/E")
    if end_time < start_time:
        raise UsageError("--end-time is before --start-time")

    stations = pd.DataFrame({"station_id": [s.upper() for s in station], "name": None})
    if bbox is not None:
        found = _bbox_stations(bbox)
        if found.empty and not station:
            raise DataError(f"no METAR airports found inside --bbox {bbox}")
        stations = pd.concat([stations, found]).drop_duplicates("station_id")
    print(
        f"metar-fetch: {len(stations)} airport(s), {start_time}..{end_time}, from IEM",
        file=sys.stderr,
    )

    frames, skipped = [], 0
    for sid in stations["station_id"]:
        raw = _download(sid, start_time, end_time)
        if raw.empty or "metar" not in raw:
            continue
        decoded, bad = _decode(raw.dropna(subset=["metar"]))
        skipped += bad
        if not decoded.empty:
            frames.append(decoded)
    if not frames:
        raise DataError(
            f"no METARs for {', '.join(stations['station_id'])} in {start_time}..{end_time}; "
            "check the ICAO codes or widen the window"
        )
    df = pd.concat(frames, ignore_index=True)

    # One row per station and slot: the report nearest the slot time, with the
    # weather flags from every report in the slot.
    step = pd.Timedelta(interval)
    df["time"] = (df["valid"] + step / 2).dt.floor(step)
    df["dist"] = (df["valid"] - df["time"]).abs()
    flags = df.groupby(["station_id", "time"])[
        ["thunderstorm", "precipitation", "cumulonimbus"]
    ].max()
    nearest = (
        df.sort_values(["station_id", "time", "dist", "valid"])
        .drop_duplicates(["station_id", "time"])
        .set_index(["station_id", "time"])
    )
    nearest[flags.columns] = flags
    meta = (
        nearest.reset_index().groupby("station_id")[["latitude", "longitude", "elevation"]].first()
    )
    data = nearest.drop(columns=["valid", "dist", "latitude", "longitude", "elevation"])

    ds = xr.Dataset.from_dataframe(data)
    first = pd.Timestamp(start_time)
    ds = ds.reindex(time=pd.date_range(first, ds["time"].values.max(), freq=step))
    sids = ds["station_id"].values
    names = stations.set_index("station_id")["name"].reindex(sids)
    ds = ds.assign_coords(
        latitude=("station_id", meta.loc[sids, "latitude"].astype(float).values),
        longitude=("station_id", meta.loc[sids, "longitude"].astype(float).values),
        elevation=(
            "station_id",
            meta.loc[sids, "elevation"].astype(float).values,
            {
                "units": "m",
                "standard_name": "height_above_mean_sea_level",
                "long_name": "airfield elevation",
            },
        ),
        name=("station_id", names.fillna("").astype(str).values),
    )
    for name in ("present_weather", "metar"):
        ds[name] = ds[name].fillna("").astype(str)
    for name in ds.data_vars:
        if name not in ("present_weather", "metar"):
            ds[name] = ds[name].astype("float64")

    stamp_cf_dsg(
        ds, VAR_ATTRS, station_id_long_name="ICAO airport code", name_long_name="station name"
    )
    import metpy

    ds.attrs.update(
        Conventions="CF-1.13",
        featureType="timeSeries",
        title="METAR airport weather reports",
        source="METAR reports archived by the Iowa Environmental Mesonet, decoded with MetPy",
        institution="Iowa State University Iowa Environmental Mesonet (archive); national weather services (reports)",
        references="https://mesonet.agron.iastate.edu/request/download.phtml",
        history=f"{datetime.now(UTC).isoformat()} metar-fetch {start_time}..{end_time}",
        weather_skills_source="iem-metar",
        metpy_version=metpy.__version__,
    )
    verify_cf_dsg(ds)
    reported = int(ds["air_temperature"].notnull().sum())
    print(
        f"metar-fetch: {ds.sizes['station_id']} airport(s) reported; {reported} of "
        f"{ds['air_temperature'].size} {interval} slots have a report"
        + (f"; {skipped} report(s) MetPy could not decode were skipped" if skipped else ""),
        file=sys.stderr,
    )
    return stamp_data_interval(ds, period=INTERVALS[interval])


if __name__ == "__main__":
    fetch()
