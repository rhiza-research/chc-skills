# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "fsspec",
#   "h5netcdf",
#   "h5py",
#   "icechunk>=2.3",
#   "numpy",
#   "xarray",
#   "zarr>=3",
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
# - Bias-corrected `pr` (CHC products/SubX/Data/Bias-corrected, CHIRPS3 grid, 0.05 degree):
#   the same SubX layout with a scalar `S`; it carries no units attr, so `mm day-1`
#   (`lwe_precipitation_rate`) is set here. Past weekly issues come from
#   products/SubX/Data/Archive/{Thursday}/Processed_forecasts: `precip(time, ens,
#   latitude, longitude)`, 31 days from the Thursday, no init; `ens` -> `number`, and
#   `step` counts from the inferred init (first step = first day - init).

from __future__ import annotations

import contextlib
import os
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from datetime import date as _date

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import ensure_normalized_longitude
from weather_skills_core.units import stamp_data_interval, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

CHC_BASE = "ftp://ftp.chc.ucsb.edu/experimental/SubC/forecasts"
DEFAULT_WORKERS = 2
DEFAULT_VARIABLE = "pr"
_FTP_HOST = "ftp.chc.ucsb.edu"
_FTP_TIMEOUT = 120
_FTP_BLOCK = 2**20
# Icechunk mirror of the forecasts (one repository per variable, one group per model,
# built from the CHC files by the same conversion this skill applies). Read first;
# the CHC FTP server is the fallback for inits and variables the mirror lacks.
# SUBC_FORECAST_MIRROR overrides it (a gs:// prefix or a local folder; "none" disables).
_MIRROR = "gs://africa-forecasting-data/chc-mirror/experimental/SubC/forecasts"
_MIRROR_ENV = "SUBC_FORECAST_MIRROR"
# Env vars holding a raw service-account key JSON (the runners' shared GCP key).
_CREDENTIAL_ENVS = ("GCLOUD_SERVICE_CREDENTIALS", "NEURAL_GCM_SERVICE_CREDENTIALS")
SOURCES = ("auto", "mirror", "chc")
# Refused or dropped connections can persist for minutes; back off long.
_RETRY_DELAYS = (15, 30, 60, 120, 240)
# CHC's bias-corrected daily precipitation (corrected to the CHIRPS3-sat climatology, on
# the CHIRPS 0.05 degree grid), one file per region and model, overwritten with each new
# run. Processed_forecasts/ holds the same values cut to 31 days from the issue Thursday.
BC_BASE = "ftp://ftp.chc.ucsb.edu/products/SubX/Data/Bias-corrected"
# Past weekly issues: Archive/{issue Thursday}/Processed_forecasts/, the 31-day processed
# copy only (no init recorded). EA only before the 2026-11-05 issue.
BC_ARCHIVE = "ftp://ftp.chc.ucsb.edu/products/SubX/Data/Archive"
# --model name -> the model name in the bias-corrected file names.
BC_MODELS = {
    "NCEP-CFSv2": "NCEP-CFSv2",
    "EMC-GEFSv12_CPC": "EMC-GEFSv12",
    "GMAO-GEOS_V3": "GMAO-GEOS_V3",
    "RSMAS-CCSM4": "RSMAS-CCSM4",
    # Only in the EA archive weeks before 2026-11-05.
    "ECCC-GEPS8": "ECCC-GEPS8",
    "ESRL-FIMr1p1": "ESRL-FIMr1p1",
}
# Models whose first daily lead is not day 0 (GEFSv12's L starts at 1.5 days).
_BC_FIRST_LEAD = {"EMC-GEFSv12_CPC": 1}
# When several raw runs fit an archived issue, the one whose bbox-mean daily series
# correlates best with the bias-corrected one wins, by at least this margin. Checked on the
# 2026-11-12 issue (init known): GEOS_V3 0.92 for its run against 0.21 for the next one,
# CFSv2 0.96 against 0.74 for the day before.
_BC_MATCH_MARGIN = 0.1
# Region code -> (north, west, south, east) cell-edge extent of its grid.
BC_REGIONS = {
    "EA": (25.0, 20.0, -13.0, 52.0),
    "WA": (20.0, -20.0, 0.0, 30.0),
    "SA": (-2.0, 10.0, -36.0, 55.0),
}
BIAS_CORRECTED = ("auto", "yes", "no")
# CHC refreshes the files once a week: each model's latest run up to a Thursday, with the
# Processed_forecasts copy starting on that Thursday (or the run's first day, if later).
_BC_ISSUE_WEEKDAY = 3  # Thursday
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

# IRIDL unit spellings some models' files carry (CFSv2 `tas` is "Kelvin_scale").
_UNIT_ALIASES = {"Kelvin_scale": "K", "Celsius_scale": "degree_Celsius"}

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
# CHC asks download scripts to use its FTP server rather than HTTPS. Each file is
# fetched whole (one RETR) into a temporary directory and opened locally: an FTP range
# read costs a fresh data connection (~3.5 s), while a whole file streams at several MB/s.


class _FTPError(Exception):
    """An FTP error reply, mapped onto the HTTP-style ``status`` the retry rules use."""

    def __init__(self, reply: str):
        super().__init__(reply)
        code = int(reply[:3]) if reply[:3].isdigit() else 0
        self.ftp_code = code
        if code == 550:
            self.status = 404  # no such file or directory
        elif code == 421:
            self.status = 429  # too many connections from this address
        elif 400 <= code < 500:
            self.status = 503  # temporary failure
        else:
            self.status = 400  # permanent failure


def _http_status(exc: BaseException) -> int | None:
    """HTTP-style status carried anywhere on an exception chain."""
    for err in _exc_chain(exc):
        status = getattr(err, "status", None)
        if isinstance(status, int):
            return status
    return None


def _exc_chain(exc: BaseException | None):
    """``exc`` and its causes, outermost first."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _describe(exc: BaseException) -> str:
    """The innermost cause as ``Type: message``."""
    root = list(_exc_chain(exc))[-1]
    return f"{type(root).__name__}: {root}" if str(root) else type(root).__name__


def _is_transient(exc: BaseException) -> bool:
    status = _http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    for err in _exc_chain(exc):
        text = f"{type(err).__name__} {err}".lower()
        if isinstance(err, (TimeoutError, ConnectionError, EOFError)) or any(
            marker in text
            for marker in ("timeout", "timed out", "connection reset", "disconnect", "refused")
        ):
            return True
    return False


def _ftp_path(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).path


@contextlib.contextmanager
def _ftp():
    """An anonymous passive-mode session on the CHC FTP server."""
    import ftplib

    try:
        ftp = ftplib.FTP(_FTP_HOST, timeout=_FTP_TIMEOUT)
        ftp.login()
        ftp.set_pasv(True)
    except ftplib.Error as exc:
        raise _FTPError(str(exc)) from exc
    try:
        yield ftp
    except ftplib.Error as exc:
        raise _FTPError(str(exc)) from exc
    finally:
        with contextlib.suppress(OSError, EOFError, ftplib.Error):
            ftp.quit()
        ftp.close()


def _download(url: str, dest) -> None:
    with _ftp() as ftp, open(dest, "wb") as fh:
        ftp.retrbinary(f"RETR {_ftp_path(url)}", fh.write, blocksize=_FTP_BLOCK)


@contextlib.contextmanager
def _open_remote(url: str):
    """Download one NetCDF over FTP to a temporary file and open it; removed on exit."""
    import tempfile
    from pathlib import Path

    import xarray as xr

    with tempfile.TemporaryDirectory(prefix="subc-") as tmp:
        path = Path(tmp) / url.rsplit("/", 1)[-1]
        _download(url, path)
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            yield ds


def _ftp_list(url: str) -> list[str]:
    """File names in one FTP directory."""
    with _ftp() as ftp:
        return [name.rsplit("/", 1)[-1] for name in ftp.nlst(_ftp_path(url))]


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
                        "ftp.chc.ucsb.edu is refusing more connections (FTP 421) and did "
                        "not recover after retrying; wait several minutes and rerun with "
                        "--workers 1."
                    ) from None
                kind = "unreachable" if _is_transient(exc) else "unreadable"
                raise DataError(f"CHC SubC {kind}: {url}: {_describe(exc)}") from None
            delay = _RETRY_DELAYS[attempt]
            print(
                f"{_describe(exc)} from ftp.chc.ucsb.edu; retrying in {delay} s ...",
                file=sys.stderr,
            )
            _time.sleep(delay)
    raise AssertionError("unreachable")


def _list_inits(model: str, var: str, month: int) -> list[_date]:
    """Every init date (all years) in one model/variable month folder."""
    import re

    url = f"{CHC_BASE}/{var}/daily/{model}/{month:02d}/"
    names = _with_retry(
        lambda: _ftp_list(url),
        url,
        missing=f"no {var} forecasts for {model} on the CHC server ({url})",
    )
    pattern = re.compile(rf"{re.escape(var)}_{re.escape(model)}_(\d{{8}})\.daily\.nc")
    found = set()
    for name in names:
        if m := pattern.fullmatch(name):
            stamp = m.group(1)
            found.add(_date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8])))
    return sorted(found)


# --- mirror ---------------------------------------------------------------------------


def _mirror_root() -> str | None:
    root = os.environ.get(_MIRROR_ENV, _MIRROR).strip().rstrip("/")
    return None if root.lower() in ("", "none", "off") else root


def _gcloud_token() -> str | None:
    """Access token of the gcloud CLI's login, or None when gcloud is unavailable."""
    import shutil
    import subprocess

    exe = shutil.which("gcloud")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


def _gcs_credentials() -> dict:
    """icechunk.gcs_storage credentials: key JSON env, credentials file, ADC, gcloud, anonymous."""
    from pathlib import Path

    for name in _CREDENTIAL_ENVS:
        key = os.environ.get(name, "").strip()
        if key:
            return {"service_account_key": key}
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path:
        return {"application_credentials": path}
    config = Path(os.environ["APPDATA"]) if os.name == "nt" else Path.home() / ".config"
    adc = config / "gcloud" / "application_default_credentials.json"
    if adc.is_file():
        return {"application_credentials": str(adc)}
    token = _gcloud_token()
    if token:
        return {"bearer_token": token}
    return {"anonymous": True}


def _open_mirror(var: str):
    """Read-only store of the mirror repository for ``var`` (raises if unreachable)."""
    import icechunk

    root = _mirror_root()
    if root.startswith("gs://"):
        bucket, _, prefix = root[len("gs://") :].partition("/")
        storage = icechunk.gcs_storage(
            bucket=bucket, prefix=f"{prefix}/{var}.icechunk".lstrip("/"), **_gcs_credentials()
        )
    else:
        storage = icechunk.local_filesystem_storage(f"{root}/{var}.icechunk")
    return icechunk.Repository.open(storage).readonly_session("main").store


def _read_mirror(model: str, var: str, init: _date, bbox):
    """(field, init value, level attrs) from the mirror, or None if it lacks this init."""
    import numpy as np
    import xarray as xr

    ds = xr.open_zarr(_open_mirror(var), group=model, consolidated=False, chunks=None)
    hit = np.flatnonzero(ds["time"].values.astype("datetime64[D]") == np.datetime64(init, "D"))
    if hit.size == 0:
        return None
    da = ds[var].isel(time=int(hit[0]))
    if bbox is not None:
        lat_idx, lon_idx = _bbox_index(da["latitude"].values, da["longitude"].values, bbox)
        da = da.isel(latitude=lat_idx, longitude=lon_idx)
    init_value = da["time"].values
    level_attrs = dict(ds["level"].attrs) if "level" in ds.coords else {}
    da = da.drop_vars([c for c in ("time", "valid_time") if c in da.coords]).load()
    da.attrs = {k: da.attrs[k] for k in ("units", "standard_name") if k in da.attrs}
    return da.rename(var), init_value, level_attrs


# --- bias-corrected forecasts ---------------------------------------------------------


def _bc_region(bbox) -> str | None:
    """The smallest bias-corrected region that contains ``bbox``, or None."""
    north, west, south, east = bbox
    inside = [
        (abs((n - s) * (e - w)), code)
        for code, (n, w, s, e) in BC_REGIONS.items()
        if s <= south and north <= n and w <= west and east <= e and west <= east
    ]
    return min(inside)[1] if inside else None


def _bc_static_reason(model: str, variables: list[str], bbox, source: str) -> str | None:
    """Why the bias-corrected forecast cannot serve this request (None when it can)."""
    if variables != ["pr"]:
        return "bias-corrected forecasts exist for pr only (request pr on its own)"
    if model not in BC_MODELS:
        return f"CHC publishes no bias-corrected {model} forecast (only {', '.join(BC_MODELS)})"
    if source == "mirror":
        return "--source mirror reads only the mirror, which holds the raw forecasts"
    if bbox is None:
        return "bias-corrected forecasts cover African regions only; pass a --bbox inside one"
    if _bc_region(bbox) is None:
        extents = "; ".join(
            f"{c} {n:g}/{w:g}/{s:g}/{e:g}" for c, (n, w, s, e) in BC_REGIONS.items()
        )
        return f"--bbox is not inside one bias-corrected region ({extents})"
    return None


def _bc_url(model: str, bbox) -> str:
    return f"{BC_BASE}/{_bc_region(bbox)}_pr_sfc_{BC_MODELS[model]}.daily.nc"


def _bc_init_from(s_var) -> _date:
    """Init date from a bias-corrected file's scalar ``S`` (raw days since 1960, or decoded)."""
    import numpy as np

    value = np.asarray(s_var.values)
    if np.issubdtype(value.dtype, np.datetime64):
        return value.astype("datetime64[D]").item()
    units = s_var.attrs.get("units", "")
    if not units.startswith("days since 1960-01-01"):
        raise DataError(f"unexpected init units {units!r} in a bias-corrected file")
    return _date(1960, 1, 1) + timedelta(days=round(float(s_var.values)))


def _ftp_header(url: str, read):
    """``read(ds)`` on a remote NetCDF opened by byte ranges (header reads stay cheap)."""
    import ftplib

    import fsspec
    import xarray as xr

    def attempt():
        try:
            fs = fsspec.filesystem("ftp", host=_FTP_HOST, timeout=_FTP_TIMEOUT)
            with (
                fs.open(_ftp_path(url), "rb", block_size=4 * 2**20, cache_type="readahead") as fh,
                xr.open_dataset(fh, engine="h5netcdf", decode_times=False) as ds,
            ):
                return read(ds)
        except ftplib.Error as exc:
            raise _FTPError(str(exc)) from exc
        except (FileNotFoundError, StopIteration) as exc:
            # fsspec's FTP stat of a file missing from a listed folder ends in one of these.
            raise _FTPError(f"550 {exc or 'no such file'}") from exc

    return _with_retry(attempt, url, missing=f"no bias-corrected file on the CHC server: {url}")


def _bc_run(ds) -> tuple[_date, _date]:
    """(init, first valid day) of the run in a bias-corrected file."""
    init = _bc_init_from(ds["S"])
    return init, init + timedelta(days=int(_lead_start_days(ds["L"])[0]))


def _bc_first_day(ds) -> _date:
    """First valid day of a Processed_forecasts file (``time`` in days since its start)."""
    units = ds["time"].attrs.get("units", "")
    if not units.startswith("days since "):
        raise DataError(f"unexpected time units {units!r} in a processed bias-corrected file")
    origin = _date.fromisoformat(units.removeprefix("days since ")[:10])
    return origin + timedelta(days=int(ds["time"].values[0]))


def _latest_issue(today: _date) -> _date:
    """The most recent Thursday on or before ``today``."""
    return today - timedelta(days=(today.weekday() - _BC_ISSUE_WEEKDAY) % 7)


def _bc_current(model: str, bbox, today: _date) -> tuple[_date, str | None]:
    """(init of CHC's bias-corrected run, why it is stale or None when current)."""
    url = _bc_url(model, bbox)
    init, first_valid = _ftp_header(url, _bc_run)
    processed = url.replace("/Bias-corrected/", "/Bias-corrected/Processed_forecasts/")
    first_day = _ftp_header(processed, _bc_first_day)
    issue = _latest_issue(today)
    expected = max(issue, first_valid)
    if not (issue - timedelta(days=6) <= init <= issue) or first_day != expected:
        return init, (
            f"CHC's bias-corrected {model} files are stale: the run starts {init} and the "
            f"processed forecast {first_day}, but the latest weekly issue (Thursday {issue}) "
            f"should start {expected}"
        )
    return init, None


def _issue_for(init: _date) -> _date:
    """The weekly issue (Thursday) whose files a run started on ``init`` would be in."""
    return init + timedelta(days=(_BC_ISSUE_WEEKDAY - init.weekday()) % 7)


def _bc_archive_url(model: str, bbox, issue: _date) -> str:
    return (
        f"{BC_ARCHIVE}/{issue.isoformat()}/Processed_forecasts/"
        f"{_bc_region(bbox)}_pr_sfc_{BC_MODELS[model]}.daily.nc"
    )


def _processed_first_day(time_var) -> _date:
    """First valid day of a processed file's ``time`` (raw days since its start, or decoded)."""
    import numpy as np

    values = np.asarray(time_var.values)
    if np.issubdtype(values.dtype, np.datetime64):
        return values[0].astype("datetime64[D]").item()
    units = time_var.attrs.get("units", "")
    if not units.startswith("days since "):
        raise DataError(f"unexpected time units {units!r} in a processed bias-corrected file")
    origin = _date.fromisoformat(units.removeprefix("days since ")[:10])
    return origin + timedelta(days=int(values[0]))


def _bc_issue_candidates(model: str, issue: _date, first_day: _date) -> list[_date]:
    """Raw runs ``issue`` may have been built from, newest first.

    The archived files record no init. CHC takes each model's latest run up to the issue
    Thursday that it has by then, and the processed copy starts on max(Thursday, the run's
    first valid day): every raw init in the week consistent with that first day. A run on
    the Thursday itself may not have reached CHC in time (GEOS_V3 2026-11-12), so more than
    one can fit; ``_bc_match`` decides from the data.
    """
    start = issue - timedelta(days=6)
    months = sorted({start.month, issue.month})
    raw = {d for month in months for d in _list_inits(model, "pr", month)}
    lead = timedelta(days=_BC_FIRST_LEAD.get(model, 0))
    return sorted(
        (d for d in raw if start <= d <= issue and max(issue, d + lead) == first_day),
        reverse=True,
    )


def _bc_archived(model: str, init: _date, bbox, today: _date):
    """``(archive URL, issue date, candidate runs)`` when an archived issue may have been
    built from ``init``, else ``(None, why not, [])``."""
    issue = _issue_for(init)
    if issue > _latest_issue(today):
        return None, f"no weekly issue covers {init} yet (the next is Thursday {issue})", []
    url = _bc_archive_url(model, bbox, issue)
    try:
        first_day = _ftp_header(url, lambda ds: _processed_first_day(ds["time"]))
    except DataError as exc:
        if str(exc).startswith("no bias-corrected file"):
            return (
                None,
                (
                    f"the bias-corrected archive has no {_bc_region(bbox)} {model} file for "
                    f"the {issue} issue"
                ),
                [],
            )
        raise
    candidates = _bc_issue_candidates(model, issue, first_day)
    if init not in candidates:
        runs = ", ".join(str(d) for d in candidates) or "no raw run in its week"
        return None, f"CHC's {issue} bias-corrected issue was built from {runs}, not {init}", []
    return url, issue.isoformat(), candidates


def _bc_match(model: str, bc, first_day: _date, candidates: list[_date], bbox) -> _date | None:
    """The candidate raw run whose bbox-mean daily series fits ``bc`` best, or None when
    no run wins by ``_BC_MATCH_MARGIN``.

    Scored per member (members are the same runs) when the member counts agree, else on
    the ensemble mean (GEOS_V3 issues with 15 lagged members).
    """
    import numpy as np

    b_days = np.datetime64(first_day.isoformat(), "D") + np.arange(bc.sizes["time"])
    b = bc.mean(("latitude", "longitude")).transpose("time", "ens").values.astype("float64")
    scores = {}
    for init in candidates:
        raw, _, _ = _read_variable(model, "pr", init, bbox)
        r = raw.mean(("latitude", "longitude")).transpose("step", "number")
        r_days = np.datetime64(init.isoformat(), "D") + (
            r["step"].values / np.timedelta64(1, "D")
        ).astype("int64")
        _, bi, ri = np.intersect1d(b_days, r_days, return_indices=True)
        bv, rv = b[bi], r.values.astype("float64")[ri]
        if bi.size < 5:
            scores[init] = float("nan")
        elif bv.shape[1] == rv.shape[1]:
            scores[init] = float(
                np.nanmean([np.corrcoef(bv[:, k], rv[:, k])[0, 1] for k in range(bv.shape[1])])
            )
        else:
            scores[init] = float(np.corrcoef(bv.mean(1), rv.mean(1))[0, 1])
    ranked = sorted((s, d) for d, s in scores.items() if np.isfinite(s))
    print(
        "Run match (correlation with the archived issue): "
        + ", ".join(f"{d} {s:.2f}" for d, s in scores.items()),
        file=sys.stderr,
    )
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[-1][0] - ranked[-2][0] < _BC_MATCH_MARGIN:
        return None
    return ranked[-1][1]


def _read_bc_archive(url: str, bbox):
    """(``precip(time, ens, latitude, longitude)`` over ``bbox``, first valid day) from an
    archived processed bias-corrected file."""

    def read():
        with _open_remote(url) as ds:
            first_day = _processed_first_day(ds["time"])
            lat_idx, lon_idx = _bbox_index(ds["latitude"].values, ds["longitude"].values, bbox)
            da = ds["precip"].isel(latitude=lat_idx, longitude=lon_idx).load()
        return da, first_day

    return _with_retry(read, url, missing=f"no bias-corrected file on the CHC server: {url}")


def _bc_archive_field(da, first_day: _date, init: _date):
    """(field, init value, level attrs) from an archived issue read for the run ``init``."""
    import numpy as np

    offset = (first_day - init).days
    da = da.rename({"ens": "number", "time": "step"})
    da = da.assign_coords(step=(np.arange(da.sizes["step"]) + offset + 0.5).astype("float64"))
    keep = ("number", "step", "latitude", "longitude")
    da = da.drop_vars([c for c in da.coords if c not in keep])
    da.attrs = {"units": "mm day-1", "standard_name": "lwe_precipitation_rate"}
    return _to_field(da.rename("pr"), var="pr"), np.datetime64(init.isoformat(), "ns"), {}


def _read_bias_corrected(model: str, init: _date, bbox):
    """(field, init value, level attrs) from CHC's bias-corrected file for ``bbox``."""
    import numpy as np

    url = _bc_url(model, bbox)

    def read():
        with _open_remote(url) as ds:
            held = _bc_init_from(ds["S"])
            if held != init:
                raise DataError(
                    f"CHC replaced the bias-corrected {model} file while it was read "
                    f"(it now holds {held}); rerun"
                )
            da = ds["pr"].isel(_bbox_indexers(ds, bbox))
            return da.drop_vars([c for c in ("S",) if c in da.coords]).load()

    da = _with_retry(read, url, missing=f"no bias-corrected file on the CHC server: {url}")
    da.attrs = {"units": "mm day-1", "standard_name": "lwe_precipitation_rate"}
    return _to_field(da, var="pr"), np.datetime64(init.isoformat(), "ns"), {}


def _choose_bias_corrected(model, variables, init, bbox, source, mode):
    """``(kind, url, issue, candidate runs)`` of the bias-corrected file to read, or None.

    ``kind`` is ``current`` (CHC's latest run, full leads) or ``archive`` (a past weekly
    issue, 31 days from its Thursday, with the raw runs it may have been built from).
    ``mode`` yes makes a miss an error.
    """
    if mode == "no":
        return None
    candidates: list[_date] = []
    reason = _bc_static_reason(model, variables, bbox, source)
    if reason is None:
        today = datetime.now(UTC).date()
        url = _bc_url(model, bbox)
        try:
            held, stale = _bc_current(model, bbox, today)
        except DataError as exc:
            held, stale = None, f"cannot read {url} ({exc})"
        if held == init and stale is None:
            return "current", url, _latest_issue(today).isoformat(), []
        try:
            archived, note, candidates = _bc_archived(model, init, bbox, today)
        except DataError as exc:
            archived, note = None, f"cannot read the bias-corrected archive ({exc})"
        if archived is not None:
            return "archive", archived, note, candidates
        current = stale or (f"the current {model} run is {held}" if held is not None else "")
        reason = f"{note}; {current}" if current else note
    _bc_miss(reason, mode)
    return None


def _bc_miss(reason: str, mode: str) -> None:
    """Report why the raw forecast is used; with ``mode`` yes that is an error."""
    if mode == "yes":
        raise DataError(f"--bias-corrected yes: {reason}")
    print(f"Raw (not bias-corrected) forecast: {reason}.", file=sys.stderr)


# --- reshaping ------------------------------------------------------------------------


def _bbox_indexers(ds, bbox) -> dict:
    """Source Y/X integer indexers covering every cell that overlaps ``bbox``."""
    lat_idx, lon_idx = _bbox_index(ds["Y"].values, ds["X"].values, bbox)
    return {"Y": lat_idx, "X": lon_idx}


def _bbox_index(lat_values, lon_values, bbox):
    """Integer indices of the latitudes/longitudes of every cell that overlaps ``bbox``."""
    import numpy as np

    north, west, south, east = bbox
    lat = np.asarray(lat_values, dtype="float64")
    lon = (np.asarray(lon_values, dtype="float64") + 180.0) % 360.0 - 180.0
    # Just under half a cell keeps cells overlapping the box but not those that only
    # touch it; the 1e-3 margin exceeds float32 coordinate resolution.
    pad = float(np.abs(np.diff(lat)).min()) / 2 - 1e-3 if lat.size > 1 else 0.0
    lat_idx = np.flatnonzero((lat >= south - pad) & (lat <= north + pad))
    lo, hi = west - pad, east + pad
    lon_mask = (lon >= lo) & (lon <= hi) if west <= east else (lon >= lo) | (lon <= hi)
    lon_idx = np.flatnonzero(lon_mask)
    if lat_idx.size == 0 or lon_idx.size == 0:
        raise UsageError(f"--bbox {north}/{west}/{south}/{east} selects no grid cells")
    return lat_idx, lon_idx


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
    if "units" in keep:
        keep["units"] = _UNIT_ALIASES.get(keep["units"], keep["units"])
    da.attrs = keep
    return da.rename(var)


def _read_variable(model: str, var: str, init: _date, bbox, source: str = "auto"):
    if source != "chc" and _mirror_root() is not None:
        where = f"{_mirror_root()}/{var}.icechunk"
        try:
            got = _read_mirror(model, var, init, bbox)
        except UsageError:
            raise
        except Exception as exc:  # noqa: BLE001 — any mirror failure falls back to CHC
            # Icechunk errors carry a multi-line context trace; the first line is the reason.
            reason = _describe(exc).splitlines()[0]
            if type(exc).__name__ == "RepositoryNotFoundError":
                reason = f"{var} is not mirrored"
            elif isinstance(exc, KeyError | FileNotFoundError) and "group" in str(exc).lower():
                reason = f"{model} is not mirrored for {var}"
            if source == "mirror":
                raise DataError(f"cannot read the SubC mirror {where}: {reason}") from None
            print(f"SubC mirror {where}: {reason}; reading the CHC FTP server.", file=sys.stderr)
        else:
            if got is not None:
                print(f"{model} {var} {init}: read from the mirror {where}", file=sys.stderr)
                return got
            if source == "mirror":
                raise DataError(f"{model} {var} init {init} is not on the SubC mirror {where}")
            print(
                f"{model} {var} {init} is not on the mirror yet; reading the CHC FTP server.",
                file=sys.stderr,
            )
    elif source == "mirror":
        raise UsageError(f"--source mirror, but {_MIRROR_ENV} disables the mirror")
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
    "--source",
    choices=list(SOURCES),
    default="auto",
    help=(
        "Where to read: auto (the Icechunk mirror, then the CHC FTP server for inits or "
        "variables it lacks), mirror (mirror only), chc (CHC FTP only). Default: auto."
    ),
)
@weather_skill.argument(
    "--bias-corrected",
    choices=list(BIAS_CORRECTED),
    default="auto",
    help=(
        "Use CHC's bias-corrected daily precipitation (CHIRPS3 climatology, 0.05 degree grid) "
        "instead of the raw model output: auto (when it covers the request: pr only; "
        "NCEP-CFSv2, EMC-GEFSv12_CPC, GMAO-GEOS_V3 or RSMAS-CCSM4, plus ECCC-GEPS8 and "
        "ESRL-FIMr1p1 in early archive weeks; a --bbox inside the EA, WA or SA region; and "
        "--date the run CHC used for its current weekly issue, full leads, or for an "
        "archived issue, 31 days from that Thursday), yes (error "
        "when it does not), no (raw only). Default: auto. With yes, --probe-latest prints "
        "the init of the current bias-corrected run for the --bbox region, or none when "
        "CHC's files are stale (their forecast must start on the latest Thursday)."
    ),
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
def fetch(date, model, variable, bbox, workers, source, bias_corrected, probe_latest, **kwargs):
    """Fetch one SubX model's real-time ensemble forecast from CHC SubC as a weather-skills Zarr.

    Every member at every daily lead (out to 32-45 days by model) for one init date, on
    the model's native SubX grid (1 degree; 0.5 degree for GMAO-GEOS_V3). For precipitation
    over Africa, CHC's bias-corrected forecast (CHIRPS3 climatology, 0.05 degree; the
    current weekly run or an archived issue) is used instead when it covers the request
    (see --bias-corrected).
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
        if bias_corrected == "yes":
            reason = _bc_static_reason(model, [var], bbox, source)
            if reason is not None:
                raise UsageError(f"--bias-corrected yes: {reason}")
            latest, stale = _bc_current(model, bbox, today)
            print("none" if stale else latest.isoformat())
            print(
                f"{stale}."
                if stale
                else (
                    f"bias-corrected {model} run ({_bc_region(bbox)}) started {latest}, "
                    f"{(today - latest).days} days ago; CHC keeps only this run."
                ),
                file=sys.stderr,
            )
            return
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
            return _read_variable(model, var, init, bbox, source)
        except DataError as exc:
            if str(exc).startswith(f"no {model} {var} forecast for init"):
                raise DataError(str(exc) + "." + _nearest_inits(model, var, init)) from None
            raise

    plan = _choose_bias_corrected(model, vars_wanted, init, bbox, source, bias_corrected)
    results = None
    if plan is not None and plan[0] == "archive":
        _, url, issue, candidates = plan
        print(f"{model} pr {init}: archived bias-corrected {issue} issue {url}", file=sys.stderr)
        bc, first_day = _read_bc_archive(url, bbox)
        used = init if len(candidates) == 1 else _bc_match(model, bc, first_day, candidates, bbox)
        if used == init:
            print(
                f"Archived issue: 31 days from {first_day} only; CHC records no init, so the "
                f"run ({init}) is inferred.",
                file=sys.stderr,
            )
            results = [_bc_archive_field(bc, first_day, init)]
        else:
            _bc_miss(
                f"CHC's {issue} bias-corrected issue was built from the {used} run, not {init}"
                if used is not None
                else f"cannot tell which of the runs {', '.join(map(str, candidates))} CHC's "
                f"{issue} bias-corrected issue was built from",
                bias_corrected,
            )
            plan = None
    elif plan is not None:
        print(
            f"{model} pr {init}: bias-corrected forecast ({plan[2]} issue) from {plan[1]}",
            file=sys.stderr,
        )
        results = [_read_bias_corrected(model, init, bbox)]
    bc_url = plan[1] if plan is not None else None
    if results is None:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(load, vars_wanted))

    fields = [field for field, _, _ in results]
    ds = xr.merge(fields, join="outer", compat="override", combine_attrs="drop_conflicts")
    ds = ds.sortby("latitude")
    ds = ensure_normalized_longitude(ds, lon_dim="longitude")
    # The same grid whichever source served it: SubX grids sit on a 0.5/1 degree lattice
    # (GMAO-GEOS_V3's source equator is -2.9e-13), and IRIDL's coordinate attrs
    # (`degree_north`, `pointwidth`, `gridtype`) are replaced by plain CF ones.
    ds = ds.assign_coords(
        latitude=np.round(ds["latitude"].values.astype("float64"), 4).astype("float32"),
        longitude=np.round(ds["longitude"].values.astype("float64"), 4).astype("float32"),
    )
    ds["latitude"].attrs = {
        "standard_name": "latitude",
        "long_name": "latitude",
        "units": "degrees_north",
        "axis": "Y",
    }
    ds["longitude"].attrs = {
        "standard_name": "longitude",
        "long_name": "longitude",
        "units": "degrees_east",
        "axis": "X",
    }

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
        "bias_corrected": "yes" if bc_url is not None else "no",
    }
    if bc_url is not None:
        ds.attrs["title"] = f"SubC {model} bias-corrected forecast"
        ds.attrs["source"] = f"{bc_url} (CHC bias-corrected SubX precipitation)"
        ds.attrs["bias_correction"] = (
            "CHC bias correction to the CHIRPS3-sat climatology (source attr obs_climatology), "
            "on the CHIRPS 0.05 degree grid"
        )
        ds.attrs["bias_corrected_issue"] = plan[2]
        if plan[0] == "archive":
            ds.attrs["bias_corrected_init"] = (
                "inferred: CHC's archived issue records no init; it is the raw run in the "
                "7 days up to the issue Thursday that fits the issue's first day (when "
                "several do, the one whose daily series matches best)"
            )
        ds["pr"].attrs["long_name"] = f"{model} bias-corrected precipitation"
    stamp_cf_attrs(ds)
    ds = to_standard_units(ds)
    return stamp_data_interval(ds, period="1 day")


if __name__ == "__main__":
    fetch()
