"""Tests for plot-stations-metpy on METAR-like and temperature-only station data."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill, write_zarr

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def mod():
    return load_skill("plot-stations-metpy", "plot_stations_metpy")


def _stations(full=True, n=6):
    lat = np.linspace(-4, 3, n)
    lon = np.linspace(34, 41, n)
    times = np.array(["2026-04-16T11:00", "2026-04-16T12:00"], dtype="datetime64[ns]")
    shape = (n, 2)
    data = {
        "air_temperature": (
            ("station_id", "time"),
            np.full(shape, 25.0),
            {"units": "degree_Celsius", "standard_name": "air_temperature"},
        )
    }
    if full:
        data |= {
            "dew_point_temperature": (
                ("station_id", "time"),
                np.full(shape, 18.0),
                {"units": "degree_Celsius", "standard_name": "dew_point_temperature"},
            ),
            "wind_from_direction": (
                ("station_id", "time"),
                np.full(shape, 90.0),
                {"units": "degree", "standard_name": "wind_from_direction"},
            ),
            "wind_speed": (
                ("station_id", "time"),
                np.tile([0.0, 12.0], (n, 1)),
                {"units": "knot", "standard_name": "wind_speed"},
            ),
            "qnh": (("station_id", "time"), np.full(shape, 1012.3), {"units": "hPa"}),
            "cloud_cover": (("station_id", "time"), np.full(shape, 4.0), {"units": "1"}),
            "present_weather_code": (("station_id", "time"), np.full(shape, 61.0), {"units": "1"}),
        }
    ds = xr.Dataset(
        data,
        coords={
            "station_id": [f"S{i}" for i in range(n)],
            "time": times,
            "latitude": (
                "station_id",
                lat,
                {"standard_name": "latitude", "units": "degrees_north"},
            ),
            "longitude": (
                "station_id",
                lon,
                {"standard_name": "longitude", "units": "degrees_east"},
            ),
        },
    )
    ds["time"].attrs.update(standard_name="time", axis="T")
    ds["station_id"].attrs.update(cf_role="timeseries_id")
    return ds


def test_full_station_model(mod, tmp_path, capsys):
    inp = write_zarr(_stations(), tmp_path / "st.zarr")
    out = tmp_path / "map.png"
    run_skill(mod.plot_stations, "-i", str(inp), "--labels", "-o", str(out))
    assert out.read_bytes()[:8] == PNG
    # Default time: the latest with reports.
    assert "6 stations at 2026-04-16 12:00 UTC" in capsys.readouterr().err


def test_temperature_only_and_thinning(mod, tmp_path, capsys):
    ds = _stations(full=False, n=6)
    ds["latitude"].values[:] = 0.0
    ds["longitude"].values[:] = 36.0 + np.arange(6) * 0.05  # ~5.5 km apart
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "map.png"
    run_skill(
        mod.plot_stations,
        "-i",
        str(inp),
        "--time",
        "2026-04-16T11:00",
        "--min-spacing",
        "12",
        "-o",
        str(out),
    )
    assert out.read_bytes()[:8] == PNG
    err = capsys.readouterr().err
    # 5.5 km apart, 12 km minimum: the 1st and 4th stay.
    assert "2 stations at 2026-04-16 11:00 UTC" in err


def test_member_dim_must_be_reduced(mod, tmp_path, capsys):
    ds = _stations().expand_dims(number=[0, 1])
    inp = write_zarr(ds, tmp_path / "st.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.plot_stations, "-i", str(inp), "-o", str(tmp_path / "m.png"))
    assert excinfo.value.code == 2
    assert "reduce number first" in capsys.readouterr().err
