# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "gcsfs",
#   "google-auth",
#   "numpy",
#   "pandas",
#   "xarray",
#   "zarr>=3",
# ]
# ///
"""Fetch ECMWF 0.4 degree extended-range reforecasts (hindcasts) from the Sheerwater datalake."""

# Source-to-output transform declaration (everything else passes through):
# - Dims: the store's (init, year, number, step, latitude, longitude) -> (init_time, number,
#   step, latitude, longitude). Every selected (init, year) pair is one reforecast run, and
#   `init_time` is the date that run started: `year` with the init's month and day.
#   Non-dim coords `hindcast_year (init_time)` and `reforecast_init (init_time)` keep the
#   store's labels; `valid_time (init_time, step) = init_time + step`.
# - Values: tp accumulated since init (kg m**-2 = mm) -> the daily amount of the 24 h
#   starting at init + N days (difference of consecutive daily accumulations, negative
#   rounding noise clipped to 0), i.e. a rate in `mm day-1`, `data_interval = 1 day`.
#   Step 0's zero field is dropped, so `step = N days` labels the start of its interval,
#   as ecmwf-04deg-fetch does for the forecast.
# - Coordinates: lat/lon rounded to 4 decimals; latitude stays descending.
# - standard_name `lwe_precipitation_rate` and long_name `Total precipitation` on `tp`.

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date as _date

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset
from weather_skills_core.units import stamp_data_interval, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

_BUCKET = "sheerwater-datalake"
_PREFIX = "ecmwf-0_4"
# One store per model-cycle year: the hindcast years shift with the year the
# real-time forecasts they belong to are made in.
_STORE_RE = re.compile(r"ECMWF_ext_range_hindcast_tp_04deg_(\d{4})\.zarr$")
_VARIABLES = {"tp": "tp", "pr": "tp", "precip": "tp"}
_MAX_LEADS = 46
# A store init further than this from the date's day of the year belongs to another
# part of the season (inits come every 2 days), the same limit downscale_04deg_.py uses.
_MAX_GAP_DAYS = 4

# Env vars holding a raw service-account key JSON, checked in this order:
# GCLOUD_SERVICE_CREDENTIALS is the name runners store the shared GCP key under;
# NEURAL_GCM_SERVICE_CREDENTIALS is the one pbc-fetch and neuralgcm-fetch read.
_CREDENTIAL_ENVS = ("GCLOUD_SERVICE_CREDENTIALS", "NEURAL_GCM_SERVICE_CREDENTIALS")

_AUTH_MSG = (
    f"cannot read gs://{_BUCKET}/{_PREFIX} (private GCS). Set "
    "GCLOUD_SERVICE_CREDENTIALS (or NEURAL_GCM_SERVICE_CREDENTIALS) to the full "
    "service-account key JSON (no file needed), GOOGLE_APPLICATION_CREDENTIALS to a "
    "service-account JSON file path, or "
    "run `gcloud auth application-default login` (a plain `gcloud auth login` also "
    "works when the gcloud CLI is on PATH)."
)

_FS = {}


def _gcloud_token():
    """Access token of the gcloud CLI's login, or None when gcloud is unavailable."""
    exe = shutil.which("gcloud")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe, "auth", "print-access-token"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _token():
    """gcsfs token: the service-account key JSON in GCLOUD_SERVICE_CREDENTIALS or
    NEURAL_GCM_SERVICE_CREDENTIALS, then None for GOOGLE_APPLICATION_CREDENTIALS /
    Application Default Credentials, then the gcloud CLI's login.
    """
    for name in _CREDENTIAL_ENVS:
        creds_json = os.environ.get(name, "").strip()
        if not creds_json:
            continue
        try:
            return json.loads(creds_json)
        except json.JSONDecodeError as exc:
            raise DataError(
                f"{name} is not valid JSON (line {exc.lineno}, column {exc.colno}); it must "
                "hold the full service-account key JSON."
            ) from None
    import google.auth
    import google.auth.exceptions

    try:
        google.auth.default()
        return None
    except google.auth.exceptions.DefaultCredentialsError:
        access = _gcloud_token()
        if access is None:
            raise DataError(_AUTH_MSG) from None
        import google.oauth2.credentials

        return google.oauth2.credentials.Credentials(access)


def _gcs(asynchronous=False):
    """Authenticated GCS filesystem (an asynchronous one for zarr, which takes it as is)."""
    if asynchronous not in _FS:
        import gcsfs

        token = _token() if "token" not in _FS else _FS["token"]
        _FS["token"] = token
        try:
            kwargs = {"asynchronous": asynchronous}
            if token is not None:
                kwargs["token"] = token
            _FS[asynchronous] = gcsfs.GCSFileSystem(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface auth failures cleanly
            raise DataError(f"{_AUTH_MSG} ({type(exc).__name__})") from None
    return _FS[asynchronous]


def _gcs_error(action: str, exc: Exception) -> DataError:
    """Classify a GCS failure: auth (401/403) or transport."""
    text = str(exc)
    if any(code in text for code in ("401", "403", "Permission", "Anonymous caller")):
        return DataError(f"{action}: access denied. {_AUTH_MSG}")
    return DataError(f"{action} failed ({type(exc).__name__}: {text[:200]}); retry later.")


def _ls(prefix: str) -> list[str]:
    """Names directly under a GCS prefix (empty when it doesn't exist)."""
    try:
        return [str(p).rstrip("/").rsplit("/", 1)[-1] for p in _gcs().ls(prefix, detail=False)]
    except FileNotFoundError:
        return []
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error(f"listing gs://{prefix}", exc) from None


def _list_stores() -> dict:
    """{model-cycle year: store path} of the reforecast stores in the bucket."""
    stores = {}
    for name in _ls(f"{_BUCKET}/{_PREFIX}"):
        match = _STORE_RE.search(name)
        if match:
            stores[int(match.group(1))] = f"{_BUCKET}/{_PREFIX}/{name}"
    if not stores:
        raise DataError(
            f"no reforecast stores (ECMWF_ext_range_hindcast_tp_04deg_<YYYY>.zarr) in "
            f"gs://{_BUCKET}/{_PREFIX}/; the bucket may be unreachable."
        )
    return stores


def _pick_store(year: int | None):
    """(model-cycle year, path): the store for `year` if there is one, else the newest."""
    stores = _list_stores()
    cycle = year if year in stores else max(stores)
    if year is not None and cycle != year:
        print(
            f"No reforecast store for {year}; using the {cycle} one "
            f"(stores: {', '.join(map(str, sorted(stores)))})",
            file=sys.stderr,
        )
    return cycle, stores[cycle]


def _open_store(path: str):
    """The reforecast store, opened lazily; selected chunks are fetched concurrently."""
    import xarray as xr

    try:
        from zarr.storage import FsspecStore

        store = FsspecStore(_gcs(asynchronous=True), path=path, read_only=True)
        return xr.open_zarr(store, consolidated=True, chunks=None)
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error(f"opening gs://{path}", exc) from None


def _filled_years(path: str, init_index: int) -> set[int]:
    """Indices along `year` whose chunk exists for this init. The store is laid out for
    every init first and filled one at a time, so an unfilled init has no chunks."""
    return {int(n) for n in _ls(f"{path}/tp/c/{init_index}") if n.isdigit()}


def _gap_in_year(a, b) -> int:
    """Days between two dates' month and day, whatever their years (wraps the new year)."""
    gap = abs((_date(2000, a.month, a.day) - _date(2000, b.month, b.day)).days)
    return min(gap, 366 - gap)


def _select_inits(inits, target, window_days: int) -> list[int]:
    """Indices of the store inits for `target`: the closest in the year, plus every other
    init within `window_days` of `target`. Exit 2 when the closest is too far away."""
    gaps = [_gap_in_year(init, target) for init in inits]
    closest = min(range(len(inits)), key=gaps.__getitem__)
    if gaps[closest] > _MAX_GAP_DAYS:
        raise UsageError(
            f"the closest reforecast init ({inits[closest]}) is {gaps[closest]} days from "
            f"{target:%m-%d}, more than {_MAX_GAP_DAYS}"
        )
    chosen = {closest} | {i for i, g in enumerate(gaps) if g <= window_days}
    return sorted(chosen, key=lambda i: (gaps[i], i))


def _parse_years(value: str | None, available) -> list[int]:
    """--years as hindcast years present in the store: `2010-2020` or `2005,2010`."""
    available = [int(y) for y in available]
    if value is None:
        return available
    years = set()
    try:
        for part in value.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = (int(p) for p in part.split("-", 1))
                years |= set(range(lo, hi + 1))
            elif part:
                years.add(int(part))
    except ValueError:
        raise UsageError(f"--years {value!r} is not a year list (2010-2020 or 2005,2010)") from None
    picked = [y for y in available if y in years]
    if not picked:
        raise UsageError(
            f"--years {value!r} matches none of the hindcast years {available[0]}-{available[-1]}"
        )
    return picked


def _parse_lead_days(value: str) -> int:
    """--max-lead as whole days (pandas timedelta: 46D, 14D, 336h)."""
    import pandas as pd

    try:
        days = pd.Timedelta(value) / pd.Timedelta(days=1)
    except ValueError:
        raise UsageError(f"--max-lead {value!r} is not a timedelta (46D, 14D, 336h)") from None
    if days <= 0 or days != int(days):
        raise UsageError(f"--max-lead must be a positive whole number of days, got {value!r}")
    return int(days)


def _run_date(year: int, init):
    """Start date of the reforecast run of hindcast `year` for store init `init` (29 Feb in
    a non-leap year becomes 28 Feb)."""
    import numpy as np

    day = init.day if not (init.month == 2 and init.day == 29 and year % 4) else 28
    return np.datetime64(_date(year, init.month, day).isoformat(), "ns")


def _to_reforecast(raw, store_inits, init_indices, year_values, n_leads):
    """The store's accumulated tp of every (init, year) run selected as daily rates on
    (init_time, number, step, latitude, longitude), loaded in one concurrent read."""
    import numpy as np
    import xarray as xr

    n_steps = raw.sizes["step"] - 1
    if n_leads > n_steps:
        print(f"The reforecasts have {n_steps} lead days (requested {n_leads}).", file=sys.stderr)
        n_leads = n_steps
    sub = raw["tp"].isel(init=init_indices, step=slice(0, n_leads + 1)).sel(year=year_values)
    n_runs = len(init_indices) * len(year_values)
    print(
        f"Reading {n_runs} reforecast runs ({len(init_indices)} init(s) x {len(year_values)} "
        f"hindcast years) x {sub.sizes['number']} members x {n_leads} lead days on "
        f"{sub.sizes['latitude']}x{sub.sizes['longitude']} cells (the store's chunks hold the "
        f"whole domain: ~9 MB per run whatever the bbox, ~{9 * n_runs} MB)",
        file=sys.stderr,
    )
    try:
        accumulated = sub.load()
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error("reading the reforecasts", exc) from None
    accumulated = accumulated.stack(run=("init", "year")).transpose("run", ...)
    if bool(accumulated.isnull().all(["number", "step", "latitude", "longitude"]).any()):
        raise DataError("a selected reforecast run is empty in the store; retry later.")

    daily = accumulated.diff("step").clip(min=0).values.astype("float32")
    inits = [store_inits[i] for i in init_indices for _ in year_values]
    years = [int(y) for _ in init_indices for y in year_values]
    starts = np.array([_run_date(y, i) for y, i in zip(years, inits, strict=True)])
    order = np.argsort(starts, kind="stable")
    steps = (np.arange(n_leads) * np.timedelta64(1, "D")).astype("timedelta64[ns]")
    tp = xr.DataArray(
        daily[order],
        dims=("init_time", "number", "step", "latitude", "longitude"),
        coords={
            "init_time": starts[order],
            "number": accumulated["number"].values.astype("int64"),
            "step": steps,
            "latitude": np.round(accumulated["latitude"].values.astype("float64"), 4),
            "longitude": np.round(accumulated["longitude"].values.astype("float64"), 4),
        },
        attrs={"units": "mm day-1", "long_name": "Total precipitation"},
    )
    ds = xr.Dataset({"tp": tp}).sortby("latitude", ascending=False)
    return ds.assign_coords(
        hindcast_year=("init_time", np.asarray(years, dtype="int64")[order]),
        reforecast_init=(
            "init_time",
            np.array([np.datetime64(i.isoformat(), "ns") for i in inits])[order],
        ),
        valid_time=(("init_time", "step"), starts[order][:, None] + steps[None, :]),
    )


def _store_inits(raw):
    import pandas as pd

    return [ts.date() for ts in pd.to_datetime(raw["init"].values)]


@weather_skill(
    name="ecmwf-04deg-reforecast-fetch",
    version=_SKILL_VERSION,
)
@weather_skill.argument(
    "--date",
    help=(
        "Forecast date YYYY-MM-DD the reforecasts are for: the store init closest to its day "
        f"of the year (at most {_MAX_GAP_DAYS} days away) is used. Required unless probing."
    ),
)
@weather_skill.argument(
    "--window-days",
    type=int,
    default=0,
    help=(
        "Also pool every store init within this many days of --date's day of the year "
        "(inits come every 2 days; 4 gives about 5 inits, as ECMWF pools its m-climate). "
        "Default 0: the closest init only."
    ),
)
@weather_skill.argument(
    "--years",
    help="Hindcast years to keep: a range (2010-2020) or list (2005,2010). Default: all.",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help="Field to retrieve. Only tp (daily precipitation; `pr` is accepted). Default tp.",
)
@weather_skill.argument(
    "--max-lead",
    default=f"{_MAX_LEADS}D",
    help=(
        "Keep the lead days whose 24 h interval ends by this lead (pandas timedelta: 14D, "
        f"336h). Default {_MAX_LEADS}D, the whole reforecast."
    ),
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help=(
        "Print the newest filled reforecast init date of the newest store (YYYY-MM-DD) on "
        "stdout and exit. IDENT is ignored. Does not download fields."
    ),
)
def fetch(date, window_days, years, bbox, variable, max_lead, probe_latest, **kwargs):
    """Fetch ECMWF 0.4 degree extended-range reforecasts (20 hindcast years x 11 members, 46
    lead days, East Africa) from the private store in gs://sheerwater-datalake/ecmwf-0_4/ as
    an ensemble forecast Zarr of daily rates, one init_time per reforecast run."""
    if probe_latest is not None:
        _, path = _pick_store(None)
        inits = _store_inits(_open_store(path))
        for index in range(len(inits) - 1, -1, -1):
            if _filled_years(path, index):
                print(inits[index].isoformat())
                return
        raise DataError(f"gs://{path} holds no filled reforecast inits yet.")

    if date is None:
        raise UsageError("--date is required (the forecast date the reforecasts are for)")
    unknown = [v for v in (variable or []) if v not in _VARIABLES]
    if unknown:
        raise UsageError(
            f"unknown variable(s): {', '.join(unknown)}. Available: tp (alias pr, precip)."
        )
    if window_days < 0:
        raise UsageError(f"--window-days must be 0 or more, got {window_days}")
    n_leads = _parse_lead_days(max_lead)
    target = date.date() if hasattr(date, "date") else date

    cycle, path = _pick_store(target.year)
    raw = _open_store(path)
    store_inits = _store_inits(raw)
    year_values = _parse_years(years, raw["year"].values)
    year_index = {int(y): i for i, y in enumerate(raw["year"].values)}

    chosen, skipped = [], []
    for init_index in _select_inits(store_inits, target, window_days):
        filled = _filled_years(path, init_index)
        if all(year_index[y] in filled for y in year_values):
            chosen.append(init_index)
        else:
            skipped.append(store_inits[init_index].isoformat())
    if skipped:
        print(f"Not filled in the store yet, skipped: {', '.join(skipped)}", file=sys.stderr)
    if not chosen:
        raise DataError(
            f"no filled reforecast init near {target:%m-%d} in gs://{path} "
            f"(unfilled: {', '.join(skipped)}); retry later."
        )
    picked = [store_inits[i].isoformat() for i in chosen]
    print(f"Reforecast store {cycle}: init(s) {', '.join(picked)}", file=sys.stderr)

    if bbox is not None:
        raw = bbox_subset(raw, bbox, lat_dim="latitude", lon_dim="longitude")
    ds = _to_reforecast(raw, store_inits, chosen, year_values, n_leads)

    ds.attrs.update(
        Conventions="CF-1.13",
        title="ECMWF 0.4-degree extended-range reforecasts (daily precipitation)",
        source="ECMWF IFS extended-range hindcasts (stream eefh), 0.4 degree",
        reforecast_model_cycle=str(cycle),
        reforecast_inits=",".join(picked),
        weather_skills_source=(
            f"sheerwater-ecmwf-0_4-hindcast:{cycle}:{','.join(i.replace('-', '') for i in picked)}"
        ),
        license="Licensed ECMWF data; private. Do not publish the raw fields.",
    )
    stamp_cf_attrs(ds)
    ds = to_standard_units(ds)
    return stamp_data_interval(ds, period="1 day")


if __name__ == "__main__":
    fetch()
