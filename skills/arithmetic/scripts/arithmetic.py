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
"""Add, subtract, multiply or divide Zarrs cell by cell, optionally with a constant."""

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
    help="Operands in order: A, B, ... (A op B op C, left to right). One is enough with --constant.",
)
@weather_skill.argument(
    "--op",
    required=True,
    choices=["add", "subtract", "multiply", "divide"],
    help="A + B, A - B, A * B, or A / B.",
)
@weather_skill.argument(
    "--constant",
    help="A number or quantity ('2', '25.4 mm', '100 %%') used as the last operand "
    "(or the first with --constant-first).",
)
@weather_skill.argument(
    "--constant-first",
    action="store_true",
    help="Put --constant before the inputs: VALUE op A op B ... (e.g. 100 %% - above - below).",
)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument("--units", help="Convert the result to these units (e.g. 'mm', '%%').")
def arithmetic(ds, op, constant, constant_first, variable, units, **kwargs):
    """Add, subtract, multiply or divide Zarrs cell by cell, optionally with a constant."""
    import numpy as np
    import pint
    import pint_xarray
    import xarray as xr

    unit_errors = (
        pint.DimensionalityError,
        pint.OffsetUnitCalculusError,
        pint_xarray.errors.PintExceptionGroup,
    )
    if constant is None and len(ds) < 2:
        raise UsageError(f"pass at least two --input paths, or one with --constant (got {len(ds)})")
    if constant_first and constant is None:
        raise UsageError("--constant-first needs --constant")
    inputs = [ensure_normalized_longitude(d) for d in ds]
    ds_a = inputs[0]
    const = _parse_constant(constant) if constant is not None else None
    if const is not None and op == "divide" and not constant_first and float(const.magnitude) == 0:
        raise UsageError("--constant 0 with --op divide")

    pool = [v for v in ds_a.data_vars if all(v in d.data_vars for d in inputs[1:])]
    vars_ = list(dict.fromkeys(variable)) if variable else pool
    if not vars_:
        listing = ", ".join(f"{list(d.data_vars)}" for d in inputs)
        raise UsageError(
            f"inputs share no data variable ({listing}); rename them to match first (rename skill)"
        )
    for v in vars_:
        if v not in pool:
            where = "the input" if len(inputs) == 1 else "every input"
            raise UsageError(f"--variable {v!r} must be in {where}")

    def _apply(x, y, v):
        try:
            if op == "add":
                return x + y
            if op == "subtract":
                return x - y
            if op == "multiply":
                return x * y
            return x / y
        except unit_errors as exc:
            raise UsageError(f"{v!r}: units do not allow {op}: {exc}") from None
        except ValueError as exc:  # unlabeled dims of different sizes
            raise UsageError(f"{v!r}: cannot align the inputs: {exc}") from None

    out = {}
    for v in vars_:
        fields = [_widen(d[v], np) for d in inputs]
        for i, b in enumerate(fields[1:], start=2):
            seen = {d for f in fields[: i - 1] for d in f.dims}
            only_a = [d for d in seen if d not in b.dims]
            only_b = [d for d in b.dims if d not in seen]
            if only_a and only_b:
                raise UsageError(
                    f"{v!r}: input {i} has dims {only_b} the earlier inputs lack, and they have "
                    f"{only_a} it lacks; broadcasting would pair every label with every other. "
                    "Put them on the same axis first (e.g. step-to-time)"
                )
        if op in ("add", "subtract"):
            periods = {
                str(f.attrs[AGGREGATION_PERIOD_ATTR]): f
                for f in fields
                if f.attrs.get(AGGREGATION_PERIOD_ATTR)
            }
            if len({ureg.Quantity(p) for p in periods}) > 1 and any(
                looks_like_precip_total(f) for f in periods.values()
            ):
                raise UsageError(
                    f"{v!r}: inputs are totals over different periods ({sorted(periods)}); "
                    "combine totals over the same period (accumulate or aggregate-temporal first)"
                )
        if const is None:
            operands = fields
        elif constant_first:
            operands = [const, *fields]
        else:
            operands = [*fields, const]
        res = operands[0]
        for nxt in operands[1:]:
            res = _apply(res, nxt, v)
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
            elif res_units.dimensionless and res_units != ureg.percent:
                # % + % stays %; mixed scales (mm / m, 1 / %) collapse to 1.
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
        scaled = const is not None and len(inputs) == 1 and op in ("multiply", "divide")
        label = "scaled" if scaled and not constant_first else _LABEL[op]
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
