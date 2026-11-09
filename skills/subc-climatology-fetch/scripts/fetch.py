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
"""Fetch one SubX model's 1999-2016 hindcast climatology from CHC SubC as a weather-skills Zarr."""

# Source-to-output transform declaration (everything else passes through):
# - Dims: CHC's `L`, `Y`, `X` -> `step`, `latitude`, `longitude`; the member dim `M` is
#   dropped (only the `*_ens_mean` / pooled-percentile fields are read, never the
#   per-member `{var}` field). Latitude is sorted ascending (CFSv2 files run north to
#   south) and longitude mapped to [-180, 180) (most models are 0..359).
# - `step` (--outlook daily): the lead `L` (0.5, 1.5, ... days, labelled at the middle of
#   each day) becomes `L - 0.5` days, labelled at the start of its 24 h interval;
#   `data_interval = 1 day`. A window outlook (`--outlook Nd`) gets one `step` = the
#   window start (`--window-start`, default 0 days) and `aggregation_period = N day`, as
#   subc-mme-fetch writes its windows.
# - Windows: 7d/15d/30d from the init read CHC's precomputed `{acc|avg}{N}` fields. Any
#   other window of the mean is computed from the daily `{var}_ens_mean`: pr summed over
#   the N lead days (rate x 1 day -> mm), temperatures averaged; a cell missing any lead
#   is NaN. This reproduces CHC's 7/15/30-day means to float32 precision. Percentiles are
#   not additive, so quantiles exist only daily and for the precomputed windows.
# - Scalar `time` is the `--date` given; only its month-day picks the source file.
# - Variable names: `{var}_ens_mean` / `{var}_{acc|avg}{N}_ens_mean` -> `{var}` (--stat
#   mean); `{var}[_{acc|avg}{N}]_p05..p95` -> `{var}` on a `quantile` dim (--stat
#   quantiles), quantile = percentile / 100 (p33/p67 -> 1/3, 2/3).
# - Units: daily `pr` `mm/day` -> `mm day-1` (same unit, udunits spelling). Window `pr`
#   is a total in `mm`. The source standard_name `precipitation_flux` on every pr field is
#   dropped; core stamps `lwe_precipitation_rate` / the amount name. Core's standard-unit pass
#   then writes air temperatures (`tas`, `tasmax`, `tasmin`) in degree_Celsius; `ts`
#   and `tdps` stay in K. No other value conversion.
# - long_name per variable; `cell_methods` `time: sum` (window pr) or `time: mean`.

from __future__ import annotations

import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset, ensure_normalized_longitude
from weather_skills_core.units import (
    stamp_data_interval,
    stamp_precip_amounts,
    to_standard_units,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

CHC_BASE = "https://data.chc.ucsb.edu/experimental/SubC/climatology"
PERIOD = "1999_2016"
_USER_AGENT = "weather-skills/subc-climatology-fetch"
DEFAULT_WORKERS = 2
# The HDF5 metadata is spread through each file; 2 MiB blocks keep the open to a few
# dozen range requests, and one daily field (one chunk) to a handful more.
_BLOCK_SIZE = 2 * 2**20
# data.chc.ucsb.edu answers bursts with HTTP 429 for several minutes; back off long.
_RETRY_DELAYS = (15, 30, 60, 120, 240)

# Variables each model's climatology is published for (server listing, 2026-11).
MODEL_VARIABLES: dict[str, tuple[str, ...]] = {
    "ECCC-GEPS8": ("pr", "tas", "tasmax", "tasmin", "tdps", "ts"),
    "EMC-GEFSv12_CPC": ("pr", "tas"),
    "ESRL-FIMr1p1": ("pr", "tas", "tasmax", "tasmin", "tdps", "ts"),
    "GMAO-GEOS_V2p1_5daily": ("pr", "tas", "tasmax", "tasmin", "tdps", "ts"),
    "GMAO-GEOS_V3": ("pr", "tas"),
    "NCEP-CFSv2": ("pr", "tas", "ts"),
    "RSMAS-CCSM4": ("pr", "tas", "tasmax", "tasmin", "ts"),
}
MODELS = tuple(MODEL_VARIABLES)
VARIABLES: tuple[str, ...] = ("pr", "tas", "tasmax", "tasmin", "tdps", "ts")

LONG_NAMES: dict[str, str] = {
    "pr": "Precipitation",
    "tas": "Near-surface air temperature",
    "tasmax": "Maximum near-surface air temperature",
    "tasmin": "Minimum near-surface air temperature",
    "tdps": "Dewpoint temperature",
    "ts": "Surface temperature",
}

# Windows from the init that CHC precomputes (mean and percentiles).
PRECOMPUTED_WINDOWS = (7, 15, 30)
STATS = ("mean", "quantiles")
# Source percentile suffix -> quantile level.
QUANTILES: dict[str, float] = {
    "p05": 0.05,
    "p20": 0.2,
    "p33": 1 / 3,
    "p67": 2 / 3,
    "p80": 0.8,
    "p95": 0.95,
}


def _file_url(model: str, var: str, day: _date) -> str:
    mmdd = day.strftime("%m%d")
    return f"{CHC_BASE}/{PERIOD}/{var}/{model}/{mmdd[:2]}/{var}_{model}_{mmdd}.daily.nc"


def _parse_outlook(outlook: str) -> int | None:
    """`daily` -> None; `Nd` (N >= 1) -> N."""
    import re

    text = str(outlook).strip().lower()
    if text == "daily":
        return None
    match = re.fullmatch(r"(\d+)\s*d(ays?)?", text)
    if not match or int(match.group(1)) < 1:
        raise UsageError(
            f"--outlook {outlook!r}: use 'daily' or a window length in days such as 7d, 10d, 14d"
        )
    return int(match.group(1))


def _plan(outlook: str, window_start: int, stat: str) -> tuple[int | None, int, bool]:
    """(window days or None, window start day, read CHC's precomputed window?)."""
    days = _parse_outlook(outlook)
    if window_start < 0:
        raise UsageError("--window-start must be >= 0 (lead days after the init)")
    if days is None:
        if window_start:
            raise UsageError(
                "--window-start needs a window --outlook (e.g. --outlook 14d); "
                "for daily leads from a later day, run select on step afterwards"
            )
        return None, 0, False
    precomputed = window_start == 0 and days in PRECOMPUTED_WINDOWS
    if stat == "quantiles" and not precomputed:
        raise UsageError(
            "--stat quantiles exists only for --outlook daily and for the 7d, 15d and 30d "
            "windows from the init (--window-start 0): percentiles of a window total "
            "cannot be built from daily percentiles. Use --stat mean for other windows."
        )
    return days, window_start, precomputed


def _source_names(var: str, days: int | None, precomputed: bool, stat: str) -> list[str]:
    """Source variable names to read, in output order (mean, or p05..p95)."""
    if precomputed:
        kind = "acc" if var == "pr" else "avg"
        prefix = f"{var}_{kind}{days}"
        mean = f"{prefix}_ens_mean"
    else:
        prefix = f"{var}_ens"
        mean = f"{var}_ens_mean"
    if stat == "mean":
        return [mean]
    return [f"{prefix}_{p}" for p in QUANTILES]


def _http_status(exc: BaseException) -> int | None:
    """HTTP status carried anywhere on an exception chain (fsspec wraps aiohttp's)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        status = getattr(exc, "status", None)
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


def _filesystem():
    """HTTPS filesystem verifying against certifi's CA bundle.

    data.chc.ucsb.edu's Let's Encrypt chain fails against some system stores (an
    expired cross-sign left in the Windows store), and passes against certifi's.
    """
    import ssl

    import aiohttp
    import certifi
    import fsspec

    async def get_client(**kwargs):
        ctx = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ctx),
            headers={"User-Agent": _USER_AGENT},
            **kwargs,
        )

    return fsspec.filesystem("https", get_client=get_client, skip_instance_cache=True)


def _open_remote(url: str):
    """Open one climatology NetCDF lazily over HTTP range requests."""
    import xarray as xr

    handle = _filesystem().open(url, block_size=_BLOCK_SIZE, cache_type="blockcache")
    return xr.open_dataset(handle, engine="h5netcdf")


def _read_fields(url: str, names: list[str]):
    """Open ``url`` and load ``names``; return them as loaded DataArrays."""
    with _open_remote(url) as ds:
        missing = [n for n in names if n not in ds.data_vars]
        if missing:
            raise DataError(f"{url} has no variable(s) {missing}; it holds {sorted(ds.data_vars)}")
        return [ds[n].load() for n in names]


def _read_with_retry(url: str, names: list[str]):
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return _read_fields(url, names)
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 — every HTTP/HDF5 failure is classified below
            status = _http_status(exc)
            if status == 404:
                raise DataError(f"climatology file not found on the CHC server: {url}") from None
            if not _is_transient(exc) or attempt == len(_RETRY_DELAYS):
                if status == 429:
                    raise DataError(
                        "data.chc.ucsb.edu is rate-limiting requests (HTTP 429) and did not "
                        "recover after retrying; wait several minutes and rerun with "
                        "--workers 1."
                    ) from None
                kind = "unreachable" if _is_transient(exc) else "unreadable"
                raise DataError(f"climatology file {kind}: {url}: {exc}") from None
            delay = _RETRY_DELAYS[attempt]
            why = f"HTTP {status}" if status else type(exc).__name__
            print(f"{why} from data.chc.ucsb.edu; retrying in {delay} s ...", file=sys.stderr)
            _time.sleep(delay)
    raise AssertionError("unreachable")


def _lead_days(lead):
    """CHC `L` (float days, or timedelta when decoded) as float days."""
    import numpy as np

    values = np.asarray(lead.values)
    if np.issubdtype(values.dtype, np.timedelta64):
        return values / np.timedelta64(1, "D")
    return values.astype("float64")


def _to_field(
    arrays, *, var: str, days: int | None, start: int, precomputed: bool, stat: str, model: str
):
    """Stack one variable's source arrays into an output DataArray."""
    import numpy as np
    import xarray as xr

    rename = {"Y": "latitude", "X": "longitude", "L": "step"}
    das = []
    for da in arrays:
        da = da.rename({k: v for k, v in rename.items() if k in da.dims})
        da = da.drop_vars([c for c in da.coords if c not in da.dims], errors="ignore")
        das.append(da)
    if stat == "quantiles":
        field = xr.concat(das, dim="quantile", coords="minimal", compat="override")
        field = field.assign_coords(quantile=list(QUANTILES.values()))
    else:
        field = das[0]

    src_attrs = dict(arrays[0].attrs)
    units = src_attrs.get("units")
    if units == "mm/day":
        units = "mm day-1"

    if precomputed:
        field = field.expand_dims(step=[np.timedelta64(0, "ns")])
    else:
        lead_start = np.round(_lead_days(arrays[0]["L"]) - 0.5).astype("int64")
        field = field.assign_coords(step=lead_start)
        if days is not None:
            wanted = np.arange(start, start + days)
            missing = np.setdiff1d(wanted, lead_start)
            if missing.size:
                raise DataError(
                    f"{model} {var} climatology has lead days {lead_start.min()}-"
                    f"{lead_start.max()}; the window {start}-{start + days - 1} needs "
                    f"{missing.size} more. Shorten --outlook or lower --window-start."
                )
            window = field.sel(step=wanted)
            if var == "pr":
                field = window.sum("step", skipna=False)  # mm day-1 x 1 day each -> mm
                units = "mm"
            else:
                field = window.mean("step", skipna=False)
            field = field.expand_dims(step=[start])
        step = field["step"].values.astype("timedelta64[D]").astype("timedelta64[ns]")
        field = field.assign_coords(step=step)

    lead_dims = ["quantile", "step"] if stat == "quantiles" else ["step"]
    field = field.transpose(*lead_dims, "latitude", "longitude")

    field.attrs = {"units": units}
    # CHC labels every pr field `precipitation_flux` (kg m-2 s-1), daily mm/day rates and
    # window totals alike; leave pr's name for core to stamp from its units.
    if src_attrs.get("standard_name") and var != "pr":
        field.attrs["standard_name"] = src_attrs["standard_name"]
    return field.rename(var).astype("float32")


def _resolve_vars(model: str, variable) -> list[str]:
    available = MODEL_VARIABLES[model]
    wanted = list(dict.fromkeys(variable)) if variable else list(available)
    unknown = [v for v in wanted if v not in VARIABLES]
    if unknown:
        raise UsageError(f"unknown variable(s) {unknown}; choose from: {', '.join(VARIABLES)}")
    missing = [v for v in wanted if v not in available]
    if missing:
        raise UsageError(
            f"{model} has no climatology for {missing}; it has: {', '.join(available)}"
        )
    return wanted


@weather_skill(name="subc-climatology-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--date",
    required=True,
    help=(
        "Forecast init date YYYY-MM-DD the climatology is for. Its month-day picks the "
        "file (02-29 has its own); the full date is written as the init `time`."
    ),
)
@weather_skill.argument(
    "--model",
    required=True,
    choices=list(MODELS),
    help="SubX model whose hindcast climatology to fetch (one per call).",
)
@weather_skill.argument(
    "--outlook",
    default="daily",
    help=(
        "daily: one field per lead day (default). Nd (any N, e.g. 7d, 10d, 14d): one "
        "window of N lead days (precip total, temperature mean); 7d/15d/30d from the init "
        "match subc-mme-fetch."
    ),
)
@weather_skill.argument(
    "--window-start",
    type=int,
    default=0,
    help=(
        "Lead day the --outlook Nd window starts on (default 0 = the init day), e.g. "
        "--outlook 14d --window-start 14 for weeks 3-4. Ensemble mean only."
    ),
)
@weather_skill.argument(
    "--stat",
    default="mean",
    choices=list(STATS),
    help=(
        "mean: ensemble mean (default). quantiles: the 5/20/33/67/80/95th percentiles "
        "pooled over all hindcast years and members, on a `quantile` dim."
    ),
)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Variable (repeatable): pr, tas, tasmax, tasmin, tdps, ts. Default: every "
        "variable the model has."
    ),
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--workers",
    type=int,
    default=DEFAULT_WORKERS,
    help="Concurrent file reads, one per variable (default 2; the server rate-limits).",
)
def fetch(date, model, outlook, window_start, stat, variable, bbox, workers, **kwargs):
    """Fetch one SubX model's 1999-2016 hindcast climatology from CHC SubC as a weather-skills Zarr.

    The climatology is lead-dependent: for the init month-day it gives the model's
    ensemble mean (or pooled percentiles) at every lead day, or over any window of lead
    days, from hindcasts initialized within 10 days of that month-day.
    """
    import numpy as np
    import xarray as xr

    if model not in MODEL_VARIABLES:
        raise UsageError(f"unknown --model {model!r}; choose from: {', '.join(MODELS)}")
    if stat not in STATS:
        raise UsageError(f"unknown --stat {stat!r}; choose from: {', '.join(STATS)}")
    if workers < 1:
        raise UsageError("--workers must be >= 1")
    window, start, precomputed = _plan(outlook, window_start, stat)
    init: _date = date
    vars_wanted = _resolve_vars(model, variable)

    def load(var):
        url = _file_url(model, var, init)
        arrays = _read_with_retry(url, _source_names(var, window, precomputed, stat))
        return _to_field(
            arrays,
            var=var,
            days=window,
            start=start,
            precomputed=precomputed,
            stat=stat,
            model=model,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fields = list(pool.map(load, vars_wanted))

    # Leads differ between variables only if CHC changes a file; outer-join with NaN.
    ds = xr.merge(fields, join="outer", compat="override", combine_attrs="drop_conflicts")
    ds = ds.sortby("latitude")
    ds = ensure_normalized_longitude(ds, lon_dim="longitude")

    if bbox is not None:
        # Pad by just under half a grid cell so every cell that overlaps the bbox is kept
        # (bbox_subset is inclusive, so a full half cell would keep cells that only touch
        # the edge). The 1e-3 margin must exceed float32 coordinate resolution.
        res = float(abs(ds["latitude"].values[1] - ds["latitude"].values[0]))
        pad = res / 2 - 1e-3
        north, west, south, east = bbox
        ds = bbox_subset(ds, (north + pad, west - pad, south - pad, east + pad))

    ds = ds.assign_coords(time=np.datetime64(init.isoformat(), "ns"))
    ds["time"].attrs.update(
        standard_name="forecast_reference_time",
        axis="T",
        long_name="initialization date the climatology is for (month-day used)",
    )
    ds["step"].attrs.update(
        standard_name="forecast_period",
        long_name=(
            "lead (start of each 24 h interval)"
            if window is None
            else "start of the outlook window (days from initialization)"
        ),
    )
    if "quantile" in ds.dims:
        ds["quantile"].attrs.update(long_name="quantile level (probability)", units="1")

    mmdd = init.strftime("%m-%d")
    for name in ds.data_vars:
        attrs = ds[name].attrs
        attrs["long_name"] = f"{model} {LONG_NAMES[name]} climatology" + (
            " quantiles" if stat == "quantiles" else " (ensemble mean)"
        )
        attrs["comment"] = (
            f"SubC {model} hindcast climatology 1999-2016 for init {mmdd} (+/-10 days)"
        )
        if window is None:
            attrs["cell_methods"] = "time: mean"
        else:
            attrs["aggregation_period"] = f"{window} day"
            attrs["cell_methods"] = "time: sum" if name == "pr" else "time: mean"

    ds.attrs = {
        "Conventions": "CF-1.13",
        "title": f"SubC {model} hindcast climatology",
        "weather_skills_source": "chc-subc-climatology",
        "source": f"https://data.chc.ucsb.edu/experimental/SubC/climatology/{PERIOD}/",
        "model": model,
        "climatology_years": "1999-2016",
        "climatology_window_days": 10,
        "calendar_day": mmdd,
        "outlook": "daily" if window is None else f"{window}d",
        "window_source": (
            "daily leads"
            if window is None
            else "CHC precomputed window"
            if precomputed
            else "computed from daily ensemble means"
        ),
        "statistic": stat,
        "initialization_date": init.isoformat(),
    }
    stamp_cf_attrs(ds)
    stamp_precip_amounts(ds)
    ds = to_standard_units(ds)
    if window is None:
        ds = stamp_data_interval(ds, period="1 day")
    return ds


if __name__ == "__main__":
    fetch()
