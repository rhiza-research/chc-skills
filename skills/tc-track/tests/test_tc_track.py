"""Tests for tc-track on synthetic cyclones."""

from __future__ import annotations

import os

import cftime
import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_tc_dataset, run_skill, storm_path, write_zarr


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-track", "tc_track")


def _track(mod, tmp_path, ds, *args, name="in"):
    inp = write_zarr(ds, tmp_path / f"{name}.zarr")
    out = tmp_path / f"{name}_tracks.zarr"
    run_skill(mod.tc_track, "-i", str(inp), "-o", str(out), *args)
    return xr.open_zarr(out).load()


def _assert_follows(out, n_time, dt_hours, res, k=0, path=None):
    """Track k follows the synthetic storm path within one grid cell."""
    lat, lon = path if path is not None else storm_path(n_time, dt_hours)
    got_lat = out["latitude"].values[k]
    got_lon = out["longitude"].values[k]
    n = int(np.isfinite(got_lat).sum())
    assert n == n_time
    assert np.nanmax(np.abs(got_lat[:n] - lat)) <= res + 1e-3
    assert np.nanmax(np.abs(((got_lon[:n] - lon + 180) % 360) - 180)) <= res + 1e-3


def test_recovers_synthetic_track(mod, tmp_path):
    out = _track(mod, tmp_path, make_tc_dataset())
    assert out.sizes["track"] == 1
    _assert_follows(out, 17, 6, 0.5)
    assert out.attrs["featureType"] == "trajectory"
    assert out.attrs["tc_warm_core"] == 1
    assert out["msl"].attrs["units"] == "Pa"
    assert np.nanmin(out["msl"].values) < 99000
    assert np.nanmax(out["wind_max"].values) > 20
    assert out["ts_strength"].values[0, 0] == 1
    cat = out["category"]
    assert cat.attrs["flag_meanings"].split()[0] == "tropical_depression"
    expected = mod.wind_category(out["wind_max"].values)
    np.testing.assert_array_equal(cat.values, expected)


def test_wind_category_edges(mod):
    kt = np.array([0.0, 33.9, 34.0, 63.9, 64.0, 82.9, 83.0, 95.9, 96.0, 150.0])
    got = mod.wind_category(np.append(kt / mod.KNOTS_PER_MS, np.nan))
    np.testing.assert_array_equal(got[:-1], [0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
    assert np.isnan(got[-1])


def test_open_trough_is_not_a_cyclone(mod, tmp_path):
    ds = make_tc_dataset(depth=0.0)
    lat = ds["latitude"]
    ds["msl"] = ds["msl"] - 2000.0 * np.exp(
        -((lat + 12.0) ** 2) / 8.0
    )  # zonal trough, no closed low
    out = _track(mod, tmp_path, ds)
    assert out.sizes["track"] == 0


def test_cold_core_rejected_unless_skipped(mod, tmp_path):
    ds = make_tc_dataset(warm=-12.0)
    assert _track(mod, tmp_path, ds, name="cold").sizes["track"] == 0
    out = _track(mod, tmp_path, ds, "--no-warm-core", name="nowc")
    assert out.sizes["track"] == 1
    assert out.attrs["tc_warm_core"] == 0


def test_gap_is_bridged(mod, tmp_path):
    ds = make_tc_dataset()
    ds["msl"][8] = 101300.0  # the low vanishes at one time
    out = _track(mod, tmp_path, ds)
    assert out.sizes["track"] == 1
    assert int(np.isfinite(out["latitude"].values[0]).sum()) == 16


def test_too_short_track_dropped(mod, tmp_path):
    out = _track(mod, tmp_path, make_tc_dataset(n_time=8))  # 42 h < 54 h
    assert out.sizes["track"] == 0


def test_ensemble_members_tracked_separately(mod, tmp_path):
    out = _track(mod, tmp_path, make_tc_dataset(members=3))
    assert out.sizes["track"] == 3
    assert sorted(out["number"].values.tolist()) == [0, 1, 2]
    assert list(out.attrs["ensemble_members"]) == [0, 1, 2]
    assert all(t.startswith(("m00", "m01", "m02")) for t in out["track"].values)


def test_forecast_valid_times(mod, tmp_path):
    out = _track(mod, tmp_path, make_tc_dataset(forecast=True))
    times = out["time"].values[0]
    assert times[0] == np.datetime64("2026-02-01T00")
    assert times[16] == np.datetime64("2026-02-05T00")
    assert out.attrs["forecast_reference_time"].startswith("2026-02-01")


def test_dateline_crossing_bbox(mod, tmp_path):
    path = storm_path(17, 6, start=(-15.0, 184.0))
    ds = make_tc_dataset(lats=(-35.0, 5.0), lons=(160.0, 205.0), path=path)
    out = _track(mod, tmp_path, ds)
    assert out.sizes["track"] == 1
    lon = out["longitude"].values[0]
    assert np.nanmax(lon) > 170 and np.nanmin(lon) < -170  # wrapped to [-180, 180)
    steps = np.abs(((np.diff(lon) + 180) % 360) - 180)
    assert np.nanmax(steps) < 1.5


def test_global_grid_wraps(mod, tmp_path):
    path = storm_path(17, 6, start=(-15.0, 4.0))
    ds = make_tc_dataset(res=1.0, lats=(-40.0, 10.0), lons=(0.0, 359.0), path=path)
    out = _track(mod, tmp_path, ds)
    assert out.sizes["track"] == 1
    _assert_follows(out, 17, 6, 1.0, path=path)


@pytest.mark.parametrize(
    ("res", "dt", "n_time"),
    [(0.25, 6, 17), (1.0, 6, 17), (1.5, 6, 17), (0.5, 1, 97), (0.5, 24, 5), (1.5, 24, 5)],
)
def test_grid_and_time_step_independent(mod, tmp_path, res, dt, n_time):
    ds = make_tc_dataset(res=res, dt_hours=dt, n_time=n_time)
    out = _track(mod, tmp_path, ds)
    assert out.sizes["track"] == 1
    _assert_follows(out, n_time, dt, res)


def test_units_and_names_from_other_sources(mod, tmp_path):
    ds = make_tc_dataset()
    # ERA5-style: hPa pressure, geopotential in m2 s-2, long names, descending latitude.
    era = xr.Dataset(
        {
            "mean_sea_level_pressure": (ds["msl"] / 100).assign_attrs(units="hPa"),
            "10m_u_component_of_wind": ds["u10"],
            "10m_v_component_of_wind": ds["v10"],
            "geopotential": (ds["gh"] * 9.80665).assign_attrs(units="m2 s-2"),
        }
    ).sortby("latitude", ascending=False)
    out = _track(mod, tmp_path, era, name="era")
    assert out.sizes["track"] == 1
    _assert_follows(out, 17, 6, 0.5)
    assert out.attrs["tc_warm_core"] == 1


def test_cmip6_names_noleap_and_0_360(mod, tmp_path):
    ds = make_tc_dataset(lons=(35.0, 75.0))
    times = [
        cftime.DatetimeNoLeap(2050, 2, 1) + np.timedelta64(6 * k, "h").item() for k in range(17)
    ]
    plev = xr.DataArray(ds["level"].values * 100.0, dims="level", attrs={"units": "Pa"})
    cmip = xr.Dataset(
        {
            "psl": ds["msl"],
            "uas": ds["u10"],
            "vas": ds["v10"],
            "zg": ds["gh"].rename(level="plev").assign_coords(plev=plev.values),
        }
    ).assign_coords(time=times)
    cmip["plev"].attrs["units"] = "Pa"
    cmip["time"].attrs.update(standard_name="time")
    out = _track(mod, tmp_path, cmip, name="cmip")
    assert out.sizes["track"] == 1
    assert isinstance(out["time"].values[0, 0], cftime.DatetimeNoLeap)
    assert out.attrs["tc_warm_core"] == 1


def test_split_inputs_and_850_hpa_wind(mod, tmp_path):
    ds = make_tc_dataset()
    a = write_zarr(ds[["msl"]], tmp_path / "msl.zarr")
    b = write_zarr(
        xr.Dataset(
            {
                "u850": ds["u10"] * 1.25,
                "v850": ds["v10"] * 1.25,
                "z500": ds["gh"].sel(level=500, drop=True),
                "z300": ds["gh"].sel(level=300, drop=True),
            }
        ),
        tmp_path / "upper.zarr",
    )
    out_path = tmp_path / "split_tracks.zarr"
    run_skill(mod.tc_track, "-i", str(a), "-i", str(b), "-o", str(out_path))
    out = xr.open_zarr(out_path)
    assert out.sizes["track"] == 1
    assert out.attrs["tc_wind_level"] == "850hPa"
    assert out.attrs["tc_warm_core_layer"] == "300-500 hPa"


def test_override_flag(mod, tmp_path):
    out = _track(mod, tmp_path, make_tc_dataset(), "--wind-min", "40")
    assert out.sizes["track"] == 0
    assert out.attrs["tc_wind_min"] == 40


def test_no_pressure_exits_2(mod, tmp_path):
    inp = write_zarr(make_tc_dataset()[["u10", "v10"]], tmp_path / "w.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.tc_track, "-i", str(inp), "-o", str(tmp_path / "o.zarr"))
    assert exc.value.code == 2


def test_auto_criteria_table(mod):
    c, _ = mod.resolve_criteria("auto", 0.25, 6.0)
    assert c["psl_dist"] == 5.5 and c["range"] == 8.0 and c["min_time"] == 54.0
    assert c["wind_min"] == pytest.approx(10.0, abs=0.01)
    c, _ = mod.resolve_criteria("auto", 0.25, 1.0)
    assert c["range"] == 2.0
    c, _ = mod.resolve_criteria("auto", 1.5, 24.0)
    assert c["range"] == 12.5 and c["min_time"] == 48.0 and c["wind_min"] < 9
    c, _ = mod.resolve_criteria("auto", 2.5, 6.0)
    assert c["psl_dist"] == 7.5 and c["merge_dist"] == 6.0
    c, notes = mod.resolve_criteria("auto", 1.5, 24.0, mean_psl=True)
    assert c["psl_delta"] == 100.0 and notes
    c, notes = mod.resolve_criteria("zu2017", 1.0, 6.0, wind_level="850hPa")
    assert c["wind_min"] == 12.5
    c, notes = mod.resolve_criteria("auto", 0.25, 6.0, have_warm_core=False)
    assert c["warm_core"] is False and notes


def test_step_over_a_day_refused(mod):
    with pytest.raises(Exception, match="24 h"):
        mod.resolve_criteria("auto", 1.0, 48.0)


# IBTrACS v04r01 fixes for Cyclone Idai (2019), 00 UTC: time, lat, lon.
IDAI_FIXES = (
    ("2019-03-05", -17.0, 37.2),
    ("2019-03-07", -15.2, 35.9),
    ("2019-03-09", -16.8, 39.1),
    ("2019-03-10", -17.2, 42.4),
    ("2019-03-11", -17.3, 43.1),
    ("2019-03-12", -18.0, 42.2),
    ("2019-03-13", -19.2, 40.1),
    ("2019-03-14", -19.7, 37.9),
    ("2019-03-15", -19.7, 34.8),
)


@pytest.mark.skipif(
    not os.environ.get("TC_TRACK_ERA5_IDAI"),
    reason="set TC_TRACK_ERA5_IDAI to an ERA5 Zarr (msl, 10 m wind, surface geopotential; "
    "2019-03-04..15, 6-hourly, 2S-32S 28E-60E)",
)
def test_era5_idai_matches_ibtracs(mod, tmp_path):
    out_path = tmp_path / "idai.zarr"
    run_skill(mod.tc_track, "-i", os.environ["TC_TRACK_ERA5_IDAI"], "-o", str(out_path))
    out = xr.open_zarr(out_path).load()
    errors = []
    for time, lat, lon in IDAI_FIXES:
        hit = out["time"].values == np.datetime64(time, "ns")
        assert hit.any(), f"no track at {time}"
        k, j = (int(i[0]) for i in np.nonzero(hit))
        p1, p2 = np.radians(out["latitude"].values[k, j]), np.radians(lat)
        dl = np.radians(out["longitude"].values[k, j] - lon)
        cos = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dl)
        errors.append(6371.0 * np.arccos(np.clip(cos, -1, 1)))
    assert np.mean(errors) < 150.0
