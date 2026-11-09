# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "aiohttp",
#   "certifi",
#   "cftime>=1.6",
#   "fsspec",
#   "h5netcdf",
#   "h5py",
#   "numpy",
#   "xarray",
# ]
# ///
"""Fetch one SubX model's real-time ensemble forecast from CHC SubC as a weather-skills Zarr."""

# Source-to-output transform declaration (everything else passes through):
# - Dims: CHC's (IRIDL SubX) `S`, `M`, `P`, `L`, `Y`, `X` -> scalar `time`, `number`,
#   `level`, `step`, `latitude`, `longitude`, transposed to
#   (number, step[, level], latitude, longitude). Member ids keep the source numbering
#   (ECCC 0-20 with 0 the control; CFSv2 1-4) as int64. Latitude is sorted ascending and
#   longitude mapped to [-180, 180); the source runs either way and 0..359.
# - `step`: the lead `L` (0.5, 1.5, ... days, float days or timedelta, labelled at the
#   middle of each day) becomes `L - 0.5` days, the start of its 24 h interval;
#   `data_interval = 1 day`. This is the step convention subc-climatology-fetch writes, so
#   the two subtract directly.
# - `--bbox` is applied as source index ranges before any data is read (the files are
#   tiled), with the cell-overlap padding subc-mme-fetch uses.
# - Units: passed through, then core's standard-unit pass converts recognized fields:
#   `pr` `kg m-2 s-1` (a daily-mean flux) -> `mm day-1` rate (`lwe_precipitation_rate`),
#   air temperatures `tas`/`tasmax`/`tasmin` K -> degree_Celsius, `psl` Pa -> hPa.
#   Every other variable keeps its source units and standard_name.
# - `level` (pressure-level variables such as zg, ua, va): source `P` in hPa, kept.

from __future__ import annotations

import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from datetime import date as _date

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import ensure_normalized_longitude
from weather_skills_core.units import stamp_data_interval, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

CHC_BASE = "https://data.chc.ucsb.edu/experimental/SubC/forecasts"
_USER_AGENT = "weather-skills/subc-forecast-fetch"
DEFAULT_WORKERS = 2
DEFAULT_VARIABLE = "pr"
_BLOCK_SIZE = 2 * 2**20
_HTTP_TIMEOUT = 60
# data.chc.ucsb.edu answers bursts with HTTP 429 for several minutes; back off long.
_RETRY_DELAYS = (15, 30, 60, 120, 240)
# --probe-latest looks back through this many month folders.
_PROBE_MONTHS = 6

MODELS: tuple[str, ...] = (
    "ECCC-GEPS8",
    "EMC-GEFSv12_CPC",
    "ESRL-FIMr1p1",
    "GMAO-GEOS_V2p1_5daily",
    "GMAO-GEOS_V3",
    "NCEP-CFSv2",
    "RSMAS-CCSM4",
)

# Variable directories under forecasts/ (dir_acronyms.txt). Not every model has each one;
# a missing file is reported as such.
VARIABLES: dict[str, str] = {
    "cape": "Surface-based convective available potential energy",
    "dlwrf": "Surface downward long-wave radiation flux",
    "dswrf": "Surface downward short-wave radiation flux",
    "hfls": "Surface latent heat flux",
    "hfss": "Surface sensible heat flux",
    "huss": "Specific humidity",
    "mrro": "Surface water runoff",
    "mrso": "Vertically integrated soil moisture",
    "pr": "Precipitation",
    "psl": "Mean sea level pressure",
    "rad": "Net surface radiation",
    "rlut": "Outgoing longwave radiation at top of atmosphere",
    "sic": "Sea ice concentration",
    "snc": "Surface snow cover fraction",
    "snod": "Surface snow depth",
    "snwd": "Snow density",
    "stx": "Surface zonal stress",
    "sty": "Surface meridional stress",
    "swe": "Snow water equivalent",
    "tas": "Near-surface air temperature",
    "tasmax": "Maximum near-surface air temperature",
    "tasmin": "Minimum near-surface air temperature",
    "tdps": "Dewpoint temperature",
    "ts": "Surface temperature",
    "ua": "Eastward wind",
    "uas": "10 m eastward wind",
    "ulwrf": "Surface upward long-wave radiation flux",
    "uswrf": "Surface upward short-wave radiation flux",
    "va": "Northward wind",
    "vas": "10 m northward wind",
    "wap": "Pressure vertical velocity",
    "zg": "Geopotential height",
}

_DIM_RENAME = {
    "S": "time",
    "M": "number",
    "P": "level",
    "L": "step",
    "Y": "latitude",
    "X": "longitude",
}


def _file_url(model: str, var: str, init: _date) -> str:
    stamp = init.strftime("%Y%m%d")
    return f"{CHC_BASE}/{var}/daily/{model}/{stamp[4:6]}/{var}_{model}_{stamp}.daily.nc"


# --- transport ------------------------------------------------------------------------


def _ssl_context():
    """certifi's CA bundle: data.chc.ucsb.edu's chain fails some system stores."""
    import ssl

    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def _http_status(exc: BaseException) -> int | None:
    """HTTP status carried anywhere on an exception chain (fsspec wraps aiohttp's)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        status = getattr(exc, "status", None)
        if not isinstance(status, int):
            status = getattr(exc, "code", None)  # urllib.error.HTTPError
        if isinstance(status, int):
            return status
        exc = exc.__cause__ or exc.__context__
    return None


def _is_transient(exc: BaseException) -> bool:
    import asyncio

    status = _http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    text = f"{type(exc).__name__} {exc}".lower()
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)) or any(
        marker in text for marker in ("timeout", "timed out", "connection reset", "disconnect")
    )


def _with_retry(fn, url: str, *, missing: str):
    """Run ``fn()``, retrying 429/5xx/network failures; classify the final failure."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return fn()
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 — every HTTP/HDF5 failure is classified below
            status = _http_status(exc)
            if status == 404:
                raise DataError(missing) from None
            if not _is_transient(exc) or attempt == len(_RETRY_DELAYS):
                if status == 429:
                    raise DataError(
                        "data.chc.ucsb.edu is rate-limiting requests (HTTP 429) and did not "
                        "recover after retrying; wait several minutes and rerun with "
                        "--workers 1."
                    ) from None
                kind = "unreachable" if _is_transient(exc) else "unreadable"
                raise DataError(f"CHC SubC {kind}: {url}: {exc}") from None
            delay = _RETRY_DELAYS[attempt]
            why = f"HTTP {status}" if status else type(exc).__name__
            print(f"{why} from data.chc.ucsb.edu; retrying in {delay} s ...", file=sys.stderr)
            _time.sleep(delay)
    raise AssertionError("unreachable")


def _get_text(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8", "replace")


def _list_inits(model: str, var: str, month: int) -> list[_date]:
    """Every init date (all years) in one model/variable month folder."""
    import re

    url = f"{CHC_BASE}/{var}/daily/{model}/{month:02d}/"
    html = _with_retry(
        lambda: _get_text(url),
        url,
        missing=f"no {var} forecasts for {model} on the CHC server ({url})",
    )
    pattern = re.compile(rf'href="{re.escape(var)}_{re.escape(model)}_(\d{{8}})\.daily\.nc"')
    found = {_date(int(m[:4]), int(m[4:6]), int(m[6:8])) for m in pattern.findall(html)}
    return sorted(found)


def _filesystem():
    import aiohttp
    import fsspec

    async def get_client(**kwargs):
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_ssl_context()),
            headers={"User-Agent": _USER_AGENT},
            **kwargs,
        )

    return fsspec.filesystem("https", get_client=get_client, skip_instance_cache=True)


def _open_remote(url: str):
    """Open one forecast NetCDF lazily over HTTP range requests."""
    import xarray as xr

    handle = _filesystem().open(url, block_size=_BLOCK_SIZE, cache_type="blockcache")
    return xr.open_dataset(handle, engine="h5netcdf")


# --- reshaping ------------------------------------------------------------------------


def _bbox_indexers(ds, bbox) -> dict:
    """Source Y/X integer indexers covering every cell that overlaps ``bbox``."""
    import numpy as np

    north, west, south, east = bbox
    lat = np.asarray(ds["Y"].values, dtype="float64")
    lon = (np.asarray(ds["X"].values, dtype="float64") + 180.0) % 360.0 - 180.0
    # Just under half a cell keeps cells overlapping the box but not those that only
    # touch it; the 1e-3 margin exceeds float32 coordinate resolution.
    pad = float(np.abs(np.diff(lat)).min()) / 2 - 1e-3 if lat.size > 1 else 0.0
    lat_idx = np.flatnonzero((lat >= south - pad) & (lat <= north + pad))
    lo, hi = west - pad, east + pad
    lon_mask = (lon >= lo) & (lon <= hi) if west <= east else (lon >= lo) | (lon <= hi)
    lon_idx = np.flatnonzero(lon_mask)
    if lat_idx.size == 0 or lon_idx.size == 0:
        raise UsageError(f"--bbox {north}/{west}/{south}/{east} selects no grid cells")
    return {"Y": lat_idx, "X": lon_idx}


def _lead_start_days(lead):
    """CHC `L` (float days, or timedelta when decoded) as whole start days."""
    import numpy as np

    values = np.asarray(lead.values)
    days = (
        values / np.timedelta64(1, "D")
        if np.issubdtype(values.dtype, np.timedelta64)
        else values.astype("float64")
    )
    return np.round(days - 0.5).astype("int64")


def _to_field(da, *, var: str):
    """One source variable -> (number, step[, level], latitude, longitude) DataArray."""
    import numpy as np

    da = da.rename({k: v for k, v in _DIM_RENAME.items() if k in da.dims})
    if "time" in da.dims:
        if da.sizes["time"] != 1:
            raise DataError(f"expected one init in the {var} file; got {da.sizes['time']}")
        da = da.isel(time=0)
    if "number" not in da.dims:
        da = da.expand_dims(number=[0])
    start = _lead_start_days(da["step"])
    da = da.assign_coords(
        step=start.astype("timedelta64[D]").astype("timedelta64[ns]"),
        number=np.asarray(da["number"].values).astype("int64"),
    )
    if "level" in da.dims:
        da = da.assign_coords(level=np.asarray(da["level"].values).astype("float64"))
    order = ["number", "step"] + (["level"] if "level" in da.dims else [])
    da = da.transpose(*order, "latitude", "longitude")
    keep = {k: da.attrs[k] for k in ("units", "standard_name") if k in da.attrs}
    da.attrs = keep
    return da.rename(var)


def _read_variable(model: str, var: str, init: _date, bbox):
    url = _file_url(model, var, init)

    def read():
        with _open_remote(url) as ds:
            if var not in ds.data_vars:
                raise DataError(f"{url} has no variable {var!r}; it holds {list(ds.data_vars)}")
            da = ds[var]
            if bbox is not None:
                da = da.isel(_bbox_indexers(ds, bbox))
            init_value = ds["S"].values[0] if "S" in ds.coords else None
            level_attrs = dict(ds["P"].attrs) if "P" in ds.coords else {}
            return da.load(), init_value, level_attrs

    da, init_value, level_attrs = _with_retry(
        read,
        url,
        missing=(
            f"no {model} {var} forecast for init {init.isoformat()} on the CHC server; "
            f"run --probe-latest {var} or pick another --date (inits are irregular per model)"
        ),
    )
    return _to_field(da, var=var), init_value, level_attrs


def _nearest_inits(model: str, var: str, init: _date) -> str:
    """Inits in the requested month folder around ``init``, for an error message."""
    try:
        dates = [d for d in _list_inits(model, var, init.month) if d.year == init.year]
    except DataError:
        return ""
    if not dates:
        return f" No {init:%Y-%m} inits are published for {model} {var}."
    return f" {init:%Y-%m} inits for {model} {var}: " + ", ".join(d.isoformat() for d in dates)


def _probe_latest(model: str, var: str, today: _date) -> _date | None:
    """Newest init within a year, walking back from this month's folder.

    Each month folder holds that calendar month of every year, so older years are
    filtered out by date.
    """
    month = today.month
    for _ in range(_PROBE_MONTHS):
        recent = [d for d in _list_inits(model, var, month) if 0 <= (today - d).days < 366]
        if recent:
            return max(recent)
        month = 12 if month == 1 else month - 1
    return None


def _resolve_vars(variable) -> list[str]:
    wanted = list(dict.fromkeys(variable)) if variable else [DEFAULT_VARIABLE]
    unknown = [v for v in wanted if v not in VARIABLES]
    if unknown:
        raise UsageError(f"unknown variable(s) {unknown}; choose from: {', '.join(VARIABLES)}")
    return wanted


@weather_skill(name="subc-forecast-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--date",
    help="Forecast init date YYYY-MM-DD (required unless --probe-latest).",
)
@weather_skill.argument(
    "--model",
    required=True,
    choices=list(MODELS),
    help="SubX model to fetch (one per call).",
)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Variable (repeatable), e.g. pr, tas, tasmax, tasmin, tdps, ts, psl, uas, vas, "
        "rlut, zg, ua, va (pressure levels). Default: pr."
    ),
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--workers",
    type=int,
    default=DEFAULT_WORKERS,
    help="Concurrent file reads, one per variable (default 2; the server rate-limits).",
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const=DEFAULT_VARIABLE,
    metavar="VAR",
    probe=True,
    help=(
        "Print the newest published init YYYY-MM-DD for --model (and VAR, default pr) on "
        "stdout, or 'none', and exit. Does not download fields."
    ),
)
def fetch(date, model, variable, bbox, workers, probe_latest, **kwargs):
    """Fetch one SubX model's real-time ensemble forecast from CHC SubC as a weather-skills Zarr.

    Every member at every daily lead (out to 32-45 days by model) for one init date, on
    the model's native SubX grid (1 degree; 0.5 degree for GMAO-GEOS_V3).
    """
    import numpy as np
    import xarray as xr

    if model is None:
        raise UsageError(f"--model is required; choose from: {', '.join(MODELS)}")
    if model not in MODELS:
        raise UsageError(f"unknown --model {model!r}; choose from: {', '.join(MODELS)}")

    if probe_latest is not None:
        var = probe_latest or DEFAULT_VARIABLE
        if var not in VARIABLES:
            raise UsageError(
                f"unknown --probe-latest variable {var!r}; choose from: {', '.join(VARIABLES)}"
            )
        today = datetime.now(UTC).date()
        latest = _probe_latest(model, var, today)
        print(latest.isoformat() if latest is not None else "none")
        if latest is not None:
            age = (today - latest).days
            print(f"latest {model} {var} init {latest} is {age} days old.", file=sys.stderr)
        return

    if date is None:
        raise UsageError("--date is required unless --probe-latest is set")
    if workers < 1:
        raise UsageError("--workers must be >= 1")
    init: _date = date
    vars_wanted = _resolve_vars(variable)

    def load(var):
        try:
            return _read_variable(model, var, init, bbox)
        except DataError as exc:
            if str(exc).startswith(f"no {model} {var} forecast for init"):
                raise DataError(str(exc) + "." + _nearest_inits(model, var, init)) from None
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(load, vars_wanted))

    fields = [field for field, _, _ in results]
    ds = xr.merge(fields, join="outer", compat="override", combine_attrs="drop_conflicts")
    ds = ds.sortby("latitude")
    ds = ensure_normalized_longitude(ds, lon_dim="longitude")

    init_value = next((v for _, v, _ in results if v is not None), None)
    init_time = (
        np.datetime64(init_value, "ns")
        if init_value is not None
        else np.datetime64(init.isoformat(), "ns")
    )
    if init_time.astype("datetime64[D]") != np.datetime64(init.isoformat(), "D"):
        raise DataError(
            f"file for --date {init} holds init {init_time.astype('datetime64[D]')}; "
            "the CHC archive is inconsistent for this date"
        )
    ds = ds.assign_coords(time=init_time)
    ds["time"].attrs.update(
        standard_name="forecast_reference_time", axis="T", long_name="initialization time"
    )
    ds["step"].attrs.update(
        standard_name="forecast_period", long_name="lead (start of each 24 h interval)"
    )
    ds["number"].attrs.update(standard_name="realization", long_name="ensemble member")
    if "level" in ds.dims:
        level_attrs = next((a for _, _, a in results if a), {})
        ds["level"].attrs.update(
            standard_name="air_pressure",
            units=level_attrs.get("units", "hPa"),
            long_name="pressure level",
            positive="down",
            axis="Z",
        )
    ds = ds.assign_coords(valid_time=ds["time"] + ds["step"])

    for name in ds.data_vars:
        ds[name].attrs["long_name"] = f"{model} {VARIABLES[name]}"
        ds[name].attrs["cell_methods"] = "time: mean"

    ds.attrs = {
        "Conventions": "CF-1.13",
        "title": f"SubC {model} forecast",
        "weather_skills_source": "chc-subc-forecast",
        "source": f"{CHC_BASE}/ (SubX real-time forecasts mirrored from IRIDL)",
        "model": model,
        "initialization_date": init.isoformat(),
    }
    stamp_cf_attrs(ds)
    ds = to_standard_units(ds)
    return stamp_data_interval(ds, period="1 day")


if __name__ == "__main__":
    fetch()
