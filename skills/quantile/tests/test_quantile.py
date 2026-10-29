"""Tests for quantile."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("quantile", "quantile")


def _members(tmp_path, n=11):
    ds = make_forecast(n_step=2, members=n)
    ds["tp"] = ds["tp"] * xr.DataArray(np.arange(n, dtype=float), dims="number")
    return write_zarr(ds, tmp_path / "fc.zarr")


def test_known_values(mod, tmp_path):
    inp = _members(tmp_path)
    out = tmp_path / "q.zarr"
    run_skill(
        mod.quantile, "-i", str(inp), "-o", str(out), "--dim", "number", "-q", "0.1,0.5", "-q", "1"
    )
    res = xr.open_zarr(out)
    assert "number" not in res.dims
    assert list(res["quantile"].values) == [0.1, 0.5, 1.0]
    np.testing.assert_allclose(res["tp"].isel(step=0, latitude=0, longitude=0), [1.0, 5.0, 10.0])
    assert res["tp"].attrs["units"] == "mm"
    assert res["tp"].attrs["long_name"] == "Quantiles of Total precipitation across number"
    assert res["tp"].attrs["quantile_method"] == "linear"


def test_pooled_dims_and_preset(mod, tmp_path):
    inp = _members(tmp_path, n=3)
    out = tmp_path / "q.zarr"
    run_skill(
        mod.quantile,
        *("-i", str(inp), "-o", str(out), "--dim", "number", "--dim", "step", "-q", "terciles"),
    )
    res = xr.open_zarr(out)
    assert set(res["tp"].dims) == {"quantile", "latitude", "longitude"}
    assert res.sizes["quantile"] == 2


def test_squeeze_and_nan(mod, tmp_path):
    ds = make_forecast(n_step=1, members=4)
    ds["tp"][:] = np.array([1.0, 2.0, np.nan, 3.0])[:, None, None, None]
    inp = write_zarr(ds, tmp_path / "fc.zarr")
    out = tmp_path / "q.zarr"
    run_skill(
        mod.quantile,
        *("-i", str(inp), "-o", str(out), "--dim", "number", "-q", "0.5", "--squeeze"),
    )
    res = xr.open_zarr(out)
    assert "quantile" not in res.dims
    assert float(res["tp"].isel(step=0, latitude=0, longitude=0)) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "args",
    [
        ("--dim", "nope", "-q", "0.5"),
        ("--dim", "number", "-q", "90"),
        ("--dim", "number", "-q", "tertiles"),
        ("--dim", "number", "-q", "0.1,0.9", "--squeeze"),
    ],
)
def test_usage_errors(mod, tmp_path, args):
    inp = _members(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.quantile, "-i", str(inp), "-o", str(tmp_path / "q.zarr"), *args)
    assert exc.value.code == 2
