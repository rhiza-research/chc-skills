# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "pint",
#   "pint-xarray",
#   "xarray",
# ]
# ///
"""Add, subtract, multiply or divide two Zarrs cell by cell, or one Zarr and a constant."""

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_utils import ensure_normalized_longitude
from weather_skills_core.units import (
    AGGREGATION_PERIOD_ATTR,
    convert_dataarray,
    looks_like_precip_total,
    ureg,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

_WIDEN = {1: "int16", 2: "int32", 4: "int64"}
_LABEL = {"add": "sum", "subtract": "difference", "multiply": "product", "divide": "ratio"}


def _widen(da, np):
    """Promote bool/uint so arithmetic cannot wrap."""
    if da.dtype.kind == "b":
        return da.astype(np.int16)
    if da.dtype.kind == "u":
        return da.astype(getattr(np, _WIDEN.get(da.dtype.itemsize, "int64")))
    return da


def _parse_constant(text):
    try:
        return ureg.Quantity(str(text).strip())
    except Exception:  # noqa: BLE001 — pint raises several parse errors
        raise UsageError(
            f"--constant {text!r} is not a number or a quantity like '25.4 mm'"
        ) from None


@weather_skill(name="arithmetic", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    required=True,
    help="A, then optionally B (pass twice for A op B; once with --constant).",
)
@weather_skill.argument(
    "--op",
    required=True,
    choices=["add", "subtract", "multiply", "divide"],
    help="A + B, A - B, A * B, or A / B.",
)
@weather_skill.argument(
    "--constant",
    help="Use this number or quantity ('2', '25.4 mm', '7 day') as B instead of a second input.",
)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument("--units", help="Convert the result to these units (e.g. 'mm', '%%').")
def arithmetic(ds, op, constant, variable, units, **kwargs):
    """Add, subtract, multiply or divide two Zarrs cell by cell, or one Zarr and a constant."""
    import numpy as np
    import pint
    import pint_xarray
    import xarray as xr

    unit_errors = (
        pint.DimensionalityError,
        pint.OffsetUnitCalculusError,
        pint_xarray.errors.PintExceptionGroup,
    )
    if constant is None and len(ds) != 2:
        raise UsageError(f"pass two --input paths, or one with --constant (got {len(ds)})")
    if constant is not None and len(ds) != 1:
        raise UsageError(f"--constant takes one --input (got {len(ds)})")
    ds_a = ensure_normalized_longitude(ds[0])
    ds_b = ensure_normalized_longitude(ds[1]) if constant is None else None
    const = _parse_constant(constant) if constant is not None else None
    if const is not None and op == "divide" and float(const.magnitude) == 0:
        raise UsageError("--constant 0 with --op divide")

    pool = [v for v in ds_a.data_vars if ds_b is None or v in ds_b.data_vars]
    vars_ = list(dict.fromkeys(variable)) if variable else pool
    if not vars_:
        raise UsageError(
            f"inputs share no data variable (A: {list(ds_a.data_vars)}, "
            f"B: {list(ds_b.data_vars)}); rename one to match first (rename skill)"
        )
    for v in vars_:
        if v not in pool:
            where = "A" if ds_b is None else "both inputs"
            raise UsageError(f"--variable {v!r} must be in {where}")

    out = {}
    for v in vars_:
        a = _widen(ds_a[v], np)
        if ds_b is not None:
            b = _widen(ds_b[v], np)
            only_a = [d for d in a.dims if d not in b.dims]
            only_b = [d for d in b.dims if d not in a.dims]
            if only_a and only_b:
                raise UsageError(
                    f"{v!r}: A has dims {only_a} that B lacks and B has {only_b} that A lacks; "
                    "broadcasting would pair every A label with every B label. Put both on "
                    "the same axis first (e.g. step-to-time)"
                )
            period_a = a.attrs.get(AGGREGATION_PERIOD_ATTR)
            period_b = b.attrs.get(AGGREGATION_PERIOD_ATTR)
            if (
                op in ("add", "subtract")
                and period_a
                and period_b
                and (looks_like_precip_total(a) or looks_like_precip_total(b))
                and ureg.Quantity(period_a) != ureg.Quantity(period_b)
            ):
                raise UsageError(
                    f"{v!r}: A is a {period_a} total and B a {period_b} total; combine totals "
                    "over the same period (accumulate or aggregate-temporal both first)"
                )
        else:
            b = const
        try:
            if op == "add":
                res = a + b
            elif op == "subtract":
                res = a - b
            elif op == "multiply":
                res = a * b
            else:
                res = a / b
        except unit_errors as exc:
            raise UsageError(f"{v!r}: units do not allow {op}: {exc}") from None
        except ValueError as exc:  # unlabeled dims of different sizes
            raise UsageError(f"{v!r}: cannot align A and B: {exc}") from None
        empty = [d for d in res.dims if res.sizes[d] == 0]
        if empty:
            raise UsageError(
                f"{v!r}: no overlapping {empty} labels between A and B. Labels must match "
                "exactly: for lat/lon, put both on one grid with coarsen --reference-grid; "
                "for time, step-to-time a forecast and aggregate both the same way"
            )
        if not isinstance(res, xr.DataArray):
            raise UsageError(f"{v!r}: unexpected result type {type(res).__name__}")

        res_units = res.pint.units
        if res_units is not None:
            # K - K is a temperature difference, not an absolute temperature.
            if op == "subtract" and res_units.dimensionality == "[temperature]":
                if "delta_" not in str(res_units):
                    res = res.pint.to("delta_degC")
            elif res_units.dimensionless:
                res = res.pint.to("dimensionless")
        if units:
            try:
                res, _ = convert_dataarray(res, units)
            except unit_errors as exc:
                raise UsageError(f"{v!r}: cannot convert the result to {units!r}: {exc}") from None
        if op == "divide":
            # x / 0 is inf; write it as missing.
            magnitude = res.pint.dequantify() if res.pint.units is not None else res
            res = res.where(np.isfinite(magnitude))

        attrs = dict(ds_a[v].attrs)
        attrs.pop("units", None)
        a_units = ds_a[v].pint.units
        new_units = res.pint.units
        same_kind = (a_units is None and new_units is None) or (
            a_units is not None
            and new_units is not None
            and a_units.dimensionality == new_units.dimensionality
        )
        if not same_kind:
            # A ratio or product is not the input quantity: its CF names would mislead.
            for key in ("standard_name", "GRIB_name", "short_name", "cell_methods"):
                attrs.pop(key, None)
        label = "scaled" if const is not None and op in ("multiply", "divide") else _LABEL[op]
        long_name = str(attrs.get("long_name") or v)
        if label not in long_name.lower():
            attrs["long_name"] = f"{long_name} {label}"
        res.attrs = attrs
        if new_units is not None and new_units.dimensionless:
            res = res.pint.dequantify()
            res.attrs["units"] = "%" if str(new_units) == "percent" else "1"
        out[v] = res
    return xr.Dataset(out)


if __name__ == "__main__":
    arithmetic()
