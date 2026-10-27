# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime",
#   "numpy",
#   "pandas",
#   "xarray",
# ]
# ///
"""Lay daily observations onto a forecast's lead days, once per year, as an observed climatology sample."""

from __future__ import annotations

import sys

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"


def _parse_years(text):
    try:
        first, _, last = text.partition("-")
        first, last = int(first), int(last or first)
    except ValueError:
        raise UsageError(f"--years must be YYYY or YYYY-YYYY; got {text!r}") from None
    if last < first:
        raise UsageError(f"--years range is reversed: {text!r}")
    return range(first, last + 1)


def _init_in_year(init, year):
    """init's month, day and time of day in another year; Feb 29 becomes Feb 28 outside leap years."""
    import calendar

    import pandas as pd

    day = init.day
    if init.month == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    return pd.Timestamp(year=year, month=init.month, day=day, hour=init.hour, minute=init.minute)


@weather_skill(name="lead-climatology", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("spatial"),
    action="append",
    required=True,
    help="Daily observation Zarr with a time axis; repeat for several (e.g. one per year).",
)
@weather_skill.argument(
    "--like",
    required=True,
    help="Forecast Zarr whose init time and step values the sample follows.",
)
@weather_skill.argument(
    "--years",
    required=True,
    help="Years to lay out, YYYY-YYYY or YYYY. A year with any missing day is dropped.",
)
@weather_skill.argument("--variable", "-v", action="append")
def lead_climatology(ds, like, years, variable, **kwargs):
    """Lay daily observations onto a forecast's lead days, once per year, as an observed climatology sample."""
    import numpy as np
    import pandas as pd
    import xarray as xr

    fc = xr.open_zarr(like, consolidated=None)
    if "step" not in fc.dims:
        raise UsageError(f"--like has no step dim (has {list(fc.dims)}); pass a forecast Zarr")
    if "time" not in fc.coords or fc["time"].size != 1:
        raise UsageError("--like needs one scalar init time coord `time`")
    init = pd.Timestamp(fc["time"].values.ravel()[0])
    steps = fc["step"].values

    datasets = [ds] if isinstance(ds, xr.Dataset) else list(ds)
    for n, item in enumerate(datasets):
        if "time" not in item.dims:
            raise UsageError(f"input {n + 1} has no time dim (has {list(item.dims)})")
    obs = xr.concat(
        [ds.drop_vars([c for c in ds.coords if c != "time" and "time" in ds[c].dims])
         for ds in datasets],
        dim="time",
        data_vars="minimal",
        coords="minimal",
        compat="override",
        join="override",
    )  # fmt: skip
    obs = obs.sortby("time")
    obs = obs.isel(time=~obs.get_index("time").duplicated())
    if variable:
        missing = [v for v in variable if v not in obs.data_vars]
        if missing:
            raise UsageError(f"inputs have no {missing} (have {list(obs.data_vars)})")
        obs = obs[sorted(set(variable))]

    times = obs.get_index("time")
    spacing = np.diff(times.values)
    if spacing.size and (spacing < np.timedelta64(1, "D")).any():
        raise UsageError(
            "inputs are sub-daily; run aggregate-temporal --period daily on each input first"
        )
    off_step = [s for s in steps if s % np.timedelta64(1, "D")]
    if off_step:
        raise UsageError(
            f"--like has sub-daily steps ({pd.Timedelta(off_step[0])}); run aggregate-temporal "
            "--period daily --time-dim step on the forecast first"
        )

    per_year, dropped, inits = [], {}, []
    for year in _parse_years(years):
        start = _init_in_year(init, year)
        valid = pd.DatetimeIndex(start + pd.to_timedelta(steps))
        present = valid.isin(times)
        if not present.all():
            dropped[year] = int((~present).sum())
            continue
        sample = obs.sel(time=valid)
        if any(
            bool(sample[v].isnull().all(set(sample[v].dims) - {"time"}).any())
            for v in sample.data_vars
        ):
            dropped[year] = "an all-NaN day"
            continue
        sample = sample.rename(time="step").assign_coords(step=steps)
        per_year.append(sample.expand_dims(year=[year]))
        inits.append(start)
    if dropped:
        detail = ", ".join(
            f"{y} ({n} missing days)" if isinstance(n, int) else f"{y} ({n})"
            for y, n in dropped.items()
        )
        print(f"Dropped years without every lead day: {detail}", file=sys.stderr)
    if len(per_year) < 2:
        raise DataError(
            f"only {len(per_year)} year(s) have every lead day from {init:%m-%d}; fetch the "
            "observations for each year's window (init + steps) and pass them with -i"
        )

    out = xr.concat(per_year, dim="year")
    out = out.assign_coords(init_time=("year", pd.DatetimeIndex(inits).values))
    out["step"].attrs.update(standard_name="forecast_period")
    out["init_time"].attrs.update(standard_name="forecast_reference_time")
    dims = ["year", "step"] + [d for d in out.dims if d not in ("year", "step")]
    out = out.transpose(*dims)
    out.attrs["lead_climatology_like_init"] = f"{init:%Y-%m-%d}"
    print(
        f"Laid {len(per_year)} years ({per_year[0].year.item()}-{per_year[-1].year.item()}) "
        f"onto {len(steps)} lead days from {init:%m-%d}.",
        file=sys.stderr,
    )
    return out


if __name__ == "__main__":
    lead_climatology()
