# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@combine-dim-ontology-cleanup",
#   "cftime",
#   "fsspec",
#   "aiohttp",
#   "xarray",
#   "zarr",
#   "numpy",
#   "netcdf4",
#   "pint-xarray>=0.6",
# ]
# ///
"""Fetch CHC SubC multi-model-ensemble global archive for one init and write a forecast Zarr."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import xarray as xr
from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset
from weather_skills_core.units import to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

SUBC_BASE = "https://data.chc.ucsb.edu/experimental/SubC"
DEFAULT_WORKERS = 4

# Lead folder on the server → lead tag in filenames → lead days.
LEADS: tuple[tuple[str, str, int], ...] = (
    ("07_day", "7d", 7),
    ("15_day", "15d", 15),
    ("30_day", "30d", 30),
)

# Six published global MME map variables (mean + anomaly each).
VARIABLES: tuple[str, ...] = ("pr", "tas", "tasmax", "tasmin", "tdps", "ts")

KIND_MEAN = "mean"
KIND_ANOM = "anom"


def _url(folder: str, kind: str, var: str, lead_tag: str, init: date) -> str:
    name = f"mme_{kind}_{var}_{lead_tag}_{init.strftime('%Y%m%d')}.nc"
    return f"{SUBC_BASE}/{folder}/global/archive/{name}"


def _open_remote(url: str) -> xr.Dataset:
    """Open a remote SubC NetCDF with xarray.

    CHC serves plain HTTPS files (not OPeNDAP). ``netCDF4``'s URL opener treats
    ``https://`` as DAP and fails, so fsspec materializes a local path and we
    open that with xarray — same end result as ``xr.open_dataset(url)`` when
    the server supports it.
    """
    import fsspec

    try:
        path = fsspec.open_local(f"simplecache::{url}")
    except FileNotFoundError as exc:
        raise DataError(f"SubC archive file not found: {url}") from exc
    except Exception as exc:
        raise DataError(f"failed to open {url}: {exc}") from exc
    try:
        return xr.open_dataset(path, engine="netcdf4")
    except Exception as exc:
        raise DataError(f"failed to open {url}: {exc}") from exc


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
        window_days = int(raw.attrs.get("window_days", lead_tag.rstrip("d")))
        operation = str(raw.attrs.get("operation", ""))
    return (lead_tag, kind, var, da, window_days, operation)


@weather_skill(name="subc-mme-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--date", required=True)
@weather_skill.argument("--bbox")
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument(
    "--workers",
    type=int,
    default=DEFAULT_WORKERS,
    help="Max concurrent remote opens (default 4).",
)
def fetch(date, bbox, variable, workers, **kwargs):
    """Fetch SubC MME global archive for one init and write a forecast Zarr."""
    init: date = date
    vars_wanted = list(dict.fromkeys(variable)) if variable else list(VARIABLES)
    unknown = [v for v in vars_wanted if v not in VARIABLES]
    if unknown:
        raise UsageError(f"unknown variable(s) {unknown}; choose from: {', '.join(VARIABLES)}")
    if workers < 1:
        raise UsageError("--workers must be >= 1")

    jobs = [
        (folder, kind, var, lead_tag)
        for folder, lead_tag, _days in LEADS
        for var in vars_wanted
        for kind in (KIND_MEAN, KIND_ANOM)
    ]

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_load_one, folder, kind, var, lead_tag, init): (folder, kind, var, lead_tag)
            for folder, kind, var, lead_tag in jobs
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    # Group by variable then concat leads on step.
    by_var: dict[str, list[tuple[int, xr.DataArray]]] = {}
    for lead_tag, kind, var, da, _window_days, _operation in results:
        out_name = var if kind == KIND_MEAN else f"{var}_anomaly"
        # step = forecast lead (days since init). For SubC MME the field at
        # each lead is a mean/sum over the window from init through that lead,
        # so lead days coincide with window_days in the source attrs.
        step_days = next(d for _f, tag, d in LEADS if tag == lead_tag)
        da = da.expand_dims(step=[np.timedelta64(step_days, "D")])
        by_var.setdefault(out_name, []).append((step_days, da))

    data_vars = {}
    for out_name, pieces in by_var.items():
        pieces_sorted = [da for _d, da in sorted(pieces, key=lambda t: t[0])]
        merged = xr.concat(pieces_sorted, dim="step")
        data_vars[out_name] = merged

    ds = xr.Dataset(data_vars)
    ds = ds.assign_coords(time=np.datetime64(init.isoformat(), "ns"))
    ds["time"].attrs.update(standard_name="forecast_reference_time", axis="T")
    ds["step"].attrs.update(
        standard_name="forecast_period",
        long_name="time since forecast_reference_time",
    )
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")

    ds = ds.sortby("latitude").sortby("longitude")

    if bbox is not None:
        ds = bbox_subset(ds, bbox)

    for name in ds.data_vars:
        if name == "pr" or name.startswith("pr_"):
            ds[name].attrs.setdefault("units", "mm")
            ds[name].attrs.setdefault("long_name", f"SubC MME {name}")
        else:
            ds[name].attrs.setdefault("units", "K")
            ds[name].attrs.setdefault("long_name", f"SubC MME {name}")

    ds.attrs["Conventions"] = "CF-1.13"
    ds.attrs["weather_skills_source"] = "chc-subc-mme"
    stamp_cf_attrs(ds)
    return to_standard_units(ds)


if __name__ == "__main__":
    fetch()
