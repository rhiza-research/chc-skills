# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime",
#   "dask",
#   "icechunk",
#   "numpy",
#   "pandas",
#   "xarray",
# ]
# ///
"""Fetch Planette's ECMWF IFS 46-day reforecasts around one calendar day, every year, as a model-climatology Zarr."""
# Source: Planette's public icechunk repo s3://planette-ifs-46-day/reforecasts
# (us-east-2, anonymous), groups single/1p5latx1p5lon and single/1platx1plon.
# Dims number (10 perturbed members, no control), init_time (every other day,
# 2006 onward), lead (0..45 days, daily means labelled at the interval start),
# lat (90 -> -90), lon (-180..178.5).
#
# Source -> output:
# - init_time -> `year` dim (one init per year, the nearest to --date's month
#   and day); the init dates stay as the `init_time(year)` coord.
# - lead -> `step`, lat -> `latitude`, lon -> `longitude`.
# - Variables are renamed to dynamical.org's 46-day names (_DYNAMICAL_NAMES),
#   so the forecast from dynamical-fetch and this sample share a name.
# - units: `kg m**-2 s**-1` -> `kg m-2 s-1` (same unit, udunits spelling), then
#   precip rates -> `mm day-1` and pressures -> `hPa` (to_standard_units), and
#   every `K` temperature (2 m, skin, SST, dewpoint) -> `degree_Celsius`.
#   Other units pass through.

from __future__ import annotations

import sys

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

_BUCKET = "planette-ifs-46-day"
_PREFIX = "reforecasts"
_REGION = "us-east-2"
_OCEAN = ("sit", "ssh", "sss")  # only on the 1 degree ocean grid

# Planette short name -> dynamical.org ecmwf-ifs-ens-forecast-46-day name.
_DYNAMICAL_NAMES = {
    "pr": "precipitation_surface",
    "conv_pr": "precipitation_convective_surface",
    "t2m": "average_temperature_2m",
    "t2m_max": "maximum_temperature_2m",
    "t2m_min": "minimum_temperature_2m",
    "ts": "skin_temperature_surface",
    "sst": "sea_surface_temperature",
    "u10m": "wind_u_10m",
    "v10m": "wind_v_10m",
    "slp": "pressure_reduced_to_mean_sea_level",
    "ps": "pressure_surface",
    "smv20": "soil_moisture_0_20cm",
    "smv100": "soil_moisture_0_100cm",
    "dswrf": "downward_short_wave_radiation_flux_surface",
    "sfwe": "snowfall_water_equivalent_rate_surface",
    "snw_dens": "snow_density_surface",
    "siconc": "sea_ice_area_fraction",
}
# Same unit in a spelling udunits rejects -> the conformant spelling.
_UNIT_SPELLING = {"kg m**-2 s**-1": "kg m-2 s-1", "kg m**-2": "kg m-2", "kg m**-3": "kg m-3"}


def _open(group):
    import icechunk
    import xarray as xr

    try:
        storage = icechunk.s3_storage(
            bucket=_BUCKET, prefix=_PREFIX, region=_REGION, anonymous=True
        )
        store = icechunk.Repository.open(storage).readonly_session("main").store
        return xr.open_zarr(
            store, group=group, consolidated=False, chunks={}, decode_timedelta=True
        )
    except Exception as exc:
        raise DataError(
            f"could not open the Planette reforecast store s3://{_BUCKET}/{_PREFIX} "
            f"({type(exc).__name__}: {exc}); check the network and retry"
        ) from exc


def _pick_inits(inits, date, years, max_gap):
    """{year: index} of the init nearest date's month and day in each year, within max_gap days."""
    import calendar

    import numpy as np
    import pandas as pd

    picks = {}
    for year in years:
        leap_day = date.month == 2 and date.day == 29 and not calendar.isleap(year)
        target = pd.Timestamp(year=year, month=date.month, day=28 if leap_day else date.day)
        i = int(np.argmin(np.abs((inits - target).days)))
        if abs((inits[i] - target).days) <= max_gap and i not in picks.values():
            picks[year] = i
    return picks


def _parse_years(text):
    try:
        first, _, last = text.partition("-")
        first, last = int(first), int(last or first)
    except ValueError:
        raise UsageError(f"--years must be YYYY or YYYY-YYYY; got {text!r}") from None
    if last < first:
        raise UsageError(f"--years range is reversed: {text!r}")
    return range(first, last + 1)


@weather_skill(name="planette-reforecast-fetch", version=_SKILL_VERSION)
@weather_skill.argument("--date", required=True)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help="Planette short name (pr, t2m, t2m_max, t2m_min, u10m, v10m, ws10m, sst, ...); repeat for several. Default: pr.",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--years",
    default="2006-2025",
    help="Reforecast years to take, YYYY-YYYY or YYYY (default: 2006-2025). Years without an init near --date are skipped.",
)
@weather_skill.argument(
    "--max-gap-days",
    type=int,
    default=4,
    help="Largest allowed distance in days between --date's month and day and a year's init (default: 4).",
)
@weather_skill.argument(
    "--lead-days",
    type=int,
    default=46,
    help="Keep leads 0 .. N-1 days (default: 46, every lead).",
)
@weather_skill.argument(
    "--keep-names",
    action="store_true",
    help="Keep Planette's short names instead of renaming to dynamical.org's 46-day names.",
)
def fetch(date, variable, bbox, years, max_gap_days, lead_days, keep_names, **kwargs):
    """Fetch Planette's ECMWF IFS 46-day reforecasts around one calendar day, every year, as a model-climatology Zarr."""
    import numpy as np
    import pandas as pd
    from weather_skills_core.cf import stamp_cf_attrs
    from weather_skills_core.standard_utils import bbox_subset
    from weather_skills_core.units import (
        convert_dataarray,
        stamp_data_interval,
        to_standard_units,
        units_equal,
    )

    variables = sorted(set(variable or ["pr"]))
    year_range = _parse_years(years)
    if lead_days < 1 or lead_days > 46:
        raise UsageError(f"--lead-days must be 1..46; got {lead_days}")
    if max_gap_days < 0:
        raise UsageError(f"--max-gap-days must be >= 0; got {max_gap_days}")
    ocean = [v for v in variables if v in _OCEAN]
    if ocean and len(ocean) != len(variables):
        raise UsageError(
            f"{ocean} live on the 1 degree ocean grid and the rest on the 1.5 degree grid; "
            "fetch them in separate calls"
        )
    group = "single/1platx1plon" if ocean else "single/1p5latx1p5lon"
    date = pd.Timestamp(date)

    print(f"Opening Planette reforecasts ({group})...", file=sys.stderr)
    src = _open(group)
    missing = [v for v in variables if v not in src.data_vars]
    if missing:
        raise UsageError(
            f"Planette has no {missing} in {group}; available: {sorted(src.data_vars)}"
        )
    picks = _pick_inits(src.get_index("init_time"), date, year_range, max_gap_days)
    if not picks:
        raise DataError(
            f"no reforecast init within {max_gap_days} days of {date:%m-%d} in years {years}; "
            "widen --max-gap-days or --years"
        )
    skipped = [y for y in year_range if y not in picks]
    if skipped:
        print(
            f"No init within {max_gap_days} days of {date:%m-%d} in {skipped}; skipped.",
            file=sys.stderr,
        )

    ds = src[variables].isel(init_time=list(picks.values()), lead=slice(0, lead_days))
    ds = ds.rename({"lead": "step", "lat": "latitude", "lon": "longitude"})
    if bbox is not None:
        ds = bbox_subset(ds, bbox, lat_dim="latitude", lon_dim="longitude")
    inits = ", ".join(f"{pd.Timestamp(t):%Y-%m-%d}" for t in ds.init_time.values)
    print(f"Reading {len(picks)} reforecast inits: {inits}", file=sys.stderr)
    try:
        ds = ds.load(scheduler="threads")
    except Exception as exc:
        raise DataError(
            f"reading the Planette reforecasts failed ({type(exc).__name__}: {exc}); retry"
        ) from exc

    ds = ds.assign_coords(year=("init_time", np.array(list(picks), dtype=int)))
    ds = ds.swap_dims({"init_time": "year"})
    ds = ds.transpose("year", "number", "step", "latitude", "longitude")

    for name in variables:
        attrs = {k: v for k, v in ds[name].attrs.items() if not k.startswith("GRIB_")}
        attrs.pop("packing_threshold", None)
        units = _UNIT_SPELLING.get(attrs.get("units"), attrs.get("units"))
        if not units:
            raise DataError(f"Planette {name!r} carries no units; cannot write it")
        attrs["units"] = units
        if attrs.get("standard_name") == "unknown":
            del attrs["standard_name"]
        ds[name].attrs = attrs
        if units_equal(units, "K"):
            ds[name], _ = convert_dataarray(ds[name], "degree_Celsius")
    if not keep_names:
        ds = ds.rename({v: _DYNAMICAL_NAMES[v] for v in variables if v in _DYNAMICAL_NAMES})
    for name in list(ds.data_vars):
        try:
            ds = to_standard_units(ds, variables=[name])
        except UsageError:
            continue

    ds.attrs = {
        "weather_skills_source": "planette:ifs-46-day-reforecasts",
        "Conventions": "CF-1.13",
        "reforecast_target_date": f"{date:%Y-%m-%d}",
        "reforecast_max_gap_days": max_gap_days,
    }
    stamp_cf_attrs(ds)
    ds["step"].attrs.update(standard_name="forecast_period")
    ds["init_time"].attrs.update(standard_name="forecast_reference_time")
    return stamp_data_interval(ds, period="1 day")


if __name__ == "__main__":
    fetch()
