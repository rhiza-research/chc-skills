"""Tests for vertical-interpolate-metpy: pressure, height and isentropic levels."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_zarr

LEVELS = np.array([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200.0])
T_C = np.array([28, 23, 19, 10, 3, -5, -16, -31, -41, -53.0])
Z_M = np.array([110, 760, 1460, 3010, 4200, 5570, 7180, 9160, 10360, 11780.0])


@pytest.fixture
def mod():
    return load_skill("vertical-interpolate-metpy", "vertical_interpolate_metpy")


def _field(like, values, **attrs):
    """``like``'s shape filled with ``values``, carrying exactly ``attrs``."""
    out = xr.zeros_like(like) + values
    out.attrs = attrs
    return out


def _levels():
    ds = make_forecast(n_step=2, members=2, name="t", fill=0.0)
    ds = ds.expand_dims(level=LEVELS).transpose("number", "step", "level", ...).copy()
    ds["level"].attrs = {"units": "hPa", "standard_name": "air_pressure"}
    lev = {"dims": "level", "coords": {"level": LEVELS}}
    ds["t"] = _field(
        ds["t"], xr.DataArray(T_C + 273.15, **lev), units="K", standard_name="air_temperature"
    )
    ds["gh"] = _field(
        ds["t"], xr.DataArray(Z_M, **lev), units="m", standard_name="geopotential_height"
    )
    # u linear in log(p): log-p interpolation reproduces it exactly.
    ds["u"] = _field(ds["t"], xr.DataArray(10 * np.log(1000 / LEVELS), **lev), units="m s-1")
    return ds


def test_pressure_levels_log_p(mod, tmp_path):
    inp = write_zarr(_levels(), tmp_path / "lev.zarr")
    out = tmp_path / "p.zarr"
    run_skill(
        mod.vertical_interpolate, "-i", str(inp), "--pressure-levels", "775,650", "-o", str(out)
    )
    res = xr.open_zarr(out, consolidated=True)
    assert res["u"].dims == ("number", "step", "level", "latitude", "longitude")
    np.testing.assert_allclose(res["level"].values, [775, 650])
    np.testing.assert_allclose(
        res["u"].isel(number=0, step=0, latitude=0, longitude=0).values,
        10 * np.log(1000 / np.array([775, 650])),
        rtol=1e-6,
    )
    assert res.attrs["metpy_version"]


def test_height_levels_above_sea_and_ground(mod, tmp_path):
    ds = _levels()
    ds["sp"] = _field(ds["t"].isel(level=0, drop=True), 830.0, units="hPa")
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    asl = tmp_path / "asl.zarr"
    run_skill(
        mod.vertical_interpolate, "-i", str(inp), "--height-levels", "500,3010", "-o", str(asl)
    )
    res = xr.open_zarr(asl, consolidated=True)
    t = res["t"].isel(number=0, step=0, latitude=0, longitude=0).values
    # 500 m above sea level is under the 830 hPa ground; 3010 m is the 700 hPa level.
    assert np.isnan(t[0])
    assert t[1] == pytest.approx(10 + 273.15, abs=0.01)
    assert res["altitude"].attrs["standard_name"] == "altitude"
    assert "sp" in res  # surface fields pass through

    agl = tmp_path / "agl.zarr"
    run_skill(
        mod.vertical_interpolate,
        "-i",
        str(inp),
        "--height-levels",
        "1000,1500",
        "--above-ground",
        "-o",
        str(agl),
    )
    res = xr.open_zarr(agl, consolidated=True)
    assert res["height"].attrs["standard_name"] == "height"
    # Ground at 830 hPa, first level above it 700 hPa (~1370 m up): 1000 m has no data
    # below it, 1500 m lies between 700 and 600 hPa.
    assert np.isnan(res["t"].sel(height=1000).values).all()
    assert np.isfinite(res["t"].sel(height=1500).values).all()


def test_isentropic_levels(mod, tmp_path):
    inp = write_zarr(_levels(), tmp_path / "lev.zarr")
    out = tmp_path / "th.zarr"
    run_skill(
        mod.vertical_interpolate,
        "-i",
        str(inp),
        "--isentropic-levels",
        "310,320,330",
        "-o",
        str(out),
    )
    res = xr.open_zarr(out, consolidated=True)
    assert {"pressure", "temperature", "u", "gh"} <= set(res.data_vars)
    p = res["pressure"].isel(number=0, step=0, latitude=0, longitude=0).values
    assert np.all(np.diff(p) < 0)  # warmer isentropes sit higher
    theta = (
        res["temperature"].isel(number=0, step=0, latitude=0, longitude=0).values
        * (1000 / p) ** 0.2857
    )
    np.testing.assert_allclose(theta, [310, 320, 330], atol=0.5)


def test_exactly_one_mode(mod, tmp_path, capsys):
    inp = write_zarr(_levels(), tmp_path / "lev.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.vertical_interpolate, "-i", str(inp), "-o", str(tmp_path / "o.zarr"))
    assert excinfo.value.code == 2
    assert "exactly one of" in capsys.readouterr().err


def test_isentropes_below_ground_are_blank(mod, tmp_path):
    ds = _levels()
    ds["sp"] = _field(ds["t"].isel(level=0, drop=True), 800.0, units="hPa")
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    out = tmp_path / "th.zarr"
    run_skill(
        mod.vertical_interpolate, "-i", str(inp), "--isentropic-levels", "305,310", "-o", str(out)
    )
    p = xr.open_zarr(out, consolidated=True)["pressure"]
    # 305 K sits near 880 hPa, under an 800 hPa surface; 310 K near 770 hPa, above it.
    assert np.isnan(p.sel(isentropic_level=305)).all()
    assert (p.sel(isentropic_level=310) < 800).all()
