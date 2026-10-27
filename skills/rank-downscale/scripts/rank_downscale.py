# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime",
#   "numpy",
#   "scipy",
#   "xarray",
#   "xarray-regrid",
# ]
# ///
"""Downscale a forecast onto an observed climatology's grid by rank mapping against its model climatology."""

import math
import sys

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims
from weather_skills_core.standard_utils import ensure_normalized_longitude, grid_spacing
from weather_skills_core.units import units_equal

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.0"


def _spacing(ds, dim):
    return grid_spacing(ds[dim].values)


def _target_coord(coord, new_spacing):
    import numpy as np

    vmin, vmax = float(np.min(coord)), float(np.max(coord))
    target = vmin + new_spacing * np.arange(int(np.floor((vmax - vmin) / new_spacing)) + 1)
    return target[::-1] if coord[0] > coord[-1] else target


def _quantiles(values, model):
    """One cell: each value's mid-rank position in the model sample, in [0, 1].

    ``model`` is the cell's climatology sample (any shape, pooled); ``values``
    keeps its shape. NaN where the value is NaN or the sample is empty.
    """
    import numpy as np

    values = np.asarray(values, dtype=float)
    model = np.ravel(model).astype(float)
    out = np.full(values.shape, np.nan, dtype=float)
    ok = ~np.isnan(values)
    sorted_m = np.sort(model[~np.isnan(model)])
    if not ok.any() or not sorted_m.size:
        return out
    # Mid-rank: a value tied with many model values (precip zeros) sits in the
    # middle of its tie block. Taking the top of the block (side="right") put
    # a dry forecast at the model's dry fraction and mapped it to observed rain.
    left = np.searchsorted(sorted_m, values[ok], side="left")
    right = np.searchsorted(sorted_m, values[ok], side="right")
    out[ok] = np.clip((left + right) / (2 * sorted_m.size), 0.0, 1.0)
    return out


def _pick(quants, ref):
    """One cell: the sorted reference value whose rank is nearest each quantile.

    The output is always one of the observed sample values, not an
    interpolation between two of them.
    """
    import numpy as np

    quants = np.asarray(quants, dtype=float)
    ref = np.ravel(ref).astype(float)
    sorted_r = np.sort(ref[~np.isnan(ref)])
    out = np.full(quants.shape, np.nan, dtype=float)
    ok = ~np.isnan(quants)
    if not ok.any() or not sorted_r.size:
        return out
    index = np.rint(quants[ok] * (sorted_r.size - 1)).astype(int)
    out[ok] = sorted_r[np.clip(index, 0, sorted_r.size - 1)]
    return out


def _nan_gaussian(values, sigma):
    """Gaussian smoothing over the last two axes that ignores NaNs (normalized convolution)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    sigma = (0,) * (values.ndim - 2) + (sigma, sigma)
    nan = np.isnan(values)
    smoothed = gaussian_filter(np.where(nan, 0.0, values), sigma=sigma)
    weights = gaussian_filter((~nan).astype(float), sigma=sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = smoothed / weights
    out[weights == 0] = np.nan
    return out


def _nan_closing(values, size):
    """Grey closing over the last two axes; NaN cells take no part and stay NaN."""
    import numpy as np
    from scipy.ndimage import grey_closing

    nan = np.isnan(values)
    # Closing is dilation then erosion: filling gaps with the field minimum
    # keeps them from raising their neighbours in the dilation step.
    filled = np.where(nan, np.nanmin(values) if (~nan).any() else 0.0, values)
    out = grey_closing(filled, size=(1,) * (values.ndim - 2) + (size, size))
    out[nan] = np.nan
    return out


def _drop_partial_windows(ds):
    """``ds`` without the steps whose ``aggregation_coverage`` is below 1.

    aggregate-temporal stamps that coord on every window; a window cut short
    by the end of the forecast (day 43-46 of a weekly roll-up) has coverage < 1.
    """
    import numpy as np

    if "step" not in ds.dims or "aggregation_coverage" not in ds.coords:
        return ds
    coverage = ds["aggregation_coverage"]
    if set(coverage.dims) != {"step"}:
        return ds
    full = coverage.values >= 1 - 1e-9
    if full.all():
        return ds
    if not full.any():
        raise UsageError(
            "every forecast window is incomplete (aggregation_coverage < 1); pass "
            "--keep-partial or aggregate over a shorter --period"
        )
    dropped = [str(s.astype("timedelta64[D]")) for s in ds["step"].values[~full]]
    print(
        f"Dropped incomplete windows at step {', '.join(dropped)} "
        f"(coverage {', '.join(f'{c:.2f}' for c in coverage.values[~full])}); "
        "--keep-partial keeps them.",
        file=sys.stderr,
    )
    return ds.isel(step=np.nonzero(full)[0])


def _on_output_grid(ref, lat_dim, lon_dim, grid):
    """``ref`` cut to ``grid``'s lat/lon points, which it must all contain."""
    ref = ensure_normalized_longitude(ref, lon_dim)
    # The reference must hold every output grid point (it may extend past
    # them, e.g. the same continental grid passed as --reference-grid).
    try:
        ref = ref.sel(
            {lat_dim: grid[lat_dim], lon_dim: grid[lon_dim]}, method="nearest", tolerance=1e-6
        )
    except KeyError:
        raise UsageError(
            "--qq-reference must contain every post-downscale grid point; build it on "
            "the output grid (or pass it as --reference-grid too)"
        ) from None
    return ref.assign_coords({lat_dim: grid[lat_dim], lon_dim: grid[lon_dim]})


def _pool(da, keep, name):
    """Stack every dim of ``da`` except ``keep`` into one sample dim called ``name``."""
    da = da.reset_coords(drop=True)
    pooled = [d for d in da.dims if d not in keep]
    if len(pooled) == 1:
        return da.rename({pooled[0]: name})
    return da.stack({name: pooled}).reset_index(name, drop=True)


def _rank_downscale(
    da, model, ref, lat_dim, lon_dim, new_lat, new_lon, closing_size, rank_sigma, field_sigma
):
    """Rank ``da`` in ``model`` on the input grid, carry the ranks to the output grid, pick from ``ref``.

    ``model`` is on the input grid, ``ref`` on the output grid. ``step`` is
    matched value by value when the input has it; every other non-spatial dim
    of a sample is pooled.
    """
    import numpy as np
    import xarray as xr

    spatial = (lat_dim, lon_dim)
    match = [d for d in ("step",) if d in da.dims]
    for flag, sample in (("--qq-model", model), ("--qq-reference", ref)):
        for d in match:
            if d not in sample.dims:
                raise UsageError(
                    f"{flag} has no {d!r} dim but the input does; rank matches {d} value by "
                    f"value, so build the sample with the same {d} values"
                )
            missing = np.setdiff1d(da[d].values, sample[d].values)
            if missing.size:
                raise UsageError(
                    f"{flag} lacks {d} {[str(m) for m in missing[:3]]} that the input has; "
                    "aggregate the sample exactly like the input"
                )
        if not set(sample.dims) - set(spatial) - set(match):
            raise UsageError(
                f"{flag} has no sample dim besides {[*spatial, *match]}: rank needs a "
                "climatology sample dim (e.g. year, number)"
            )
    sel = {d: da[d].values for d in match}
    model = _pool(model.sel(sel), (*spatial, *match), "_rank_model")
    ref = _pool(ref.sel(sel), (*spatial, *match), "_rank_ref")

    # Rank on the input's own grid: each input cell against its own model sample.
    loop = [d for d in da.dims if d not in (*spatial, *match)]
    quants = xr.apply_ufunc(
        _quantiles,
        da,
        model,
        input_core_dims=[loop, ["_rank_model"]],
        output_core_dims=[loop],
        vectorize=True,
        output_dtypes=[float],
    )
    # Every output cell takes its nearest input cell's rank.
    quants = quants.sel({lat_dim: new_lat, lon_dim: new_lon}, method="nearest")
    quants = quants.assign_coords({lat_dim: new_lat, lon_dim: new_lon})
    quants = quants.transpose(..., lat_dim, lon_dim)
    values = quants.values
    if closing_size:
        values = _nan_closing(values, closing_size)
    if rank_sigma:
        values = _nan_gaussian(values, rank_sigma)
    quants = quants.copy(data=np.clip(values, 0.0, 1.0))

    loop = [d for d in quants.dims if d not in (*spatial, *match)]
    out = xr.apply_ufunc(
        _pick,
        quants,
        ref,
        input_core_dims=[loop, ["_rank_ref"]],
        output_core_dims=[loop],
        vectorize=True,
        output_dtypes=[float],
    ).transpose(*quants.dims)
    if field_sigma:
        out = out.copy(data=_nan_gaussian(out.values, field_sigma))
    # Keep the reference's own gaps (sea, outside its domain).
    out = out.where(ref.notnull().any("_rank_ref"))
    return out.transpose(*da.dims)


@weather_skill(
    name="rank-downscale",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True)
@weather_skill.argument("--variable", "-v")
@weather_skill.argument("-f", "--factor", type=int)
@weather_skill.argument("--target-resolution", type=float)
@weather_skill.argument("--reference-grid")
@weather_skill.argument(
    "--qq-model",
    required=True,
    help="The model's own climatology sample (reforecasts / hindcasts) for the same variable and windows.",
)
@weather_skill.argument(
    "--qq-reference",
    required=True,
    help="Observed climatology sample covering every output grid point (the values picked from).",
)
@weather_skill.argument(
    "--closing-size",
    type=int,
    default=0,
    help="Grey-closing window (output-grid cells) on the ranks. Default: 0 (off).",
)
@weather_skill.argument(
    "--rank-sigma",
    type=float,
    default=0.0,
    help="Gaussian sigma (output-grid cells) smoothing the ranks. Default: 0 (off).",
)
@weather_skill.argument(
    "--field-sigma",
    type=float,
    default=0.0,
    help="Gaussian sigma (output-grid cells) smoothing the result. Default: 0 (off).",
)
@weather_skill.argument(
    "--keep-partial",
    action="store_true",
    help=(
        "Keep forecast windows that aggregate-temporal stamped as incomplete "
        "(aggregation_coverage < 1). Default: drop them."
    ),
)
def rank_downscale(
    ds,
    variable,
    factor,
    target_resolution,
    reference_grid,
    qq_model,
    qq_reference,
    closing_size,
    rank_sigma,
    field_sigma,
    keep_partial,
    **kwargs,
):
    """Downscale a forecast onto an observed climatology's grid by rank mapping against its model climatology."""
    import numpy as np
    import xarray as xr

    n = sum(x is not None for x in (factor, target_resolution, reference_grid))
    if n != 1:
        raise UsageError("exactly one of --factor, --target-resolution, --reference-grid")
    if closing_size < 0 or rank_sigma < 0 or field_sigma < 0:
        raise UsageError("--closing-size, --rank-sigma and --field-sigma must be >= 0")

    lat_dim, lon_dim = detect_spatial_dims(ds)
    if variable:
        ds = ds[[variable]]
    ds = ensure_normalized_longitude(ds, lon_dim)

    # Each cell's sample is every value along the samples' non-spatial dims
    # (years, members) except step, which is matched value by value.
    samples = {}
    for flag, path in (("--qq-reference", qq_reference), ("--qq-model", qq_model)):
        other = xr.open_zarr(path, consolidated=False)
        missing = [v for v in ds.data_vars if v not in other.data_vars]
        if missing:
            raise UsageError(
                f"{flag} has no variable {missing} (has {list(other.data_vars)}); "
                "give one side the other's name with edit-coords --rename, or limit "
                "the input with --variable"
            )
        for v in ds.data_vars:
            want = ds[v].attrs.get("units") or format(ds[v].pint.units or "", "~cf")
            have = other[v].attrs.get("units")
            if not units_equal(have, want):
                raise UsageError(
                    f"{flag} {v!r} is in {have!r} but the input is in {want!r}; "
                    "unit-convert one side first"
                )
            if not set(other[v].dims) - {lat_dim, lon_dim}:
                raise UsageError(
                    f"{flag} {v!r} has only {list(other[v].dims)}: rank needs a "
                    "climatology sample dim besides lat/lon (e.g. time over many "
                    "years, or number)"
                )
        samples[flag] = other
    in_lat, in_lon = _spacing(ds, lat_dim), _spacing(ds, lon_dim)
    if not (in_lat > 0 and in_lon > 0):
        raise UsageError(
            f"input grid spacing is zero (lat {in_lat}, lon {in_lon}); each latitude and "
            "longitude must appear once"
        )

    if reference_grid is not None:
        ref = xr.open_zarr(reference_grid, consolidated=False)
        ref_lat_dim, ref_lon_dim = detect_spatial_dims(ref)
        ref = ensure_normalized_longitude(ref, ref_lon_dim)
        ref_lat, ref_lon = _spacing(ref, ref_lat_dim), _spacing(ref, ref_lon_dim)
        # Only reference points within half an input cell of the input's span
        # (as coarsen does): a continental reference would otherwise write a
        # mostly empty grid. 1e-6 slack for float32 coords.
        new_lat, new_lon = (
            np.asarray(ref[rd].values)[
                (ref[rd].values >= float(ds[d].min()) - sp / 2 - 1e-6)
                & (ref[rd].values <= float(ds[d].max()) + sp / 2 + 1e-6)
            ]
            for rd, d, sp in ((ref_lat_dim, lat_dim, in_lat), (ref_lon_dim, lon_dim, in_lon))
        )
        if not new_lat.size or not new_lon.size:
            raise UsageError("--reference-grid does not overlap the input's lat/lon span")
        # 1% rel_tol: float noise / half-cell shift is not a resolution change.
        if (in_lat < ref_lat and not math.isclose(in_lat, ref_lat, rel_tol=1e-2)) or (
            in_lon < ref_lon and not math.isclose(in_lon, ref_lon, rel_tol=1e-2)
        ):
            raise UsageError("--reference-grid is coarser than input; use coarsen")
    else:
        if factor is not None:
            if factor < 1:
                raise UsageError("--factor must be >= 1")
            # Split each input cell into `factor` steps between the input's own
            # points, so every factor-th output point is an input point and
            # --factor 1 keeps the grid. Stepping by a measured spacing instead
            # drifts on float32 grids such as CHIRPS.
            new_lat, new_lon = (
                np.linspace(c[0], c[-1], (c.size - 1) * factor + 1)
                for c in (ds[lat_dim].values.astype(float), ds[lon_dim].values.astype(float))
            )
        else:
            too_coarse = (
                in_lat < target_resolution
                and not math.isclose(in_lat, target_resolution, rel_tol=1e-2)
            ) or (
                in_lon < target_resolution
                and not math.isclose(in_lon, target_resolution, rel_tol=1e-2)
            )
            if target_resolution <= 0 or too_coarse:
                raise UsageError("--target-resolution must be finer-or-equal to input")
            new_lat = _target_coord(ds[lat_dim].values, target_resolution)
            new_lon = _target_coord(ds[lon_dim].values, target_resolution)

    target = xr.Dataset(
        coords={
            lat_dim: (lat_dim, new_lat, dict(ds[lat_dim].attrs)),
            lon_dim: (lon_dim, new_lon, dict(ds[lon_dim].attrs)),
        }
    )
    if not keep_partial:
        ds = _drop_partial_windows(ds)
    ref = _on_output_grid(samples["--qq-reference"], lat_dim, lon_dim, target)
    model = ensure_normalized_longitude(samples["--qq-model"], lon_dim)[list(ds.data_vars)]
    try:
        model = model.sel(
            {lat_dim: ds[lat_dim], lon_dim: ds[lon_dim]}, method="nearest", tolerance=1e-6
        ).assign_coords({lat_dim: ds[lat_dim], lon_dim: ds[lon_dim]})
    except KeyError:
        # Not on the input grid: interpolate the sample onto it.
        import xarray_regrid  # noqa: F401

        model = model.regrid.linear(xr.Dataset(coords={lat_dim: ds[lat_dim], lon_dim: ds[lon_dim]}))
    out = xr.Dataset(coords=target.coords)
    for v in ds.data_vars:
        mapped = _rank_downscale(
            ds[v].load(),
            model[v].load(),
            ref[v].load(),
            lat_dim,
            lon_dim,
            target[lat_dim],
            target[lon_dim],
            closing_size,
            rank_sigma,
            field_sigma,
        )
        mapped.attrs = dict(ds[v].attrs)
        out[v] = mapped
    # Scalar and non-spatial coords (init time, step, number) carry over.
    keep = [c for c in ds.coords if not {lat_dim, lon_dim} & set(ds[c].dims)]
    out = out.assign_coords({c: ds[c] for c in keep if c not in out.coords})
    out.attrs = dict(ds.attrs)
    return out


if __name__ == "__main__":
    rank_downscale()
