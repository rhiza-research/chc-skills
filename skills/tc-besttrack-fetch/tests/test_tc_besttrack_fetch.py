"""Tests for tc-besttrack-fetch on local HURDAT2 / IBTrACS fixtures (no network)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import load_skill, run_skill

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-besttrack-fetch", "fetch")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A fresh cache holding the fixtures, so nothing is downloaded."""
    root = tmp_path / "cache"
    root.mkdir()
    for f in DATA.iterdir():
        shutil.copy(f, root / f.name)
        os.utime(root / f.name)
    monkeypatch.setenv("TC_BESTTRACK_CACHE", str(root))
    return root


def _fetch(mod, tmp_path, *args):
    out = tmp_path / "bt.zarr"
    run_skill(mod.fetch, *args, "-o", str(out))
    return xr.open_zarr(out).load()


def test_hurdat_storm_units_and_schema(mod, cache, tmp_path):
    ds = _fetch(mod, tmp_path, "--storm", "TestA", "--year", "2030", "--basin", "north_atlantic")
    assert ds.attrs["featureType"] == "trajectory"
    assert list(ds["track"].values) == ["AL992030"]
    assert ds["kind"].values[0] == "best_track"
    assert ds["storm_name"].values[0] == "TESTA"
    lat, lon = ds["latitude"].values[0], ds["longitude"].values[0]
    np.testing.assert_allclose(lat[:2], [15.0, 15.5])
    np.testing.assert_allclose(lon[:2], [-50.0, -51.0])
    np.testing.assert_allclose(ds["msl"].values[0][:2], [100800.0, 100400.0])
    assert ds["msl"].attrs["units"] == "Pa"
    np.testing.assert_allclose(ds["wind_max"].values[0][0], 30 * 1852 / 3600, rtol=1e-6)
    # 30 kt TD, 40/55 kt TS, 70 kt Cat 1, 90 kt Cat 2, 100 kt Cat 3+.
    np.testing.assert_array_equal(ds["category"].values[0][:6], [0, 1, 1, 2, 3, 4])
    assert list(ds["storm_type"].values[0][:4]) == ["TD", "TS", "TS", "HU"]
    assert ds["time"].values[0][0] == np.datetime64("2030-09-01T00:00", "ns")
    assert "1-minute" in ds.attrs["tc_wind_averaging"]


def test_ibtracs_storm_id_basin_from_prefix(mod, cache, tmp_path):
    ds = _fetch(mod, tmp_path, "--storm-id", "WP302026", "--source", "ibtracs")
    assert ds["storm"].values[0] == "WP302026"
    assert ds["storm_name"].values[0] == "IN-FA"
    assert np.isfinite(ds["latitude"].values).sum() == 12


def test_window_with_bbox(mod, cache, tmp_path):
    ds = _fetch(
        mod,
        tmp_path,
        "--start",
        "2030-09-04",
        "--end",
        "2030-09-06",
        "--basin",
        "north_atlantic",
        "--source",
        "hurdat",
    )
    assert list(ds["storm"].values) == ["AL982030"]  # TESTA ended on 2 Sep
    ds = _fetch(
        mod,
        tmp_path,
        "--start",
        "2030-09-01",
        "--end",
        "2030-09-06",
        "--bbox",
        "20/-60/10/-45",
        "--source",
        "hurdat",
    )
    assert list(ds["storm"].values) == ["AL992030"]  # TESTB is outside the box


def test_unknown_and_bad_usage_exit_2(mod, cache, tmp_path):
    for args in (
        ("--storm", "Nobody", "--year", "2030", "--basin", "north_atlantic"),
        ("--storm", "TestA", "--basin", "north_atlantic"),  # no --year
        ("--storm", "TestA", "--year", "2030", "--active"),  # two modes
        ("--storm-id", "AL992030", "--models", "ecmwf-ens"),  # no --init
    ):
        with pytest.raises(SystemExit) as exc:
            run_skill(mod.fetch, *args, "-o", str(tmp_path / "x.zarr"))
        assert exc.value.code == 2, args


def test_forecast_rows_and_ensemble_numbers(mod):
    init = pd.Timestamp("2030-09-01T00").to_pydatetime()
    fc = {
        "init": init,
        "fhr": [0, 12, 24],
        "lat": [15, 16, 17],
        "lon": [-50, -52, -54],
        "vmax": [30, 50, 70],
        "mslp": [1008, np.nan, 990],
        "type": ["TD", "TS", "HU"],
    }
    rows = [mod.forecast_row("AL992030", "TESTA", f"adeck:EE{k:02d}", fc, k) for k in (1, 2)]
    ds = mod.to_dataset(rows, {})
    assert list(ds["number"].values) == [1, 2]
    assert ds.attrs["ensemble_members"] == [1, 2]
    assert ds["track"].values[0] == "AL992030:EE01:2030090100"
    assert ds["forecast_reference_time"].values[0] == np.datetime64(init, "ns")
    assert ds["time"].values[0][2] == np.datetime64("2030-09-02T00:00", "ns")
    assert np.isnan(ds["msl"].values[0][1])


def test_bbox_basins(mod):
    assert mod._bbox_basins((-2.0, 30.0, -30.0, 60.0)) == ["south_indian"]
    assert "south_pacific" in mod._bbox_basins((-5.0, 170.0, -25.0, -170.0))


def test_phase_from_official_types(mod):
    got = mod.phase_from_types(["LO", "TD", "TS", "HU", "EX", "LO", ""])
    assert got == [
        "pre-genesis",
        "tropical",
        "tropical",
        "tropical",
        "extratropical",
        "remnant",
        "",
    ]
    assert mod.phase_from_types(["DB", "LO"]) == ["pre-genesis", "pre-genesis"]


def test_best_track_has_phase(mod, cache, tmp_path):
    ds = _fetch(mod, tmp_path, "--storm", "TestA", "--year", "2030", "--basin", "north_atlantic")
    assert list(ds["phase"].values[0][:6]) == ["tropical"] * 6
