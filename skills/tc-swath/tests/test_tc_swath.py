"""Tests for tc-swath on synthetic fields and tracks."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import TRACK_T0, load_skill, make_tracks, run_skill, write_zarr

LAT = np.arange(-25.0, -4.9, 0.5)
LON = np.arange(50.0, 70.1, 0.5)


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-swath", "tc_swath")


def _forecast(value, name="tp", units="mm day-1", members=2, hours=48, dt=6):
    steps = np.arange(0, hours + 1, dt) * np.timedelta64(1, "h")
    shape = (members, steps.size, LAT.size, LON.size)
    data = np.broadcast_to(np.asarray(value, float), shape).copy()
    return xr.Dataset(
        {name: (("number", "step", "latitude", "longitude"), data, {"units": units})},
        coords={
            "number": np.arange(members),
            "step": steps.astype("timedelta64[ns]"),
            "time": TRACK_T0,
            "latitude": ("latitude", LAT, {"units": "degrees_north"}),
            "longitude": ("longitude", LON, {"units": "degrees_east"}),
        },
    )


def _stationary(n=9, number=0):
    return make_tracks(
        [("m000-001", number, 0, [(-15.0, 60.0)] * n, 99000.0, 20.0)], members=[0, 1]
    )


def _run(mod, tmp_path, tracks, field, *args):
    t = write_zarr(tracks, tmp_path / "tracks.zarr")
    f = write_zarr(field, tmp_path / "field.zarr")
    out = tmp_path / "swath.zarr"
    run_skill(mod.tc_swath, "--tracks", str(t), "-i", str(f), "-o", str(out), *args)
    return xr.open_zarr(out).load()


def test_rain_total_inside_the_radius_only(mod, tmp_path):
    out = _run(mod, tmp_path, _stationary(), _forecast(24.0))
    rain = out["tc_rain"]
    assert rain.attrs["units"] == "mm"
    # 8 six-hour intervals of 24 mm/day while the storm sits at (-15, 60): 48 mm.
    assert rain.sel(number=0, latitude=-15.0, longitude=60.0).item() == pytest.approx(48.0)
    assert rain.sel(number=0, latitude=-15.0, longitude=64.5).item() == pytest.approx(
        48.0
    )  # ~480 km
    assert rain.sel(number=0, latitude=-15.0, longitude=65.0).item() == 0.0  # ~537 km
    assert float(rain.sel(number=1).max()) == 0.0  # no storm in member 1
    np.testing.assert_allclose(out["storm_hours"].values, [48.0, 0.0])
    assert out.attrs["tc_swath_radius_km"] == 500.0


def test_rain_counts_only_while_the_storm_exists(mod, tmp_path):
    # Track only for the first 24 h (5 fixes): four intervals, 24 mm.
    out = _run(mod, tmp_path, _stationary(n=5), _forecast(24.0), "--radius", "200")
    assert out["tc_rain"].sel(number=0, latitude=-15.0, longitude=60.0).item() == pytest.approx(
        24.0
    )
    assert out["tc_rain"].sel(number=0, latitude=-15.0, longitude=62.5).item() == 0.0  # ~268 km


def test_daily_observations_use_the_starting_interval(mod, tmp_path):
    times = TRACK_T0 + np.arange(3) * np.timedelta64(24, "h")
    obs = xr.Dataset(
        {
            "precip": (
                ("time", "latitude", "longitude"),
                np.full((3, LAT.size, LON.size), 10.0),
                {"units": "mm day-1"},
            )
        },
        coords={"time": times, "latitude": LAT, "longitude": LON},
    )
    best = make_tracks(
        [("SH012026", None, 0, [(-15.0, 60.0)] * 9, 97000.0, 40.0)], reference=True
    )  # 0-48 h: days 1 and 2 (each day's centre at +12 h, +36 h); day 3's centre is outside
    out = _run(mod, tmp_path, best, obs)
    assert out["tc_rain"].sel(latitude=-15.0, longitude=60.0).item() == pytest.approx(20.0)
    assert "number" not in out.dims


def test_wind_is_the_maximum_within_the_radius(mod, tmp_path):
    wind = _forecast(5.0, name="si10", units="m s-1")
    wind["si10"][0, 4, 20, 20] = 40.0  # (-15, 60) at +24 h
    wind["si10"][0, 4, 0, 0] = 60.0  # far corner
    out = _run(mod, tmp_path, _stationary(), wind, "--field", "wind")
    w = out["tc_wind_max"]
    assert w.sel(number=0, latitude=-15.0, longitude=60.0).item() == pytest.approx(40.0)
    assert w.sel(number=0, latitude=-25.0, longitude=50.0).item() == 0.0
    assert out.attrs["tc_swath_radius_km"] == 300.0


def test_members_need_numbered_tracks_and_rates(mod, tmp_path):
    best = make_tracks([("SH012026", None, 0, [(-15.0, 60.0)] * 9, 97000.0, 40.0)], reference=True)
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, best, _forecast(24.0))
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, _stationary(), _forecast(24.0, units="mm"))
    assert exc.value.code == 2


def test_per_step_keeps_the_time_axis_for_indicator(mod, tmp_path):
    out = _run(mod, tmp_path, _stationary(n=5), _forecast(24.0), "--per-step")
    rain = out["tc_rain"]
    assert rain.dims == ("number", "step", "latitude", "longitude")
    assert rain.attrs["units"] == "mm day-1"
    at = rain.sel(number=0, latitude=-15.0, longitude=60.0).values
    # Steps 1-4 (intervals ending at 6-24 h) have the storm; later steps are 0.
    np.testing.assert_allclose(at[1:5], 24.0)
    np.testing.assert_allclose(at[5:], 0.0)
    assert float(rain.sel(number=1).max()) == 0.0
    assert "time" in out.coords  # the init time is kept for step-based skills
