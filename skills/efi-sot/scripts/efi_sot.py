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
"""Extreme Forecast Index (EFI) and Shift of Tails (SOT) of an ensemble against a model climate."""

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import AGGREGATION_PERIOD_ATTR, dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

# EFI integrates over these climate probabilities (Lalaurette 2003, as ECMWF);
# SOT uses 0.01/0.10 and 0.90/0.99 from the same set.
_LEVELS = [round(0.01 * k, 10) for k in range(1, 100)]


def _pick_variable(fc, clim, variable, climate_variable):
    if variable is None:
        shared = [v for v in fc.data_vars if v in clim.data_vars]
        if len(shared) != 1:
            raise UsageError(
                f"pass -v: forecast has {list(fc.data_vars)}, climate has {list(clim.data_vars)}"
            )
        variable = shared[0]
    if variable not in fc.data_vars:
        raise UsageError(f"-v {variable!r} not in forecast (have {list(fc.data_vars)})")
    climate_variable = climate_variable or variable
    if climate_variable not in clim.data_vars:
        raise UsageError(
            f"{climate_variable!r} not in climate (have {list(clim.data_vars)}); "
            "pass --climate-variable"
        )
    return variable, climate_variable


def _climate_quantiles(clim, sample_dims, xr, np):
    """Climate values at _LEVELS, from a quantile dim or from pooled samples."""
    if "quantile" in clim.dims:
        q = np.asarray(clim["quantile"].values, dtype=float)
        order = np.argsort(q)
        clim, q = clim.isel(quantile=order), q[order]
        if q[0] > _LEVELS[0] + 1e-9 or q[-1] < _LEVELS[-1] - 1e-9:
            raise UsageError(f"climate quantiles span {q[0]:g}-{q[-1]:g}; EFI needs 0.01-0.99")
        pieces = []
        for p in _LEVELS:
            hi = min(int(np.searchsorted(q, p - 1e-9)), len(q) - 1)
            if abs(q[hi] - p) < 1e-9 or hi == 0:
                pieces.append(clim.isel(quantile=hi, drop=True))
                continue
            w = (p - q[hi - 1]) / (q[hi] - q[hi - 1])
            pieces.append(
                clim.isel(quantile=hi - 1, drop=True) * (1 - w)
                + clim.isel(quantile=hi, drop=True) * w
            )
        return xr.concat(pieces, dim=xr.DataArray(_LEVELS, dims="quantile", name="quantile"))
    if clim.chunks is not None:
        clim = clim.chunk({d: -1 for d in sample_dims})
    sample_size = int(np.prod([clim.sizes[d] for d in sample_dims]))
    if sample_size < 10:
        raise UsageError(
            f"climate has {sample_size} samples over {sample_dims}; too few for tail quantiles"
        )
    return clim.quantile(_LEVELS, dim=sample_dims, skipna=True)


@weather_skill(name="efi-sot", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i", "--input", type=Dataset("any"), required=True, help="Forecast ensemble Zarr."
)
@weather_skill.argument(
    "--climate",
    type=Dataset("any"),
    required=True,
    help="Model-climate Zarr: raw samples (hindcast members/years) or a 'quantile' dim.",
)
@weather_skill.argument(
    "--variable", "-v", help="Forecast variable. Default: the one both inputs share."
)
@weather_skill.argument(
    "--climate-variable", help="Climate variable when its name differs. Default: --variable."
)
@weather_skill.argument(
    "--member-dim", default="number", help="Forecast ensemble dim. Default: number."
)
@weather_skill.argument(
    "--climate-dim",
    action="append",
    help="Climate sample dim to pool (repeatable). Default: every climate dim the forecast "
    "lacks, plus --member-dim. Ignored when the climate has a 'quantile' dim.",
)
@weather_skill.argument(
    "--sot",
    default="both",
    choices=["both", "upper", "lower", "none"],
    help="Which Shift of Tails to write. Default: both.",
)
def efi_sot(ds, climate, variable, climate_variable, member_dim, climate_dim, sot, **kwargs):
    """Extreme Forecast Index (EFI) and Shift of Tails (SOT) of an ensemble against a model climate."""
    import numpy as np
    import pint
    import pint_xarray
    import xarray as xr

    fc_ds, clim_ds = ds, climate
    variable, climate_variable = _pick_variable(fc_ds, clim_ds, variable, climate_variable)
    fc, clim = fc_ds[variable], clim_ds[climate_variable]
    if member_dim not in fc.dims:
        raise UsageError(f"--member-dim {member_dim!r} not in forecast dims {list(fc.dims)}")
    if fc.sizes[member_dim] < 2:
        raise UsageError(f"forecast has {fc.sizes[member_dim]} member(s); EFI needs an ensemble")

    period_f = fc.attrs.get(AGGREGATION_PERIOD_ATTR)
    period_c = clim.attrs.get(AGGREGATION_PERIOD_ATTR)
    if period_f and period_c and ureg.Quantity(period_f) != ureg.Quantity(period_c):
        raise UsageError(
            f"forecast is a {period_f} aggregate and the climate a {period_c} one; "
            "aggregate both over the same period"
        )
    # Put the climate in the forecast's units (mm vs m, or a rate vs a total is refused).
    if fc.pint.units is not None and clim.pint.units is not None:
        try:
            clim = clim.pint.to(fc.pint.units)
        except (pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
            raise UsageError(
                f"forecast units {fc.pint.units} and climate units {clim.pint.units} differ in "
                "kind (a rate vs a total?); convert one first (accumulate, unit-convert)"
            ) from None
    fc = dequantify_dataset(fc.to_dataset(name="v"))["v"]
    clim = dequantify_dataset(clim.to_dataset(name="v"))["v"]

    field_dims = [d for d in fc.dims if d != member_dim]
    if "quantile" in clim.dims:
        sample_dims = []
        extra = [d for d in clim.dims if d not in field_dims and d != "quantile"]
        if extra:
            raise UsageError(
                f"climate has a quantile dim and also {extra}, which the forecast lacks; "
                "select or summarize those first"
            )
    else:
        sample_dims = list(dict.fromkeys(climate_dim or [])) or [
            d for d in clim.dims if d not in field_dims
        ]
        missing = [d for d in sample_dims if d not in clim.dims]
        if missing or not sample_dims:
            raise UsageError(
                f"climate sample dims {sample_dims or '(none)'} not usable (climate dims "
                f"{list(clim.dims)}); pass --climate-dim"
            )
        if any(d in field_dims for d in sample_dims):
            raise UsageError(
                f"--climate-dim {sample_dims} overlaps the forecast's field dims {field_dims}"
            )

    qc = _climate_quantiles(clim, sample_dims, xr, np)
    qc = qc.drop_vars([c for c in qc.coords if c not in qc.dims and c != "quantile"])
    # Inner join on the shared field dims (step, latitude, longitude, ...).
    fc, qc = xr.align(fc, qc, join="inner", exclude={member_dim, "quantile"})
    empty = [d for d in fc.dims if fc.sizes[d] == 0]
    if empty:
        raise UsageError(
            f"no overlapping {empty} labels between forecast and climate; put both on one "
            "grid (coarsen --reference-grid) and the same lead axis (select, edit-coords)"
        )
    if fc.chunks is not None:
        fc = fc.chunk({member_dim: -1})

    p = qc["quantile"]
    valid_member = fc.notnull()
    below = (fc < qc).where(valid_member).mean(member_dim)
    integrand = (p - below) / np.sqrt(p * (1 - p))
    efi = (2 / np.pi) * integrand.integrate("quantile")
    has_data = valid_member.any(member_dim) & qc.notnull().all("quantile")
    efi = efi.where(has_data)

    what = fc_ds[variable].attrs.get("long_name") or variable
    base = {}
    if period_f:
        base[AGGREGATION_PERIOD_ATTR] = period_f
    out = {
        f"{variable}_efi": efi.assign_attrs(
            {
                **base,
                "units": "1",
                "long_name": f"Extreme Forecast Index of {what}",
                "comment": "EFI = 2/pi * integral over p of (p - F_f(p)) / sqrt(p(1-p)), "
                "F_f the share of members below the climate p-quantile (Lalaurette 2003)",
            }
        )
    }
    if sot != "none":
        qf = fc.quantile([0.1, 0.9], dim=member_dim, skipna=True)

        def level(x):
            return qc.sel(quantile=x, method="nearest", drop=True)

        if sot in ("both", "upper"):
            denom = level(0.99) - level(0.90)
            upper = (qf.sel(quantile=0.9, drop=True) - level(0.99)) / denom
            out[f"{variable}_sot_upper"] = upper.where(denom > 0).assign_attrs(
                {
                    **base,
                    "units": "1",
                    "long_name": f"Upper Shift of Tails of {what}",
                    "comment": "SOT = (Q_f(0.9) - Q_c(0.99)) / (Q_c(0.99) - Q_c(0.9)); "
                    "NaN where the climate tail is flat",
                }
            )
        if sot in ("both", "lower"):
            denom = level(0.10) - level(0.01)
            lower = (level(0.01) - qf.sel(quantile=0.1, drop=True)) / denom
            out[f"{variable}_sot_lower"] = lower.where(denom > 0).assign_attrs(
                {
                    **base,
                    "units": "1",
                    "long_name": f"Lower Shift of Tails of {what}",
                    "comment": "SOT = (Q_c(0.01) - Q_f(0.1)) / (Q_c(0.1) - Q_c(0.01)); "
                    "NaN where the climate tail is flat",
                }
            )
    result = xr.Dataset(out)
    scalar = {c: fc_ds[c] for c in fc_ds.coords if fc_ds[c].ndim == 0 and c not in result.coords}
    return result.assign_coords(scalar)


if __name__ == "__main__":
    efi_sot()
