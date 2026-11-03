"""Tests for grid-stations-metpy: station observations to a lat/lon grid."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_gridded, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("grid-stations-metpy", "grid_stations_metpy")


def _stations(n=40, values=None, seed=1):
    """Stations scattered over 4S-4N, 34-42E, two days; ``values(lat, lon)`` sets the field."""
    rng = np.random.default_rng(seed)
    lat = rng.uniform(-4, 4, n)
    lon = rng.uniform(34, 42, n)
    times = np.array(["2026-04-01", "2026-04-02"], dtype="datetime64[ns]")
    field = values(lat, lon) if values else np.full(n, 5.0)
    data = np.stack([field, field + 1.0], axis=1)
    ds = xr.Dataset(
        {"t2m": (("point_id", "time"), data, {"units": "degree_Celsius"})},
        coords={
            "point_id": [f"S{i}" for i in range(n)],
            "time": times,
            "latitude": ("point_id", lat, {"standard_name": "latitude", "units": "degrees_north"}),
            "longitude": ("point_id", lon, {"standard_name": "longitude", "units": "degrees_east"}),
        },
    )
    ds["time"].attrs.update(standard_name="time", axis="T")
    return ds


def test_linear_recovers_a_plane(mod, tmp_path):
    ds = _stations(values=lambda lat, lon: 20 + 0.5 * (lon - 38) - 0.3 * lat)
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "grid.zarr"
    run_skill(
        mod.grid_stations,
        "-i",
        str(inp),
        "--method",
        "linear",
        "--bbox",
        "2/36/-2/40",
        "--resolution",
        "0.5",
        "-o",
        str(out),
    )
    grid = xr.open_zarr(out, consolidated=True)["t2m"]
    assert grid.dims == ("time", "latitude", "longitude")
    np.testing.assert_allclose(grid["latitude"].values, np.arange(-2, 2.01, 0.5))
    expect = (20 + 0.5 * (grid["longitude"] - 38) - 0.3 * grid["latitude"]).transpose(
        "latitude", "longitude"
    )
    # A local equidistant projection is not exactly linear in degrees: allow a little.
    np.testing.assert_allclose(grid.isel(time=0), expect, atol=0.05)
    np.testing.assert_allclose(grid.isel(time=1), expect + 1.0, atol=0.05)
    assert grid.attrs["units"] == "degree_Celsius"
    assert "linear analysis of 40 stations" in grid.attrs["comment"]


def test_barnes_keeps_a_constant_field(mod, tmp_path):
    inp = write_zarr(_stations(), tmp_path / "st.zarr")
    out = tmp_path / "grid.zarr"
    run_skill(
        mod.grid_stations,
        "-i",
        str(inp),
        "--resolution",
        "1",
        "--search-radius",
        "300",
        "-o",
        str(out),
    )
    grid = xr.open_zarr(out, consolidated=True)["t2m"].isel(time=0)
    vals = grid.values[np.isfinite(grid.values)]
    assert vals.size > 0
    np.testing.assert_allclose(vals, 5.0, atol=1e-6)


def test_like_grid_and_max_distance(mod, tmp_path):
    lats = tuple(float(x) for x in np.arange(-6, 6.1, 1.0))
    lons = tuple(float(x) for x in np.arange(30, 46.1, 1.0))
    ref = write_zarr(make_gridded(lats=lats, lons=lons), tmp_path / "ref.zarr")
    inp = write_zarr(_stations(), tmp_path / "st.zarr")
    out = tmp_path / "grid.zarr"
    run_skill(
        mod.grid_stations,
        "-i",
        str(inp),
        "--like",
        str(ref),
        "--method",
        "nearest",
        "--max-distance",
        "150",
        "-o",
        str(out),
    )
    grid = xr.open_zarr(out, consolidated=True)["t2m"].isel(time=0)
    np.testing.assert_allclose(grid["longitude"].values, lons)
    # Stations span 34-42E: cells at 30E and 46E are > 150 km from all of them.
    assert np.isnan(grid.sel(longitude=30.0)).all()
    assert np.isnan(grid.sel(longitude=46.0)).all()
    assert np.isfinite(grid.sel(latitude=0.0, longitude=38.0))


def test_missing_reports_are_skipped(mod, tmp_path):
    ds = _stations()
    ds["t2m"][0, 0] = np.nan
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "grid.zarr"
    run_skill(
        mod.grid_stations,
        "-i",
        str(inp),
        "--method",
        "linear",
        "--bbox",
        "2/36/-2/40",
        "--resolution",
        "1",
        "-o",
        str(out),
    )
    grid = xr.open_zarr(out, consolidated=True)["t2m"].isel(time=0)
    np.testing.assert_allclose(grid.values, 5.0, atol=1e-6)


def test_needs_a_grid(mod, tmp_path, capsys):
    inp = write_zarr(_stations(), tmp_path / "st.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.grid_stations, "-i", str(inp), "-o", str(tmp_path / "g.zarr"))
    assert excinfo.value.code == 2
    assert "--resolution" in capsys.readouterr().err


def test_station_id_dim(mod, tmp_path):
    ds = _stations().rename(point_id="station_id")
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "grid.zarr"
    run_skill(mod.grid_stations, "-i", str(inp), "--resolution", "1", "-o", str(out))
    assert "t2m" in xr.open_zarr(out, consolidated=True)
