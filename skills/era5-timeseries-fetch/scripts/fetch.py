# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "aiohttp",
#   "cftime",
#   "dask",
#   "fsspec",
#   "numpy",
#   "pandas",
#   "requests",
#   "truststore",
#   "xarray",
# ]
# ///
"""Fetch ERA5 hourly single-level fields for any set of time windows over a small area from ECMWF's geo-chunked ARCO store."""
# Source: ECMWF Data Store ARCO, ERA5 hourly single levels, geo-chunked Zarr
# (chunks of 67,584 hours x 4 x 4 cells, 0.25 degree, 1940 onward). Reading a
# small area over many years touches few chunks; a large area over a short
# period touches many (use arco-era5-fetch for that).
#
# Source -> output:
# - Variables keep their ERA5 short names (t2m, tp, d2m, u10, ...).
# - Fields accumulated or maximised over the previous hour (GRIB stepType
#   accum / max: tp, ssrd, strd, fdir, slhf, sshf, fg10) are labelled at the
#   END of their hour in the source; they are read one hour later and
#   relabelled to the START of the hour, so a day's 00..23 rows cover that day.
# - units: `J m**-2` -> `J m-2`, `m s**-1` -> `m s-1`, `(0 - 1)` -> `1` (same
#   units, udunits spelling). Then t2m/skt/d2m/sst `K` -> `degree_Celsius`,
#   msl/sp `Pa` -> `hPa`, and tp (m per hour) -> `mm day-1` rate.
#   Other fields keep their units.
# - standard_name "unknown" is dropped; curated CF standard names are set for
#   t2m, d2m, u10, v10, skt, sst.

from __future__ import annotations

import os
import sys

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

_STORE = (
    "https://arco.datastores.ecmwf.int/cadl-arco-geo-002/arco/"
    "reanalysis_era5_single_levels/sfc/geoChunked.zarr"
)
_UNIT_SPELLING = {"J m**-2": "J m-2", "m s**-1": "m s-1", "(0 - 1)": "1"}
_STANDARD_NAMES = {
    "t2m": "air_temperature",
    "d2m": "dew_point_temperature",
    "skt": "surface_temperature",
    "sst": "sea_surface_temperature",
    "u10": "eastward_wind",
    "v10": "northward_wind",
}
_CELSIUS = ("t2m", "d2m", "skt", "sst")


def _api_key():
    """The Data Store API key: ECMWF_DATASTORES_KEY, CDSAPI_KEY, then ~/.cdsapirc."""
    for name in ("ECMWF_DATASTORES_KEY", "CDSAPI_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    rc = os.path.expanduser("~/.cdsapirc")
    if os.path.isfile(rc):
        with open(rc, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("key:"):
                    return line.split(":", 1)[1].strip()
    raise UsageError(
        "no ECMWF Data Store API key: set ECMWF_DATASTORES_KEY (or CDSAPI_KEY, or a "
        "`key:` line in ~/.cdsapirc). Get it from your profile at "
        "https://cds.climate.copernicus.eu/profile and accept the ERA5 licence there."
    )


def _open(state):
    """Open the geo-chunked store lazily (metadata only), at most once per run."""
    if "ds" not in state:
        headers = {"Authorization": f"Bearer {_api_key()}"}
        # The store's Let's Encrypt chain is newer than some certifi bundles;
        # verify against the operating system's trust store instead.
        import truststore

        truststore.inject_into_ssl()
        import xarray as xr

        try:
            state["ds"] = xr.open_zarr(
                _STORE, consolidated=True, chunks={}, storage_options={"headers": headers}
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            text = str(exc)
            if "401" in text or "403" in text:
                raise DataError(
                    "the ECMWF Data Store refused the API key (HTTP 401/403): check the "
                    "key and accept the ERA5 single levels licence on the CDS website"
                ) from None
            raise DataError(
                f"could not open ECMWF's ARCO ERA5 store ({type(exc).__name__}: "
                f"{text[:200]}); check the network and retry"
            ) from None
    return state["ds"]


def _parse_years(text):
    """'2006-2024', '1997,2015,2023' or a mix -> sorted unique years."""
    years = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition("-")
        try:
            first, last = int(first), int(last or first)
        except ValueError:
            raise UsageError(
                f"--years takes years and ranges like 2006-2024,1997; got {text!r}"
            ) from None
        if last < first:
            raise UsageError(f"--years range {part!r} is reversed")
        years.update(range(first, last + 1))
    if not years:
        raise UsageError("--years is empty")
    return sorted(years)


def _parse_hours(text):
    try:
        hours = sorted({int(h) for h in text.split(",") if h.strip()})
    except ValueError:
        raise UsageError(f"--hours takes UTC hours like 0,6,12,18; got {text!r}") from None
    if not hours or hours[0] < 0 or hours[-1] > 23:
        raise UsageError(f"--hours must be 0..23; got {text!r}")
    return hours


def _parse_window(text):
    import pandas as pd

    start, sep, end = text.partition("/")
    try:
        if not sep:
            raise ValueError
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    except ValueError:
        raise UsageError(f"--window takes YYYY-MM-DD/YYYY-MM-DD; got {text!r}") from None
    if end < start:
        raise UsageError(f"--window {text!r} ends before it starts")
    return start, end


def _shift_to_year(start, end, year):
    """The window moved to start in ``year``, keeping its length; Feb 29 -> Feb 28."""
    import calendar

    import pandas as pd

    day = start.day
    if start.month == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    new_start = pd.Timestamp(year=year, month=start.month, day=day)
    return new_start, new_start + (end - start)


def _hourly_times(windows, hours):
    """Every hour (or every listed UTC hour) of every day in the windows, sorted, unique."""
    import pandas as pd

    parts = [pd.date_range(s, e + pd.Timedelta(hours=23), freq="h") for s, e in windows]
    times = pd.DatetimeIndex(sorted(set().union(*parts)))
    if hours is not None:
        times = times[times.hour.isin(hours)]
    return times


@weather_skill(name="era5-timeseries-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--start-time")
@weather_skill.argument("--end-time")
@weather_skill.argument(
    "--window",
    action="append",
    help="A date window START/END (YYYY-MM-DD/YYYY-MM-DD, inclusive); repeat for several.",
)
@weather_skill.argument(
    "--years",
    help=(
        "Repeat every window in each of these years, keeping its month, day and length "
        "(e.g. 2006-2024, or 1997,2015,2023 for analog years)."
    ),
)
@weather_skill.argument(
    "--hours", help="Keep only these UTC hours, e.g. 0,6,12,18. Default: all 24."
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--point", help="LAT,LON: the nearest 0.25 degree cell instead of a --bbox."
)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help="Print the last day in the store (YYYY-MM-DD) on stdout and exit. IDENT is ignored.",
)
def fetch(
    start_time, end_time, window, years, hours, bbox, point, variable, probe_latest, **kwargs
):
    """Fetch ERA5 hourly single-level fields for any set of time windows over a small area from ECMWF's geo-chunked ARCO store."""
    state = {}
    if probe_latest is not None:
        ds = _open(state)
        print(str(ds["time"].values[-1])[:10])
        return

    import numpy as np
    import pandas as pd
    from weather_skills_core.cf import stamp_cf_attrs
    from weather_skills_core.standard_utils import bbox_subset, is_transient
    from weather_skills_core.units import (
        convert_dataarray,
        precip_amounts_to_rates,
        stamp_data_interval,
        to_standard_units,
    )

    # --- what to read --------------------------------------------------------
    if not variable:
        raise UsageError(
            "pass -v (e.g. -v t2m -v tp); available: blh, cbh, d2m, fdir, fg10, msl, skt, "
            "slhf, sp, sshf, ssrd, sst, strd, t2m, tcc, tp, u10, u100, v10, v100"
        )
    if bbox and point:
        raise UsageError("pass --bbox or --point, not both")
    if not bbox and not point:
        raise UsageError(
            "pass --bbox N/W/S/E or --point LAT,LON; for global fields use arco-era5-fetch"
        )
    if (start_time is None) != (end_time is None):
        raise UsageError("pass --start-time and --end-time together")
    windows = [_parse_window(w) for w in (window or [])]
    if start_time is not None:
        start, end = pd.Timestamp(start_time), pd.Timestamp(end_time)
        if end < start:
            raise UsageError("--end-time is before --start-time")
        windows.append((start, end))
    if not windows:
        raise UsageError("pass --start-time/--end-time or at least one --window")
    if years:
        windows = [_shift_to_year(s, e, y) for y in _parse_years(years) for s, e in windows]
    hour_list = _parse_hours(hours) if hours else None
    times = _hourly_times(windows, hour_list)

    ds = _open(state)
    variables = sorted(set(variable))
    missing = [v for v in variables if v not in ds.data_vars]
    if missing:
        raise UsageError(
            f"not in ERA5 single levels: {missing}; available: {', '.join(sorted(ds.data_vars))}"
        )
    ds = ds[variables]

    # --- where ---------------------------------------------------------------
    if point:
        try:
            lat, lon = (float(x) for x in point.split(","))
        except ValueError:
            raise UsageError(f"--point takes LAT,LON; got {point!r}") from None
        ilat = int(np.abs(ds["latitude"].values - lat).argmin())
        ilon = int(np.abs(ds["longitude"].values - lon).argmin())
        ds = ds.isel(latitude=[ilat], longitude=[ilon])
    else:
        ds = bbox_subset(ds, bbox, lat_dim="latitude", lon_dim="longitude")

    # --- when ----------------------------------------------------------------
    # Hour-end fields are read one hour later and relabelled to the hour start.
    store_times = ds.get_index("time")
    end_labelled = [v for v in variables if ds[v].attrs.get("GRIB_stepType") in ("accum", "max")]
    pieces = []
    kept = None
    for group, offset in ((
        [v for v in variables if v not in end_labelled], pd.Timedelta(0)),
        (end_labelled, pd.Timedelta(hours=1)),
    ):  # fmt: skip
        if not group:
            continue
        idx = store_times.get_indexer(times + offset)
        ok = idx >= 0
        kept = ok if kept is None else kept & ok
        pieces.append((group, idx, offset))
    if not kept.any():
        raise DataError(
            f"the store holds {str(store_times[0])[:10]}..{str(store_times[-1])[:10]}; none "
            "of the requested hours are in it"
        )
    if not kept.all():
        lost = times[~kept]
        print(
            f"Warning: {len(lost)} requested hours are outside the store "
            f"({str(store_times[0])[:10]}..{str(store_times[-1])[:10]}), e.g. "
            f"{lost[0]:%Y-%m-%d %H}h; dropped.",
            file=sys.stderr,
        )
    out_times = times[kept]
    parts = []
    for group, idx, _ in pieces:
        part = ds[group].isel(time=idx[kept])
        parts.append(part.assign_coords(time=out_times))
    import xarray as xr

    out = xr.merge(parts, compat="override", join="exact")[variables]
    n_windows = len(windows)
    print(
        f"Reading {len(out_times)} hours in {n_windows} window(s) over "
        f"{out.sizes['latitude']}x{out.sizes['longitude']} cells: {', '.join(variables)}",
        file=sys.stderr,
    )
    for attempt in range(3):
        try:
            out = out.load(scheduler="threads")
            break
        except MemoryError:
            raise DataError("ran out of memory; narrow --bbox, the windows, or -v") from None
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "401" in text or "403" in text:
                raise DataError(
                    "the ECMWF Data Store refused the API key while reading (HTTP 401/403); "
                    "accept the ERA5 single levels licence on the CDS website"
                ) from None
            if "429" in text:
                raise DataError(
                    "the ECMWF Data Store is rate limiting this key (HTTP 429); wait and "
                    "retry, or read fewer windows per call"
                ) from None
            if attempt < 2 and is_transient(exc):
                print(f"Transient read error ({type(exc).__name__}); retrying.", file=sys.stderr)
                continue
            raise DataError(
                f"reading ECMWF's ARCO ERA5 store failed ({type(exc).__name__}: {text[:200]})"
            ) from None

    # --- metadata and units --------------------------------------------------
    for name in variables:
        src = out[name].attrs
        units = _UNIT_SPELLING.get(src.get("units"), src.get("units"))
        if not units or units == "~":
            raise DataError(f"ERA5 {name!r} carries no usable units ({src.get('units')!r})")
        attrs = {"units": units, "long_name": src.get("long_name") or name}
        standard_name = _STANDARD_NAMES.get(name) or src.get("standard_name")
        if standard_name and standard_name != "unknown":
            attrs["standard_name"] = standard_name
        out[name].attrs = attrs
        if name in _CELSIUS:
            out[name], _ = convert_dataarray(out[name], "degree_Celsius")
    out.attrs = {
        "Conventions": "CF-1.13",
        "title": "ERA5 hourly single levels (ECMWF ARCO, geo-chunked)",
        "institution": "European Centre for Medium-Range Weather Forecasts",
        "source": _STORE,
        "weather_skills_source": "ecmwf-arco-era5-geo",
        "era5_windows": "; ".join(f"{s:%Y-%m-%d}/{e:%Y-%m-%d}" for s, e in windows),
    }
    out = to_standard_units(out)
    out = precip_amounts_to_rates(out, interval="1 hour")
    stamp_cf_attrs(out)
    return stamp_data_interval(out, period="1 hour")


if __name__ == "__main__":
    fetch()
