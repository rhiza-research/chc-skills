# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "xarray",
# ]
# ///
"""Quantiles of a Zarr's variables over one or more named dimensions."""

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import dequantify_dataset

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"

PRESETS = {
    "terciles": [1 / 3, 2 / 3],
    "quartiles": [0.25, 0.5, 0.75],
    "quintiles": [0.2, 0.4, 0.6, 0.8],
    "deciles": [round(0.1 * k, 10) for k in range(1, 10)],
    "percentiles": [round(0.01 * k, 10) for k in range(1, 100)],
}
METHODS = [
    "linear",
    "hazen",
    "weibull",
    "median_unbiased",
    "normal_unbiased",
    "lower",
    "higher",
    "midpoint",
    "nearest",
]


def parse_levels(values):
    """Probabilities from repeated --quantile values: numbers, comma lists or presets."""
    levels = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if part.lower() in PRESETS:
                levels.extend(PRESETS[part.lower()])
                continue
            try:
                q = float(part)
            except ValueError:
                raise UsageError(
                    f"--quantile {part!r} is not a number or one of {', '.join(PRESETS)}"
                ) from None
            if not 0 <= q <= 1:
                hint = f" (pass {q / 100:g}, not {q:g})" if 1 < q <= 100 else ""
                raise UsageError(f"--quantile must be between 0 and 1; got {q:g}{hint}")
            levels.append(q)
    if not levels:
        raise UsageError("--quantile needs at least one value")
    return sorted(set(levels))


@weather_skill(name="quantile", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument(
    "--dim",
    action="append",
    required=True,
    help="Dim to take quantiles over (repeatable; several dims are pooled into one sample).",
)
@weather_skill.argument(
    "--quantile",
    "-q",
    action="append",
    required=True,
    help="Probability 0-1, a comma list (0.1,0.5,0.9), or a preset: "
    "terciles, quartiles, quintiles, deciles, percentiles. Repeatable.",
)
@weather_skill.argument(
    "--method",
    default="linear",
    choices=METHODS,
    help="Quantile estimator (numpy names). Default: linear.",
)
@weather_skill.argument(
    "--squeeze",
    action="store_true",
    help="With a single --quantile, drop the quantile dim.",
)
def quantile(ds, variable, dim, quantile, method, squeeze, **kwargs):
    """Quantiles of a Zarr's variables over one or more named dimensions."""
    levels = parse_levels(quantile)
    if squeeze and len(levels) != 1:
        raise UsageError(f"--squeeze needs exactly one quantile; got {len(levels)}")
    dims = list(dict.fromkeys(dim))
    for d in dims:
        if d not in ds.dims:
            raise UsageError(f"--dim {d!r} not in dataset (have {list(ds.dims)})")
    if "quantile" in ds.dims and "quantile" not in dims:
        raise UsageError(
            "input already has a 'quantile' dim; rename it (edit-coords) or include it in --dim"
        )
    selected = list(dict.fromkeys(variable)) if variable else list(ds.data_vars)
    for var in selected:
        if var not in ds.data_vars:
            raise UsageError(f"--variable {var!r} not in dataset (have {list(ds.data_vars)})")

    # pint quantities do not go through quantile; units are kept as attrs.
    ds = dequantify_dataset(ds)
    out = ds.copy()
    for var in selected:
        da = ds[var]
        rdims = [d for d in dims if d in da.dims]
        if not rdims:
            continue
        if da.chunks is not None:
            # Dask quantiles need each reduced dim in one chunk.
            da = da.chunk({d: -1 for d in rdims})
        if da.dtype.kind not in "f":
            da = da.astype("float64")
        result = da.quantile(levels, dim=rdims, method=method, skipna=True, keep_attrs=True)
        if squeeze:
            result = result.squeeze("quantile", drop=True)
        attrs = dict(ds[var].attrs)
        what = attrs.get("long_name") or attrs.get("standard_name") or var
        label = "Quantile" if squeeze else "Quantiles"
        attrs["long_name"] = f"{label} of {what} across {', '.join(rdims)}"
        attrs["quantile_dims"] = " ".join(rdims)
        attrs["quantile_method"] = method
        if squeeze:
            attrs["quantile"] = levels[0]
        for key in ("GRIB_name", "short_name", "comment"):
            attrs.pop(key, None)
        result.attrs = attrs
        out[var] = result

    if "quantile" in out.coords:
        out["quantile"].attrs.update(long_name="cumulative probability", units="1")
    for d in dims:
        if d in out.dims and all(d not in out[v].dims for v in out.data_vars):
            out = out.drop_dims(d)
    return out


if __name__ == "__main__":
    quantile()
