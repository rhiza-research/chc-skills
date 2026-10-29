"""Tests for accumulate."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("accumulate", "accumulate")


def _daily_rate_forecast(tmp_path, n_step=20, fill=2.0):
    ds = make_forecast(n_step=n_step, members=2, fill=fill)
    ds = ds.assign_coords(step=ds["step"] - np.timedelta64(1, "D"))  # leads 0..n-1 day
    ds["tp"].attrs.update(units="mm day-1", long_name="Total precipitation rate")
    return write_zarr(ds, tmp_path / "fc.zarr")


def _run(mod, inp, out, *args):
    run_skill(mod.accumulate, "-i", str(inp), "-o", str(out), *args)
    return xr.open_zarr(out)


def test_single_window_rate_to_total(mod, tmp_path):
    inp = _daily_rate_forecast(tmp_path)
    res = _run(mod, inp, tmp_path / "a.zarr", "--from", "4 day", "--to", "18 day")
    assert res.sizes["step"] == 1
    assert res["tp"].attrs["units"] == "mm"
    np.testing.assert_allclose(res["tp"].values, 28.0)
    assert res["tp"].attrs["aggregation_period"] == "14 day"
    assert res["tp"].attrs["cell_methods"].endswith("step: sum")
    assert res["step"].values[0] == np.timedelta64(4, "D")


def test_back_to_back_and_rolling(mod, tmp_path):
    inp = _daily_rate_forecast(tmp_path, n_step=21)
    weekly = _run(mod, inp, tmp_path / "w.zarr", "--length", "7 day")
    assert weekly.sizes["step"] == 3
    np.testing.assert_allclose(weekly["tp"].values, 14.0)
    rolling = _run(mod, inp, tmp_path / "r.zarr", "--length", "7 day", "--stride", "1 day")
    assert rolling.sizes["step"] == 15
    assert rolling["tp"].attrs["data_interval"] == "1 day"


def test_cumulative_is_running_total(mod, tmp_path):
    inp = _daily_rate_forecast(tmp_path, n_step=5)
    res = _run(mod, inp, tmp_path / "c.zarr", "--cumulative")
    vals = res["tp"].isel(number=0, latitude=0, longitude=0).values
    np.testing.assert_allclose(vals, [2, 4, 6, 8, 10])
    assert "step_bounds" in res.coords
    assert "aggregation_period" not in res["tp"].attrs


def test_amounts_are_summed(mod, tmp_path):
    ds = make_gridded(n_time=10, fill=3.0)
    ds["precip"].attrs.update(units="mm", standard_name="lwe_thickness_of_precipitation_amount")
    inp = write_zarr(ds, tmp_path / "obs.zarr")
    res = _run(mod, inp, tmp_path / "a.zarr", "--from", "2026-01-03", "--to", "2026-01-08")
    np.testing.assert_allclose(res["precip"].values, 15.0)
    assert str(res["time"].values[0])[:10] == "2026-01-03"


def test_coverage_nan(mod, tmp_path):
    ds = make_gridded(n_time=10, fill=1.0)
    ds["precip"].attrs["units"] = "mm"
    ds = ds.drop_sel(time=np.datetime64("2026-01-05"))
    ds["precip"].attrs["data_interval"] = "1 day"
    inp = write_zarr(ds, tmp_path / "obs.zarr")
    res = _run(mod, inp, tmp_path / "a.zarr", "--length", "5 day")
    assert np.isnan(res["precip"].isel(time=0)).all()
    np.testing.assert_allclose(res["precip"].isel(time=1), 5.0)
    np.testing.assert_allclose(res["aggregation_coverage"], [0.8, 1.0])
    part = _run(mod, inp, tmp_path / "p.zarr", "--length", "5 day", "--min-coverage", "0.8")
    np.testing.assert_allclose(part["precip"].isel(time=0), 4.0)


def test_missing_cell_value_is_nan(mod, tmp_path):
    ds = make_gridded(n_time=4, fill=1.0)
    ds["precip"].attrs["units"] = "mm"
    ds["precip"][1, 0, 0] = np.nan
    inp = write_zarr(ds, tmp_path / "obs.zarr")
    res = _run(mod, inp, tmp_path / "a.zarr", "--length", "4 day")
    assert np.isnan(res["precip"].isel(time=0, latitude=0, longitude=0))
    assert float(res["precip"].isel(time=0, latitude=1, longitude=0)) == 4.0


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--length", "7 day", "--cumulative"),
        ("--length", "7"),
        ("--length", "60 day"),
        ("--stride", "1 day", "--cumulative"),
    ],
)
def test_usage_errors(mod, tmp_path, args):
    inp = _daily_rate_forecast(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.accumulate, "-i", str(inp), "-o", str(tmp_path / "a.zarr"), *args)
    assert exc.value.code == 2


def test_temperature_refused(mod, tmp_path):
    ds = make_gridded(n_time=4, name="t2m")
    ds["t2m"].attrs = {"units": "degree_Celsius", "standard_name": "air_temperature"}
    inp = write_zarr(ds, tmp_path / "t.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.accumulate, "-i", str(inp), "-o", str(tmp_path / "a.zarr"), "--length", "2 day"
        )
    assert exc.value.code == 2
