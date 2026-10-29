# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "numpy",
#   "xarray",
# ]
# ///
"""Max consecutive-run length along a time-like dim satisfying a threshold comparison.

For each selected data variable, computes the longest run of consecutive
entries along the time dim (``time``, or a lead-time dim such as ``step``)
satisfying ``value <comparison> threshold`` — e.g. a dry-spell length with
``--comparison lt`` on precipitation, or a wet-spell / heatwave length with
``--comparison ge``. Data variables that don't carry the time dim pass
through untouched.
"""

import operator
import sys

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import (
    ALIASES,
    detect_time_dim,
)
from weather_skills_core.units import units_equal

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

_COMPARISONS = {
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
}

_COMPARISON_SYMBOLS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤"}


def _max_consecutive_run_nd(block, threshold, comp):
    """Max consecutive-run length along the last axis, vectorized over every
    leading (batch) dim at once.

    block : ndarray, shape (..., n_time) — time dim must be the last axis
    threshold : float, compared against block via `comp`
    comp : one of operator.gt/ge/lt/le

    Loops only over n_time (small, e.g. tens of steps) instead of over every
    gridpoint/member combination; each loop iteration is a single numpy op
    over the whole batch. Any missing value anywhere along the time axis for
    a given batch element marks that element's result NaN (a run computed
    across a data gap can't be trusted to be the true max).
    """
    import numpy as np

    condition = comp(block, threshold)
    has_nan = np.isnan(block).any(axis=-1)

    counter = np.zeros(block.shape[:-1], dtype=float)
    max_run = np.zeros(block.shape[:-1], dtype=float)
    for t in range(block.shape[-1]):
        counter = (counter + 1) * condition[..., t]
        np.maximum(max_run, counter, out=max_run)

    max_run[has_nan] = np.nan
    return max_run


def _resolve_time_dim(ds, override):
    """Explicit --time-dim wins; else the ontology's time dim; else a
    lead-time dim (e.g. `step`, aliased to `prediction_timedelta`)."""
    if override:
        if override not in ds.dims:
            raise UsageError(f"--time-dim '{override}' not in dims {list(ds.dims)}")
        return override
    try:
        return detect_time_dim(ds)
    except UsageError:
        pass
    lead = next((d for d in ds.dims if ALIASES.get(d) == "prediction_timedelta"), None)
    if lead is not None:
        print(
            f"Note: no time dim found; computing spell length over lead-time "
            f"dim '{lead}' instead. Pass --time-dim to override.",
            file=sys.stderr,
        )
        return lead
    raise UsageError(
        f"no time/lead-time dim identified in {list(ds.dims)}. Pass --time-dim to override."
    )


@weather_skill(
    name="spell-length",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help="Restrict the computation to this data variable. Repeatable. "
    "Each selected variable must carry the time dim. Default (unset): "
    "every data variable carrying the time dim.",
)
@weather_skill.argument(
    "--threshold",
    type=float,
    required=True,
    help="Value to compare each time-step against, in the variable's own units.",
)
@weather_skill.argument(
    "--comparison",
    required=True,
    choices=["gt", "ge", "lt", "le"],
    help="Comparison applied as value <op> threshold, e.g. 'lt' for a dry-spell "
    "(below-threshold) run, 'ge' for a wet-spell (at-or-above-threshold) run.",
)
@weather_skill.argument(
    "--time-dim",
    default=None,
    help="Name of the time-like dim when not auto-detectable.",
)
def spell_length(ds, variable, threshold, comparison, time_dim, **kwargs):
    """Max consecutive-run length along a time-like dim satisfying a threshold comparison."""
    import numpy as np
    import xarray as xr

    dim = _resolve_time_dim(ds, time_dim)

    # Variable selection, mirroring `summarize-dim`: explicit --variable names
    # must be data variables and must each carry the time dim. Default
    # selection takes every data variable carrying it; the rest pass through
    # untouched.
    if variable is not None:
        data_vars = list(ds.data_vars)
        invalid = [v for v in variable if v not in ds.data_vars]
        if invalid:
            raise UsageError(
                f"--variable {invalid} not data variable(s) of the input. "
                f"Valid data variables: {data_vars}"
            )
        selected = list(dict.fromkeys(variable))
        missing = [v for v in selected if dim not in ds[v].dims]
        if missing:
            raise UsageError(f"variable(s) {missing} do not carry time dim '{dim}'.")
    else:
        selected = [v for v in ds.data_vars if dim in ds[v].dims]
        if not selected:
            raise UsageError(f"no data variable carries time dim '{dim}'.")

    passthrough = [v for v in ds.data_vars if v not in selected]
    if passthrough:
        print(
            f"Note: passing through unreduced data variable(s) {passthrough}.",
            file=sys.stderr,
        )

    print(
        f"Computing spell length dim={dim} comparison={comparison} "
        f"threshold={threshold} variables={selected}",
        file=sys.stderr,
    )

    comp = _COMPARISONS[comparison]
    out_ds = ds.copy()
    for var in selected:
        da = ds[var]
        # The decorator opens data-variable inputs as pint quantities; a bare
        # float threshold can't compare against one inside apply_ufunc, so
        # drop back to a plain array (this also restores the string `units`
        # attr for the label).
        if getattr(da, "pint", None) is not None and da.pint.units is not None:
            da = da.pint.dequantify()
        result = xr.apply_ufunc(
            _max_consecutive_run_nd,
            da,
            threshold,
            comp,
            input_core_dims=[[dim], [], []],
            dask="parallelized",
            dask_gufunc_kwargs={"allow_rechunk": True},
            output_dtypes=[np.float64],
        )
        src_units = da.attrs.get("units", "")
        # Dequantify above restores `units` from the pint Unit's own spelling,
        # which renders a CF dimensionless "1" as "dimensionless" (not "1") —
        # a literal "!= '1'" check would miss that and leak "dimensionless"
        # into the label. units_equal compares pint-equivalence instead of
        # exact spelling, so both spellings of "no unit" are recognized.
        is_dimensionless = bool(src_units) and units_equal(src_units, "1")
        unit_suffix = f" {src_units}" if src_units and not is_dimensionless else ""
        symbol = _COMPARISON_SYMBOLS[comparison]
        label = f"{var} spell ({symbol} {threshold}{unit_suffix})"
        # Attrs are rebuilt from scratch, NOT carried over from the source
        # variable: the source's standard_name/long_name describe the input
        # physical quantity, not this derived run-length count, and neither
        # survives the unit change. No standard_name is set — CF has no
        # entry for "consecutive spell length" to verify against. Both
        # GRIB_name and long_name are set to the same label because `plot`'s
        # colorbar-label resolution reads GRIB_name first, falling back to
        # long_name. units="1" is the CF convention for a dimensionless
        # count; this equals real days only when the time dim's cadence is
        # daily — the skill counts entries, not calendar duration.
        # standard_name is explicitly None, not merely absent: the decorator
        # heals attrs missing on an output var from the same-named input var
        # (for skills that only reshape geometry), which would otherwise
        # silently re-attach the source's physical-quantity standard_name to
        # this differently-kinded derived count.
        result.attrs = {
            "GRIB_name": label,
            "long_name": label,
            "units": "1",
            "standard_name": None,
        }
        out_ds[var] = result

    # The collapsed dim disappears from the output (with its coordinates)
    # once no data variable carries it; a dim still carried by a
    # pass-through variable stays.
    if dim in out_ds.dims and all(dim not in out_ds[v].dims for v in out_ds.data_vars):
        out_ds = out_ds.drop_dims(dim)

    return out_ds


if __name__ == "__main__":
    spell_length()
