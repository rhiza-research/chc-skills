"""Tests for arithmetic."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("arithmetic", "arithmetic")


def _pair(tmp_path, a=6.0, b=2.0, units_b="mm"):
    da = make_forecast(n_step=2, fill=a)
    db = make_forecast(n_step=2, fill=b)
    db["tp"].attrs["units"] = units_b
    return write_zarr(da, tmp_path / "a.zarr"), write_zarr(db, tmp_path / "b.zarr")


def _run(mod, out, *args):
    run_skill(mod.arithmetic, "-o", str(out), *args)
    return xr.open_zarr(out)


@pytest.mark.parametrize(
    "op,value,units",
    [("add", 8.0, "mm"), ("subtract", 4.0, "mm"), ("multiply", 12.0, "mm2"), ("divide", 3.0, "1")],
)
def test_two_inputs(mod, tmp_path, op, value, units):
    a, b = _pair(tmp_path)
    res = _run(mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", op)
    np.testing.assert_allclose(res["tp"].values, value)
    assert res["tp"].attrs["units"] == units


def test_ratio_attrs_and_percent(mod, tmp_path):
    a, b = _pair(tmp_path, a=3.0, b=4.0)
    res = _run(
        mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", "divide", "--units", "%"
    )
    np.testing.assert_allclose(res["tp"].values, 75.0)
    assert res["tp"].attrs["units"] == "%"
    assert res["tp"].attrs["long_name"] == "Total precipitation ratio"
    assert "standard_name" not in res["tp"].attrs


def test_mixed_units_are_converted(mod, tmp_path):
    a, b = _pair(tmp_path, a=6.0, b=0.002, units_b="m")
    res = _run(mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", "add")
    np.testing.assert_allclose(res["tp"].values, 8.0)


def test_constant_rate_times_duration(mod, tmp_path):
    ds = make_gridded(n_time=2, fill=2.0)  # mm day-1
    inp = write_zarr(ds, tmp_path / "r.zarr")
    res = _run(mod, tmp_path / "o.zarr", "-i", str(inp), "--op", "multiply", "--constant", "7 day")
    np.testing.assert_allclose(res["precip"].values, 14.0)
    assert res["precip"].attrs["units"] == "mm"
    scaled = _run(mod, tmp_path / "s.zarr", "-i", str(inp), "--op", "multiply", "--constant", "0.5")
    assert scaled["precip"].attrs["long_name"].endswith("scaled")
    assert scaled["precip"].attrs["standard_name"] == "lwe_precipitation_rate"


def test_divide_by_zero_is_nan(mod, tmp_path):
    a, b = _pair(tmp_path, b=0.0)
    res = _run(mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", "divide")
    assert np.isnan(res["tp"].values).all()


def test_temperature_difference_is_delta(mod, tmp_path):
    ta = make_gridded(n_time=2, name="t2m", fill=25.0)
    tb = make_gridded(n_time=2, name="t2m", fill=20.0)
    for ds in (ta, tb):
        ds["t2m"].attrs = {"units": "degree_Celsius", "standard_name": "air_temperature"}
    a, b = write_zarr(ta, tmp_path / "ta.zarr"), write_zarr(tb, tmp_path / "tb.zarr")
    res = _run(mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", "subtract")
    np.testing.assert_allclose(res["t2m"].values, 5.0)
    assert "delta" in res["t2m"].attrs["units"].lower() or res["t2m"].attrs["units"] == "K"
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path / "x.zarr", "-i", str(a), "-i", str(b), "--op", "add")
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "extra",
    [
        ("--op", "add"),  # one input, no constant
        ("--op", "add", "--constant", "1 mm/day"),  # mm + mm/day
        ("--op", "divide", "--constant", "0"),
        ("--op", "add", "--constant", "banana"),
    ],
)
def test_usage_errors(mod, tmp_path, extra):
    a, _ = _pair(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path / "o.zarr", "-i", str(a), *extra)
    assert exc.value.code == 2


def test_broadcast_guard(mod, tmp_path):
    a = write_zarr(make_gridded(n_time=2), tmp_path / "a.zarr")
    clim = make_gridded(n_time=2).rename(time="dayofyear").assign_coords(dayofyear=[1, 2])
    b = write_zarr(clim, tmp_path / "b.zarr")
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path / "o.zarr", "-i", str(a), "-i", str(b), "--op", "divide")
    assert exc.value.code == 2
