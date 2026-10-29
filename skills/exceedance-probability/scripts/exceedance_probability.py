# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
# ]
# ///
"""Compute the percentage of ensemble members exceeding a fixed threshold.

For each selected data variable, computes the percentage of entries along
``--dim`` (e.g. ``number``, aliased to ``member`` in the dim ontology, for an
ECMWF/GEFS ensemble) satisfying ``value <comparison> threshold``, per
remaining grid cell/step. Data variables that don't carry ``--dim`` pass
through untouched.
"""

import operator
import sys

from weather_skills_core import Dataset, UsageError, weather_skill
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


@weather_skill(
    name="exceedance-probability",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help="Restrict the computation to this data variable. Repeatable. "
    "Each selected variable must carry --dim. Default (unset): every "
    "data variable carrying --dim.",
)
@weather_skill.argument(
    "--dim",
    required=True,
    help="Ensemble/member dimension to compute the percentage over "
    "(e.g. 'number', aliased to 'member', for an ECMWF/GEFS ensemble forecast).",
)
@weather_skill.argument(
    "--threshold",
    type=float,
    required=True,
    help="Value to compare each member against, in the variable's own units.",
)
@weather_skill.argument(
    "--comparison",
    required=True,
    choices=["gt", "ge", "lt", "le"],
    help="Comparison applied as value <op> threshold.",
)
def exceedance_probability(ds, variable, dim, threshold, comparison, **kwargs):
    """Compute the percentage of ensemble members exceeding a fixed threshold."""
    if dim not in ds.dims:
        raise UsageError(f"--dim '{dim}' not in dims {list(ds.dims)}.")

    # Variable selection, mirroring `summarize-dim`: explicit --variable names
    # must be data variables and must each carry --dim. Default selection
    # takes every data variable carrying --dim; the rest pass through
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
            raise UsageError(f"variable(s) {missing} do not carry --dim '{dim}'.")
    else:
        selected = [v for v in ds.data_vars if dim in ds[v].dims]
        if not selected:
            raise UsageError(f"no data variable carries --dim '{dim}'.")

    passthrough = [v for v in ds.data_vars if v not in selected]
    if passthrough:
        print(
            f"Note: passing through unreduced data variable(s) {passthrough}.",
            file=sys.stderr,
        )

    print(
        f"Computing exceedance probability dim={dim} comparison={comparison} "
        f"threshold={threshold} variables={selected}",
        file=sys.stderr,
    )

    comp = _COMPARISONS[comparison]
    out_ds = ds.copy()
    for var in selected:
        da = ds[var]
        # The decorator opens data-variable inputs as pint quantities; a bare
        # float threshold can't compare against one, so drop back to a plain
        # array (this also restores the string `units` attr for the label).
        if getattr(da, "pint", None) is not None and da.pint.units is not None:
            da = da.pint.dequantify()
        condition_met = comp(da, threshold)
        pct = condition_met.sum(dim=dim) / da.sizes[dim] * 100
        src_units = da.attrs.get("units", "")
        # Dequantify above restores `units` from the pint Unit's own spelling,
        # which renders a CF dimensionless "1" as "dimensionless" (not "1") —
        # a literal "!= '1'" check would miss that and leak "dimensionless"
        # into the label. units_equal compares pint-equivalence instead of
        # exact spelling, so both spellings of "no unit" are recognized.
        is_dimensionless = bool(src_units) and units_equal(src_units, "1")
        unit_suffix = f" {src_units}" if src_units and not is_dimensionless else ""
        described = da.attrs.get("long_name", var)
        symbol = _COMPARISON_SYMBOLS[comparison]
        label = f"P({described}) {symbol} {threshold}{unit_suffix}"
        # Attrs are rebuilt from scratch, NOT carried over from the source
        # variable: the source's standard_name/long_name describe the input
        # physical quantity, not this derived percentage, and neither
        # survives the unit change to `%`. No standard_name is set — CF has
        # no entry for "probability of exceeding a threshold" to verify
        # against. Both GRIB_name and long_name are set to the same label
        # because `plot`'s colorbar-label resolution reads GRIB_name first,
        # falling back to long_name. standard_name is explicitly None, not
        # merely absent: the decorator heals attrs missing on an output var
        # from the same-named input var (for skills that only reshape
        # geometry), which would otherwise silently re-attach the source's
        # physical-quantity standard_name to this differently-kinded derived
        # percentage.
        pct.attrs = {
            "GRIB_name": label,
            "long_name": label,
            "units": "%",
            "standard_name": None,
        }
        out_ds[var] = pct

    # The collapsed dim disappears from the output (with its coordinates)
    # once no data variable carries it; a dim still carried by a
    # pass-through variable stays.
    if dim in out_ds.dims and all(dim not in out_ds[v].dims for v in out_ds.data_vars):
        out_ds = out_ds.drop_dims(dim)

    return out_ds


if __name__ == "__main__":
    exceedance_probability()
