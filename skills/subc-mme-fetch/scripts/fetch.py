# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "fsspec",
#   "aiohttp",
#   "cftime>=1.6",
#   "netcdf4",
#   "numpy",
#   "xarray",
# ]
# ///
"""Fetch one CHC SubC multi-model-ensemble outlook for an init date as a weather-skills Zarr."""

from __future__ import annotations

import contextlib
import json
import re
import sys
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

import numpy as np
import xarray as xr
from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset, ensure_normalized_longitude
from weather_skills_core.units import stamp_precip_amounts, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.6"

# Public GCS mirror of data.chc.ucsb.edu (same paths under chc-mirror/).
_BUCKET = "sheerwater-public-datalake"
_MIRROR_PREFIX = "chc-mirror/experimental/SubC"
_GCS_API = f"https://storage.googleapis.com/storage/v1/b/{_BUCKET}/o"
_GCS_MEDIA = f"https://storage.googleapis.com/{_BUCKET}"
SUBC_BASE = f"{_GCS_MEDIA}/{_MIRROR_PREFIX}"
# CHC origin. CHC asks download scripts to use its FTP server rather than HTTPS, and
# since mid-September 2026 it keeps each issue in its own date folder
# (archive/YYYYMMDD/mme_mean_pr_7d_YYYYMMDD.nc); the mirror keeps the older flat layout.
CHC_BASE = "ftp://ftp.chc.ucsb.edu/experimental/SubC"
DEFAULT_WORKERS = 4
_HTTP_TIMEOUT = 60
_FTP_HOST = "ftp.chc.ucsb.edu"
_FTP_TIMEOUT = 120
_FTP_BLOCK = 2**20
# Refused or dropped connections can persist for minutes; back off long.
_RETRY_DELAYS = (15, 30, 60, 120, 240)
# --probe-latest checks at most this many of the newest CHC date folders for the files.
_PROBE_CANDIDATES = 10

# Outlook id → (lead folder on server, filename lead tag, lead days).
OUTLOOKS: dict[str, tuple[str, str, int]] = {
    "7d": ("07_day", "7d", 7),
    "15d": ("15_day", "15d", 15),
    "30d": ("30_day", "30d", 30),
}
OUTLOOK_CHOICES = tuple(OUTLOOKS)

# Six published global MME map variables (mean + anomaly each).
VARIABLES: tuple[str, ...] = ("pr", "tas", "tasmax", "tasmin", "tdps", "ts")

# Human long_name per variable (the colorbar label); anomalies append " anomaly".
LONG_NAMES: dict[str, str] = {
    "pr": "Precipitation",
    "tas": "Near-surface air temperature",
    "tasmax": "Maximum near-surface air temperature",
    "tasmin": "Minimum near-surface air temperature",
    "tdps": "Dewpoint temperature",
    "ts": "Surface temperature",
}

KIND_MEAN = "mean"
KIND_ANOM = "anom"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise DataError(f"GCS listing failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"GCS listing failed for {url!r}: {exc.reason}") from None


def _object_key(folder: str, kind: str, var: str, lead_tag: str, init: date) -> str:
    name = f"mme_{kind}_{var}_{lead_tag}_{init.strftime('%Y%m%d')}.nc"
    return f"{_MIRROR_PREFIX}/{folder}/global/archive/{name}"


def _object_url(key: str) -> str:
    return f"{_GCS_MEDIA}/{urllib.parse.quote(key, safe='/')}"


def _url(folder: str, kind: str, var: str, lead_tag: str, init: date) -> str:
    return _object_url(_object_key(folder, kind, var, lead_tag, init))


def _archive_list_prefix(folder: str, lead_tag: str, var: str) -> str:
    return f"{_MIRROR_PREFIX}/{folder}/global/archive/mme_mean_{var}_{lead_tag}_"


def _chc_url(mirror_url: str) -> str | None:
    """The CHC FTP URL of a mirror URL: the same file inside its issue-date folder."""
    if not mirror_url.startswith(SUBC_BASE):
        return None
    rel = mirror_url[len(SUBC_BASE) :]
    head, _, name = rel.rpartition("/")
    stamp = re.search(r"_(\d{8})\.nc$", name)
    if stamp is None:
        return None
    return f"{CHC_BASE}{head}/{stamp.group(1)}/{name}"


def _candidate_urls(url: str) -> list[str]:
    """GCS mirror first; then the CHC FTP origin if the URL is on the mirror prefix."""
    urls = [url]
    chc = _chc_url(url)
    if chc is not None:
        urls.append(chc)
    return urls


# --- CHC FTP transport ----------------------------------------------------------------


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


def _exc_chain(exc: BaseException | None):
    """``exc`` and its causes, outermost first."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _http_status(exc: BaseException) -> int | None:
    """HTTP-style status carried anywhere on an exception chain."""
    for err in _exc_chain(exc):
        status = getattr(err, "status", None)
        if isinstance(status, int):
            return status
    return None


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
    return urllib.parse.urlparse(url).path


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


def _ftp_list(url: str) -> list[str]:
    """Entry names in one FTP directory."""
    with _ftp() as ftp:
        return [name.rsplit("/", 1)[-1] for name in ftp.nlst(_ftp_path(url))]


def _with_retry(fn, url: str):
    """Run ``fn()``, retrying 421/4xx/network failures. A missing file raises FileNotFoundError."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return fn()
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 — every FTP/NetCDF failure is classified below
            status = _http_status(exc)
            if status == 404:
                raise FileNotFoundError(url) from None
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


def _open_chc(url: str) -> xr.Dataset:
    """Download one CHC NetCDF over FTP and return it loaded in memory."""
    import tempfile
    from pathlib import Path

    def fetch_and_load():
        with tempfile.TemporaryDirectory(prefix="subc-mme-") as tmp:
            path = Path(tmp) / url.rsplit("/", 1)[-1]
            _download(url, path)
            with xr.open_dataset(path, engine="netcdf4") as ds:
                return ds.load()

    return _with_retry(fetch_and_load, url)


def _open_mirror(url: str) -> xr.Dataset:
    """Open one mirror object (fsspec ``simplecache`` gives netCDF4 a local copy)."""
    import fsspec

    path = fsspec.open_local(f"simplecache::{url}")
    return xr.open_dataset(path, engine="netcdf4")


def _open_remote(url: str) -> xr.Dataset:
    """Open a SubC NetCDF: the public GCS mirror first, then the CHC FTP server."""
    candidates = _candidate_urls(url)
    try:
        return _open_mirror(candidates[0])
    except FileNotFoundError:
        pass  # not mirrored (the mirror stopped updating on 2026-09-16): read CHC
    except Exception as exc:  # any mirror failure falls back to CHC
        if len(candidates) == 1:
            raise DataError(f"failed to open {url}: {_describe(exc)}") from exc
        print(f"SubC mirror unreadable ({_describe(exc)}); reading CHC.", file=sys.stderr)
    if len(candidates) == 1:
        raise DataError(f"SubC archive file not found: {url}")
    chc = candidates[1]
    try:
        return _open_chc(chc)
    except FileNotFoundError:
        raise DataError(
            f"SubC archive file not found on the mirror or on CHC: {chc} (CHC publishes "
            "each issue one to three days after its date; tdps is no longer published)"
        ) from None


def _normalize_field(ds: xr.Dataset, *, kind: str, var: str) -> xr.DataArray:
    """Rename Y/X → latitude/longitude and mme_mean/mme_anom → output var name."""
    rename = {}
    if "Y" in ds.dims or "Y" in ds.coords:
        rename["Y"] = "latitude"
    if "X" in ds.dims or "X" in ds.coords:
        rename["X"] = "longitude"
    if "latitude" not in rename.values() and "lat" in ds.dims:
        rename["lat"] = "latitude"
    if "longitude" not in rename.values() and "lon" in ds.dims:
        rename["lon"] = "longitude"
    ds = ds.rename(rename) if rename else ds

    src = "mme_mean" if kind == KIND_MEAN else "mme_anom"
    if src not in ds.data_vars:
        raise DataError(f"expected data variable {src!r}; got {list(ds.data_vars)}")
    out_name = var if kind == KIND_MEAN else f"{var}_anomaly"
    return ds[src].rename(out_name).load()


def _load_one(folder: str, kind: str, var: str, lead_tag: str, init: date):
    url = _url(folder, kind, var, lead_tag, init)
    with _open_remote(url) as raw:
        da = _normalize_field(raw, kind=kind, var=var)
    return kind, var, da


def _list_init_dates(folder: str, lead_tag: str, var: str) -> list[date]:
    """List archive init dates for one variable/outlook via the GCS JSON API."""
    prefix = _archive_list_prefix(folder, lead_tag, var)
    suffix = re.compile(re.escape(prefix) + r"(\d{8})\.nc$")
    found: set[date] = set()
    token = None
    while True:
        params: dict[str, str] = {"prefix": prefix, "maxResults": "1000"}
        if token:
            params["pageToken"] = token
        url = f"{_GCS_API}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url)
        for item in payload.get("items", []):
            match = suffix.match(item.get("name", ""))
            if not match:
                continue
            raw = match.group(1)
            found.add(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
        token = payload.get("nextPageToken")
        if not token:
            break
    return sorted(found)


def _chc_init_dates(folder: str) -> set[date]:
    """Issue dates on the CHC FTP server: the YYYYMMDD folders under the outlook's archive."""
    url = f"{CHC_BASE}/{folder}/global/archive/"
    found: set[date] = set()
    for name in _with_retry(lambda: _ftp_list(url), url):
        if re.fullmatch(r"\d{8}", name):
            with contextlib.suppress(ValueError):
                found.add(date(int(name[:4]), int(name[4:6]), int(name[6:8])))
    return found


def _chc_has(folder: str, lead_tag: str, variables: list[str], init: date) -> bool:
    """True when CHC's folder for ``init`` holds the mean and anomaly of every variable."""
    stamp = init.strftime("%Y%m%d")
    url = f"{CHC_BASE}/{folder}/global/archive/{stamp}/"
    try:
        names = set(_with_retry(lambda: _ftp_list(url), url))
    except FileNotFoundError:
        return False
    return all(
        f"mme_{kind}_{var}_{lead_tag}_{stamp}.nc" in names
        for var in variables
        for kind in (KIND_MEAN, KIND_ANOM)
    )


def _probe_latest(outlook: str, variables: list[str]) -> date | None:
    """Latest init present for every requested variable, on the mirror or on CHC."""
    folder, lead_tag, _days = OUTLOOKS[outlook]
    try:
        per_var = [set(_list_init_dates(folder, lead_tag, var)) for var in variables]
        mirrored = set.intersection(*per_var) if per_var else set()
    except DataError as exc:
        print(f"SubC mirror listing failed ({exc}); checking CHC only.", file=sys.stderr)
        mirrored = set()
    try:
        on_chc = _chc_init_dates(folder)
    except (DataError, FileNotFoundError) as exc:
        print(f"CHC listing failed ({exc}); using the mirror only.", file=sys.stderr)
        on_chc = set()
    newer = sorted(
        (d for d in on_chc - mirrored if not mirrored or d > max(mirrored)), reverse=True
    )
    for init in newer[:_PROBE_CANDIDATES]:
        if _chc_has(folder, lead_tag, variables, init):
            return init
    return max(mirrored) if mirrored else None


def _resolve_vars(variable) -> list[str]:
    vars_wanted = list(dict.fromkeys(variable)) if variable else list(VARIABLES)
    unknown = [v for v in vars_wanted if v not in VARIABLES]
    if unknown:
        raise UsageError(f"unknown variable(s) {unknown}; choose from: {', '.join(VARIABLES)}")
    return vars_wanted


@weather_skill(name="subc-mme-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--date",
    help="Archive init date YYYY-MM-DD (required unless --probe-latest).",
)
@weather_skill.argument(
    "--outlook",
    required=True,
    choices=list(OUTLOOK_CHOICES),
    help="Outlook length to fetch: 7d, 15d, or 30d (one forecast per call).",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Climate variable (repeatable): pr, tas, tasmax, tasmin, tdps, ts. "
        "Default: all six. For each selected variable both mean and anomaly "
        "are fetched. Prefer passing a variable to --probe-latest unless you "
        "need the latest date common to all variables."
    ),
)
@weather_skill.argument(
    "--workers",
    type=int,
    default=DEFAULT_WORKERS,
    help="Max concurrent remote opens (default 4).",
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="VAR",
    probe=True,
    help=(
        "Print the latest available init YYYY-MM-DD for this --outlook on "
        "stdout and exit. Optional VAR restricts the probe to one climate "
        "variable (recommended). With no VAR, requires a date present for "
        "all six variables. Does not download fields."
    ),
)
def fetch(date, outlook, bbox, variable, workers, probe_latest, **kwargs):
    """Fetch one CHC SubC multi-model-ensemble outlook for an init date as a weather-skills Zarr."""
    # Each call returns a single-lead forecast envelope: scalar ``time`` is the
    # init (``--date``), ``step`` is ``0 days`` (the window start), and the window
    # length is each variable's ``aggregation_period``.
    if outlook not in OUTLOOKS:
        raise UsageError(
            f"unknown --outlook {outlook!r}; choose one of: {', '.join(OUTLOOK_CHOICES)}"
        )
    folder, lead_tag, outlook_days = OUTLOOKS[outlook]

    if probe_latest is not None:
        ident = probe_latest
        if ident:
            if ident not in VARIABLES:
                raise UsageError(
                    f"unknown --probe-latest variable {ident!r}; "
                    f"choose from: {', '.join(VARIABLES)}"
                )
            vars_to_probe = [ident]
        else:
            vars_to_probe = list(VARIABLES)
        latest = _probe_latest(outlook, vars_to_probe)
        print(latest.isoformat() if latest is not None else "none")
        if latest is not None:
            # SubC publishes irregularly; tell the caller when the newest window is already over.
            today = datetime.now(UTC).date()
            valid = latest + timedelta(days=outlook_days)
            age = (today - latest).days
            if valid < today:
                print(
                    f"warning: latest {outlook} init {latest} is {age} days old; its window "
                    f"ended {valid}, before today ({today}). It is not a forecast for the coming days.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"latest {outlook} init {latest} is {age} days old; window runs to {valid}.",
                    file=sys.stderr,
                )
        return

    if date is None:
        raise UsageError("--date is required unless --probe-latest is set")
    init: date = date
    vars_wanted = _resolve_vars(variable)
    if workers < 1:
        raise UsageError("--workers must be >= 1")

    jobs = [(KIND_MEAN, var) for var in vars_wanted] + [(KIND_ANOM, var) for var in vars_wanted]

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_load_one, folder, kind, var, lead_tag, init): (kind, var)
            for kind, var in jobs
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    # Same forecast shape as the other fetchers: scalar `time` is the init and
    # `step` is the start of the one window it covers (0 days); the window length
    # is the `aggregation_period` stamped below.
    valid = init + timedelta(days=outlook_days)
    data_vars = {}
    for kind, var, da in results:
        out_name = var if kind == KIND_MEAN else f"{var}_anomaly"
        da = da.expand_dims(step=[np.timedelta64(0, "D")])
        data_vars[out_name] = da

    ds = xr.Dataset(data_vars)
    ds = ds.assign_coords(time=np.datetime64(init.isoformat(), "ns"))
    ds["time"].attrs.update(
        standard_name="forecast_reference_time",
        axis="T",
        long_name=f"SubC MME {outlook} outlook initialization",
    )
    ds["step"].attrs.update(
        standard_name="forecast_period",
        long_name="start of the outlook window (days from initialization)",
    )
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")

    ds = ds.sortby("latitude").sortby("longitude")

    if bbox is not None:
        # Pad by just under half a grid cell so every 1-degree cell that overlaps the bbox
        # is kept, not only those whose centre falls inside it (else a country's edge strip
        # is lost). bbox_subset is inclusive, so a full half cell would also keep cells that
        # only touch the box edge. The 1e-3 margin must exceed float32 coordinate resolution.
        pad = float(abs(ds["latitude"].values[1] - ds["latitude"].values[0])) / 2 - 1e-3
        north, west, south, east = bbox
        ds = bbox_subset(ds, (north + pad, west - pad, south - pad, east + pad))

    # Every field is one aggregate over the outlook window: precip a window sum (mm),
    # temperatures a window mean. Stamp the window so plot / chc-plot-theme can size palettes.
    period = f"{outlook_days} day"
    for name in ds.data_vars:
        base = name.removesuffix("_anomaly")
        long_name = LONG_NAMES[base] + (" anomaly" if name.endswith("_anomaly") else "")
        ds[name].attrs["long_name"] = long_name
        ds[name].attrs["comment"] = f"SubC MME {outlook} outlook, init {init.isoformat()}"
        ds[name].attrs["aggregation_period"] = period
        if base == "pr":
            ds[name].attrs.setdefault("units", "mm")
            ds[name].attrs["cell_methods"] = "time: sum"
            # Source NetCDFs mislabel 7-day window sums as precipitation_flux.
            ds[name].attrs.pop("standard_name", None)
        elif name.endswith("_anomaly"):
            # A temperature anomaly is a difference: delta_degC keeps to_standard_units
            # from applying the absolute K -> degC offset (-273.15).
            ds[name].attrs["units"] = "delta_degC"
            ds[name].attrs["cell_methods"] = "time: mean"
        else:
            ds[name].attrs.setdefault("units", "K")
            ds[name].attrs["cell_methods"] = "time: mean"

    ds.attrs["Conventions"] = "CF-1.13"
    ds.attrs["weather_skills_source"] = "chc-subc-mme"
    ds.attrs["outlook"] = outlook
    ds.attrs["initialization_date"] = init.isoformat()
    ds.attrs["outlook_valid_date"] = valid.isoformat()
    stamp_cf_attrs(ds)
    ds = ensure_normalized_longitude(ds, lon_dim="longitude")
    stamp_precip_amounts(ds)
    return to_standard_units(ds)


if __name__ == "__main__":
    fetch()
