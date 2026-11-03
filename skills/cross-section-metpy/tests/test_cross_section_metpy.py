"""Tests for cross-section-metpy on gridded pressure-level fields."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_gridded, run_skill, write_zarr

LEVELS = np.array([1000, 850, 700, 500.0])


@pytest.fixture
def mod():
    return load_skill("cross-section-metpy", "cross_section_metpy")


def _field(like, values, **attrs):
    out = xr.zeros_like(like) + values
    out.attrs = attrs
    return out


def _grid():
    lats = tuple(float(x) for x in np.arange(-4.0, 4.1, 1.0))
    lons = tuple(float(x) for x in np.arange(30.0, 42.1, 1.0))
    ds = make_gridded(n_time=2, lats=lats, lons=lons, name="t", fill=0.0)
    ds = ds.expand_dims(level=LEVELS).transpose("time", "level", ...).copy()
    ds["level"].attrs = {"units": "hPa", "standard_name": "air_pressure"}
    # Temperature rising eastward, so the section can be checked point by point.
    ds["t"] = _field(ds["t"], 280.0 + (ds["longitude"] - 30.0), units="K")
    ds["u"] = _field(ds["t"], 10.0, units="m s-1", standard_name="eastward_wind")
    ds["v"] = _field(ds["t"], 0.0, units="m s-1", standard_name="northward_wind")
    sp = 1010.0 - 30.0 * (ds["longitude"] > 35)  # high ground east of 35E
    ds["sp"] = _field(ds["t"].isel(level=0, drop=True), sp, units="hPa")
    return ds


def test_section_values_distance_and_winds(mod, tmp_path):
    inp = write_zarr(_grid(), tmp_path / "grid.zarr")
    out = tmp_path / "xs.zarr"
    run_skill(
        mod.cross_section,
        "-i",
        str(inp),
        "--start",
        "0,31",
        "--end",
        "0,41",
        "--steps",
        "11",
        "--wind-components",
        "-o",
        str(out),
    )
    xs = xr.open_zarr(out, consolidated=True)
    assert xs["t"].dims == ("time", "level", "index")
    lon = xs["longitude"].values
    np.testing.assert_allclose(lon, np.linspace(31, 41, 11), atol=1e-6)
    np.testing.assert_allclose(
        xs["t"].isel(time=0).sel(level=850).values, 280.0 + (lon - 30.0), atol=1e-6
    )
    # 10 degrees of longitude on the equator.
    assert float(xs["distance"][-1]) == pytest.approx(1112, rel=0.01)
    assert xs["distance"].attrs["units"] == "km"
    np.testing.assert_allclose(xs["wind_tangential"].sel(level=850).values, 10.0, atol=1e-6)
    np.testing.assert_allclose(xs["wind_normal"].sel(level=850).values, 0.0, atol=1e-6)
    # 1000 hPa is under the 980 hPa ground east of 35E, above the ground west of it.
    t1000 = xs["t"].isel(time=0).sel(level=1000)
    assert np.isnan(t1000.where(xs["longitude"] > 35.5, drop=True)).all()
    assert np.isfinite(t1000.where(xs["longitude"] < 34.5, drop=True)).all()
    assert xs.attrs["metpy_version"]


def test_point_outside_grid_exit_2(mod, tmp_path, capsys):
    inp = write_zarr(_grid(), tmp_path / "grid.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.cross_section,
            "-i",
            str(inp),
            "--start",
            "0,31",
            "--end",
            "10,45",
            "-o",
            str(tmp_path / "x.zarr"),
        )
    assert excinfo.value.code == 2
    assert "outside the grid" in capsys.readouterr().err


def test_wind_components_need_u_and_v(mod, tmp_path, capsys):
    inp = write_zarr(_grid()[["t"]], tmp_path / "grid.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.cross_section,
            "-i",
            str(inp),
            "--start",
            "0,31",
            "--end",
            "0,41",
            "--wind-components",
            "-o",
            str(tmp_path / "x.zarr"),
        )
    assert excinfo.value.code == 2
    assert "needs exactly one u and one v" in capsys.readouterr().err
