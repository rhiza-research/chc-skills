# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "pandas",
#   "xarray",
# ]
# ///
"""Total a variable over any length of its time or lead (step) axis."""

import sys

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import (
    AGGREGATION_COVERAGE_COORD,
    AGGREGATION_PERIOD_ATTR,
    DATA_INTERVAL_ATTR,
    dequantify_dataset,
    format_duration,
    parse_aggregation_period,
    ureg,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

_NS_PER_DAY = 86_400 * 10**9


def _pick_dim(ds, time_dim):
    if time_dim:
        if time_dim not in ds.dims:
            raise UsageError(f"--time-dim {time_dim!r} not in dataset (have {list(ds.dims)})")
        return time_dim
    if "time" in ds.dims and (ds.sizes["time"] > 1 or "step" not in ds.dims):
        return "time"
    if "step" in ds.dims:
        return "step"
    raise UsageError(f"no time or step dim (have {list(ds.dims)}); pass --time-dim")


def _duration_ns(text, flag):
    """A pint duration string ('5 day', '36 hour') in integer nanoseconds."""
    try:
        q = ureg.Quantity(str(text).strip())
    except Exception:  # noqa: BLE001 — pint raises several parse errors
        q = None
    if q is not None and q.dimensionless and float(q.magnitude) == 0:
        return 0
    if q is None or q.dimensionality != ureg.Quantity(1, "day").dimensionality:
        raise UsageError(f"{flag} {text!r} is not a duration; write it with a unit, e.g. '5 day'")
    return round(float(q.to("day").magnitude) * _NS_PER_DAY)


def _edge_ns(text, is_time, flag):
    """A window edge: a date on a time axis, a duration (lead) on a step axis."""
    if not is_time:
        return _duration_ns(text, flag)
    import pandas as pd

    try:
        return pd.Timestamp(text).as_unit("ns").value
    except (ValueError, TypeError):
        raise UsageError(f"{flag} {text!r} is not a date (YYYY-MM-DD[THH:MM])") from None


def _axis_ns(values):
    """datetime64/timedelta64 values as int64 ns, plus the dtype to restore them."""
    import numpy as np

    values = np.asarray(values)
    for kind, dtype in (("M", "datetime64[ns]"), ("m", "timedelta64[ns]")):
        if values.dtype.kind == kind:
            return values.astype(dtype).astype("int64"), np.dtype(dtype)
    raise UsageError(
        f"axis values have dtype {values.dtype}; need datetime64 or timedelta64 "
        "(convert a model calendar with convert-calendar first)"
    )


def _cells(ds, dim, selected):
    """Start and end (int ns) of each sample's cell along ``dim``."""
    import numpy as np

    labels, _ = _axis_ns(ds[dim].values)
    bounds = ds[dim].attrs.get("bounds")
    bounds = bounds if isinstance(bounds, str) and bounds in ds.variables else None
    if bounds is None and f"{dim}_bounds" in ds.variables:
        bounds = f"{dim}_bounds"
    if bounds is not None:
        pairs, _ = _axis_ns(ds[bounds].transpose(dim, ...).values)
        starts, ends = pairs[:, 0], pairs[:, 1]
    else:
        interval = next(
            (
                ds[v].attrs[DATA_INTERVAL_ATTR]
                for v in selected
                if isinstance(ds[v].attrs.get(DATA_INTERVAL_ATTR), str)
            ),
            None,
        )
        diffs = np.diff(labels)
        if interval is not None:
            width = round(
                float(parse_aggregation_period(interval).to("day").magnitude) * _NS_PER_DAY
            )
        elif diffs.size and (diffs == diffs[0]).all() and diffs[0] > 0:
            width = int(diffs[0])
        elif not diffs.size:
            raise UsageError(
                f"one sample on {dim!r} and no data_interval attr: cannot tell its length"
            )
        else:
            raise UsageError(
                f"{dim!r} is unevenly spaced and has no data_interval or {dim}_bounds; "
                "run aggregate-temporal --period first"
            )
        starts, ends = labels, labels + width
    order = np.argsort(starts)
    if (starts[order][1:] < ends[order][:-1]).any():
        raise UsageError(
            f"samples on {dim!r} overlap (rolling or already accumulated); totals would "
            "count time twice. Select non-overlapping samples first"
        )
    return starts, ends


def _windows(starts, ends, is_time, start, stop, length, stride, cumulative):
    """List of (window_start, window_end, label) in int ns."""
    origin = _edge_ns(start, is_time, "--from") if start is not None else int(starts.min())
    last = int(ends.max())
    if stop is not None:
        end = _edge_ns(stop, is_time, "--to")
        if end <= origin:
            raise UsageError("--to must come after --from (or after the first sample)")
        return [(origin, end, origin)]
    if cumulative:
        edges = sorted({int(e) for e in ends if e > origin})
        if not edges:
            raise UsageError("--from is at or after the last sample")
        return [(origin, e, e) for e in edges]
    width = _duration_ns(length, "--length")
    step = _duration_ns(stride, "--stride") if stride is not None else width
    if width <= 0 or step <= 0:
        raise UsageError("--length and --stride must be positive")
    out = []
    k = 0
    while origin + k * step + width <= last:
        out.append((origin + k * step, origin + k * step + width, origin + k * step))
        k += 1
    if not out:
        raise UsageError(
            f"--length {length!r} is longer than the data after its start "
            f"({format_duration(ureg.Quantity((last - origin) / _NS_PER_DAY, 'day'))})"
        )
    return out


def _rate_time_unit(unit):
    """The per-time unit of a rate (day in mm day-1, s in kg m-2 s-1), else None."""
    time_dim = ureg.Quantity(1, "day").dimensionality
    for name, exp in unit._units.items():
        if exp == -1 and ureg.Unit(name).dimensionality == time_dim:
            return ureg.Unit(name)
    if unit.dimensionality.get("[time]", 0) < 0:
        return ureg.Unit("s")  # W m-2 → W s m-2 (an energy per area)
    return None


@weather_skill(name="accumulate", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument("--time-dim", help="Axis to total over. Default: time, else step.")
@weather_skill.argument(
    "--from",
    dest="start",
    help="Start edge of the first window: a lead ('4 day') on step, a date on time. "
    "Default: the start of the first sample.",
)
@weather_skill.argument(
    "--to",
    dest="stop",
    help="End edge (exclusive) of one window, as --from. Gives a single total.",
)
@weather_skill.argument(
    "--length", help="Window length ('7 day', '36 hour'); windows repeat to the end."
)
@weather_skill.argument(
    "--stride",
    help="With --length: distance between window starts. Default: --length "
    "(back to back); shorter gives rolling totals.",
)
@weather_skill.argument(
    "--cumulative", action="store_true", help="Running total from --from to each sample's end."
)
@weather_skill.argument(
    "--min-coverage",
    type=float,
    default=1.0,
    help="Share (0-1) of a window the samples must cover; below it the total is NaN. Default: 1.",
)
def accumulate(
    ds, variable, time_dim, start, stop, length, stride, cumulative, min_coverage, **kwargs
):
    """Total a variable over any length of its time or lead (step) axis."""
    import numpy as np
    import xarray as xr

    modes = [stop is not None, length is not None, cumulative]
    if sum(modes) != 1:
        raise UsageError("pass exactly one of --to, --length, or --cumulative")
    if stride is not None and length is None:
        raise UsageError("--stride needs --length")
    if not 0 <= min_coverage <= 1:
        raise UsageError(f"--min-coverage must be in [0, 1]; got {min_coverage}")
    dim = _pick_dim(ds, time_dim)
    if variable:
        selected = list(dict.fromkeys(variable))
        for var in selected:
            if var not in ds.data_vars:
                raise UsageError(f"--variable {var!r} not in dataset (have {list(ds.data_vars)})")
            if dim not in ds[var].dims:
                raise UsageError(f"--variable {var!r} has no {dim!r} dim")
    else:
        selected = [v for v in ds.data_vars if dim in ds[v].dims]
    if not selected:
        raise UsageError(f"no data variable has the {dim!r} dim")

    ds = dequantify_dataset(ds)
    is_time = np.asarray(ds[dim].values).dtype.kind == "M"
    starts, ends = _cells(ds, dim, selected)
    windows = _windows(starts, ends, is_time, start, stop, length, stride, cumulative)
    _, label_dtype = _axis_ns(ds[dim].values)

    members, coverage = [], []
    for ws, we, _ in windows:
        idx = np.flatnonzero((starts >= ws) & (ends <= we))
        members.append(idx)
        coverage.append(float((ends[idx] - starts[idx]).sum()) / (we - ws))
    coverage = np.asarray(coverage)
    keep = (coverage > 0) & (coverage >= min_coverage - 1e-9)
    labels = np.array([lab for _, _, lab in windows], dtype="int64").astype(label_dtype)
    if (~keep).any():
        fmt = (
            (lambda v: str(v.astype("datetime64[s]")))
            if is_time
            else (lambda v: format_duration(ureg.Quantity(v.astype("int64") / _NS_PER_DAY, "day")))
        )
        bad = ", ".join(
            f"{fmt(lab)} ({c:.0%})" for lab, c in zip(labels[~keep], coverage[~keep], strict=True)
        )
        print(
            f"{int((~keep).sum())} window(s) below --min-coverage {min_coverage:g}, "
            f"written as NaN: {bad}",
            file=sys.stderr,
        )
    if not keep.any():
        raise UsageError("no window is covered by the data; check --from/--to/--length")

    if cumulative:
        period, interval = None, None
    elif stop is not None:
        period = format_duration(
            ureg.Quantity((windows[0][1] - windows[0][0]) / _NS_PER_DAY, "day")
        )
        interval = period
    else:
        period = format_duration(parse_aggregation_period(length))
        interval = format_duration(parse_aggregation_period(stride or length))

    out_vars = {}
    for var in selected:
        da = ds[var]
        da = da.drop_vars([c for c in da.coords if c != dim and dim in da[c].dims])
        attrs = dict(da.attrs)
        units = attrs.get("units")
        unit = None
        if isinstance(units, str) and units.strip():
            try:
                unit = ureg.Unit(units)
            except Exception:  # noqa: BLE001
                raise UsageError(f"{var!r}: cannot parse units {units!r}") from None
        if unit is not None and unit.dimensionality.get("[temperature]", 0):
            raise UsageError(
                f"{var!r} is a temperature ({units}); totals of temperatures are not "
                "meaningful. Use aggregate-temporal for window means"
            )
        per = _rate_time_unit(unit) if unit is not None else None
        if per is not None:
            seconds = (ends - starts) / 1e9
            factor = ureg.Quantity(seconds, "s").to(per).magnitude
            da = da * xr.DataArray(factor, dims=dim)
            new_unit = unit * per
            attrs["units"] = format(new_unit, "cf") if format(new_unit, "cf") else "1"
            # A rate's standard_name does not fit a total; core restamps precip amounts.
            attrs.pop("standard_name", None)
        if da.chunks is not None:
            da = da.chunk({dim: -1})
        pieces = [
            da.isel({dim: idx}).sum(dim, skipna=False)
            if idx.size
            else xr.full_like(da.isel({dim: 0}, drop=True), np.nan, dtype=float)
            for idx in members
        ]
        total = xr.concat(pieces, dim=dim, coords="minimal", compat="override")
        total = total.assign_coords({dim: labels})
        total = total.where(xr.DataArray(keep, dims=dim, coords={dim: labels}))
        what = attrs.get("long_name") or var
        attrs["long_name"] = (
            f"{what} accumulated since {start or 'the first sample'}"
            if cumulative
            else f"{what} accumulated over {period}"
        )
        step = f"{dim}: sum"
        attrs["cell_methods"] = (
            f"{attrs['cell_methods']} {step}" if attrs.get("cell_methods") else step
        )
        for key in ("GRIB_name", "GRIB_units", "short_name", "comment"):
            attrs.pop(key, None)
        attrs.pop(AGGREGATION_PERIOD_ATTR, None)
        attrs.pop(DATA_INTERVAL_ATTR, None)
        if period is not None:
            attrs[AGGREGATION_PERIOD_ATTR] = period
            attrs[DATA_INTERVAL_ATTR] = interval
        total.attrs = attrs
        out_vars[var] = total

    passthrough = [v for v in ds.data_vars if dim not in ds[v].dims]
    dropped = [v for v in ds.data_vars if v not in selected and v not in passthrough]
    if dropped:
        print(f"dropped variables not accumulated along {dim!r}: {dropped}", file=sys.stderr)
    out = xr.Dataset({**out_vars, **{v: ds[v] for v in passthrough}}, attrs=ds.attrs)
    out = out.assign_coords({AGGREGATION_COVERAGE_COORD: (dim, coverage)})
    out[dim].attrs = {k: v for k, v in ds[dim].attrs.items() if k != "bounds"}
    if cumulative:
        pairs = np.stack(
            [np.full(len(windows), windows[0][0]), [we for _, we, _ in windows]], axis=1
        ).astype(label_dtype)
        out = out.assign_coords({f"{dim}_bounds": ((dim, "nv"), pairs)})
        out[dim].attrs["bounds"] = f"{dim}_bounds"
    return out


if __name__ == "__main__":
    accumulate()
