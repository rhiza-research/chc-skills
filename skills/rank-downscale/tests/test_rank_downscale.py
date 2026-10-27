"""Correctness tests for rank-downscale."""

import pytest
import xarray as xr
from conftest import load_skill, run_skill, write_zarr


@pytest.fixture(scope="module")
def downscale():
    return load_skill("rank-downscale", "rank_downscale").rank_downscale


def _rank_grid(values, dims, coords):
    """Precip Dataset with CF lat/lon attrs for the rank tests."""
    import numpy as np

    ds = xr.Dataset({"precip": (dims, np.asarray(values, dtype=float))}, coords=coords)
    ds["precip"].attrs.update(units="mm day-1")
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    if "step" in ds.coords:
        ds["step"].attrs.update(standard_name="forecast_period")
    return ds


def _rank_case(tmp_path, forecast_values, model_values=None, ref_values=None, ref_steps=2):
    """Forecast (number, step, lat, lon) on a 2x2 1 degree grid, model sample
    (year, number, step, lat, lon) on the same grid, reference (year, step, lat, lon)
    on the factor-2 grid.

    Default samples: model year y is y (step 0) and 10*y (step 1); reference
    year y is 100+y (step 0) and 1000+y (step 1), 20 years each.
    """
    import numpy as np

    steps = np.array([np.timedelta64(d, "D") for d in range(2)]).astype("timedelta64[ns]")
    lats, lons = [0.0, 1.0], [10.0, 11.0]
    fine_lats, fine_lons = [0.0, 0.5, 1.0], [10.0, 10.5, 11.0]
    years = np.arange(20)
    forecast = _rank_grid(
        np.broadcast_to(np.asarray(forecast_values, dtype=float), (1, 2, 2, 2)),
        ("number", "step", "latitude", "longitude"),
        {
            "number": [0],
            "step": steps,
            "latitude": lats,
            "longitude": lons,
            "time": np.datetime64("2026-01-01", "ns"),
        },
    )
    if model_values is None:
        model_values = np.stack([years, 10 * years], axis=-1)  # (year, step)
    model_values = np.asarray(model_values, dtype=float)
    model = _rank_grid(
        np.broadcast_to(model_values[:, None, :, None, None], (len(model_values), 1, 2, 2, 2)),
        ("year", "number", "step", "latitude", "longitude"),
        {
            "year": np.arange(len(model_values)),
            "number": [0],
            "step": steps,
            "latitude": lats,
            "longitude": lons,
        },
    )
    if ref_values is None:
        ref_values = np.broadcast_to(
            np.stack([100 + years, 1000 + years], axis=-1)[:, :, None, None], (20, 2, 3, 3)
        )
    ref_values = np.asarray(ref_values, dtype=float)[:, :ref_steps]
    ref = _rank_grid(
        ref_values,
        ("year", "step", "latitude", "longitude"),
        {
            "year": np.arange(ref_values.shape[0]),
            "step": steps[:ref_steps],
            "latitude": fine_lats,
            "longitude": fine_lons,
        },
    )
    return (
        write_zarr(forecast, tmp_path / "fc.zarr"),
        write_zarr(model, tmp_path / "model.zarr"),
        write_zarr(ref, tmp_path / "ref.zarr"),
    )


def _run_rank(downscale, tmp_path, fc, model, ref, *extra):
    out = tmp_path / "out.zarr"
    run_skill(
        downscale, "-i", str(fc), "-o", str(out), "-f", "2",
        "--qq-model", str(model), "--qq-reference", str(ref), *extra,
    )  # fmt: skip
    return xr.open_zarr(out, consolidated=True)["precip"]


def test_rank_matches_steps_and_picks_observed_values(tmp_path, downscale):
    import numpy as np

    # The model sample's 19th of 20 values in each step: mid-rank 0.925 -> reference rank 18.
    fc, model, ref = _rank_case(tmp_path, np.array([18.0, 180.0])[None, :, None, None])
    out = _run_rank(downscale, tmp_path, fc, model, ref)
    assert out.dims == ("number", "step", "latitude", "longitude")
    assert out.sizes["latitude"] == 3 and out.sizes["longitude"] == 3
    assert np.all(out.isel(step=0).values == 118.0)
    assert np.all(out.isel(step=1).values == 1018.0)


def test_rank_dry_ties_take_middle_of_block(tmp_path, downscale):
    import numpy as np

    # 12 of 20 model years dry: a dry forecast sits at quantile 0.3 -> rank 6 (106).
    model = np.stack([np.r_[np.zeros(12), np.arange(1, 9)]] * 2, axis=-1)
    fc, model, ref = _rank_case(tmp_path, 0.0, model_values=model)
    out = _run_rank(downscale, tmp_path, fc, model, ref)
    assert np.all(out.isel(step=0).values == 106.0)


def test_rank_pools_members_and_years(tmp_path, downscale):
    import numpy as np

    steps = np.array([np.timedelta64(0, "D")]).astype("timedelta64[ns]")
    coords = {"step": steps, "latitude": [0.0, 1.0], "longitude": [10.0, 11.0]}
    fc = _rank_grid(np.full((1, 2, 2), 5.0), ("step", "latitude", "longitude"), coords)
    # 2 years x 5 members = 10 values 0..9: 5 sits at quantile 0.55.
    model = _rank_grid(
        np.broadcast_to(np.arange(10.0).reshape(2, 5, 1, 1, 1), (2, 5, 1, 2, 2)),
        ("year", "number", "step", "latitude", "longitude"),
        {"year": [0, 1], "number": np.arange(5), **coords},
    )
    ref = _rank_grid(
        np.broadcast_to(np.arange(11.0).reshape(11, 1, 1, 1), (11, 1, 2, 2)),
        ("year", "step", "latitude", "longitude"),
        {"year": np.arange(11), **coords},
    )
    out = tmp_path / "out.zarr"
    run_skill(
        downscale, "-i", str(write_zarr(fc, tmp_path / "fc.zarr")), "-o", str(out),
        "-f", "1",
        "--qq-model", str(write_zarr(model, tmp_path / "m.zarr")),
        "--qq-reference", str(write_zarr(ref, tmp_path / "r.zarr")),
    )  # fmt: skip
    # rint(0.55 * 10) = 6 (5.5 rounds half to even)
    assert np.all(xr.open_zarr(out, consolidated=True)["precip"].values == 6.0)


def test_rank_keeps_reference_nan_mask(tmp_path, downscale):
    import numpy as np

    years = np.arange(20)
    ref = np.broadcast_to(
        np.stack([100 + years, 1000 + years], axis=-1)[:, :, None, None], (20, 2, 3, 3)
    ).astype(float)
    ref[:, :, 0, 0] = np.nan
    fc, model, ref = _rank_case(tmp_path, 5.0, ref_values=ref)
    out = _run_rank(downscale, tmp_path, fc, model, ref)
    assert np.isnan(out.values[..., 0, 0]).all()
    assert not np.isnan(out.values[..., 1:, 1:]).any()


def test_rank_smoothing_blends_cell_edges(tmp_path, downscale):
    import numpy as np

    # Left coarse column high, right column low: nearest transfer gives two values.
    values = np.zeros((1, 2, 2, 2))
    values[..., 0] = 18.0
    fc, model, ref = _rank_case(tmp_path, values)
    sharp = _run_rank(downscale, tmp_path, fc, model, ref).isel(number=0, step=0).values
    assert set(np.unique(sharp)) == {100.0, 118.0}
    smooth = _run_rank(downscale, tmp_path, fc, model, ref, "--rank-sigma", "1")
    smooth = smooth.isel(number=0, step=0).values
    assert len(np.unique(smooth)) > 2
    assert smooth.min() >= 100.0 and smooth.max() <= 118.0
    # Every value is still one of the reference years.
    assert set(np.unique(smooth)) <= set(100.0 + np.arange(20))


def test_rank_missing_step_in_reference_errors(tmp_path, downscale, capsys):
    fc, model, ref = _rank_case(tmp_path, 5.0, ref_steps=1)
    with pytest.raises(SystemExit) as exc:
        _run_rank(downscale, tmp_path, fc, model, ref)
    assert exc.value.code == 2
    assert "--qq-reference lacks step" in capsys.readouterr().err


def test_rank_requires_model(tmp_path, downscale, capsys):
    fc, _, ref = _rank_case(tmp_path, 5.0)
    with pytest.raises(SystemExit) as exc:
        run_skill(
            downscale, "-i", str(fc), "-o", str(tmp_path / "o.zarr"),             "-f", "2", "--qq-reference", str(ref),
        )  # fmt: skip
    assert exc.value.code == 2
    assert "--qq-model" in capsys.readouterr().err


def _with_coverage(tmp_path, fc_path, coverage):
    fc = xr.open_zarr(fc_path, consolidated=True).load()
    fc = fc.assign_coords(aggregation_coverage=("step", coverage))
    return write_zarr(fc, tmp_path / "fc_cov.zarr")


def test_rank_drops_incomplete_windows(tmp_path, downscale, capsys):
    fc, model, ref = _rank_case(tmp_path, 5.0)
    fc = _with_coverage(tmp_path, fc, [1.0, 4 / 7])
    out = _run_rank(downscale, tmp_path, fc, model, ref)
    assert out.sizes["step"] == 1
    assert "Dropped incomplete windows at step 1 days" in capsys.readouterr().err


def test_rank_keep_partial_keeps_incomplete_windows(tmp_path, downscale):
    fc, model, ref = _rank_case(tmp_path, 5.0)
    fc = _with_coverage(tmp_path, fc, [1.0, 4 / 7])
    out = _run_rank(downscale, tmp_path, fc, model, ref, "--keep-partial")
    assert out.sizes["step"] == 2


def test_rank_all_windows_incomplete_errors(tmp_path, downscale, capsys):
    fc, model, ref = _rank_case(tmp_path, 5.0)
    fc = _with_coverage(tmp_path, fc, [0.5, 0.5])
    with pytest.raises(SystemExit) as exc:
        _run_rank(downscale, tmp_path, fc, model, ref)
    assert exc.value.code == 2
    assert "--keep-partial" in capsys.readouterr().err


def test_coarser_reference_grid_errors(tmp_path, downscale):
    fc, model, ref = _rank_case(tmp_path, 5.0)
    coarse = xr.open_zarr(ref, consolidated=True).isel(latitude=[0, 2], longitude=[0, 2]).load()
    coarse = coarse.assign_coords(latitude=[0.0, 2.0], longitude=[10.0, 12.0])
    coarse_path = write_zarr(coarse, tmp_path / "coarse.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            downscale, "-i", str(fc), "-o", str(tmp_path / "o.zarr"),
            "--reference-grid", str(coarse_path), "--qq-model", str(model),
            "--qq-reference", str(coarse_path),
        )  # fmt: skip
    assert exc.value.code == 2
