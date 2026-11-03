"""Tests for sounding-fetch with the IGRA2 station list and downloads replaced by fixtures."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import load_skill, run_skill

PNG = b"\x89PNG\r\n\x1a\n"
UNITS = {
    "pressure": "hPa",
    "height": "meter",
    "temperature": "degC",
    "dewpoint": "degC",
    "u_wind": "meter / second",
    "v_wind": "meter / second",
}
STATIONS = pd.DataFrame(
    [
        {
            "station_id": "KEM00063741",
            "latitude": -1.3012,
            "longitude": 36.7597,
            "elevation": 1796.2,
            "name": "DAGORETTI CORNER",
            "first_year": 1949,
            "last_year": 2025,
        },
        {
            "station_id": "KEM00063820",
            "latitude": -4.033,
            "longitude": 39.617,
            "elevation": 55.0,
            "name": "MOMBASA",
            "first_year": 1973,
            "last_year": 1984,
        },
    ]
)


def _launch(date, warm=0.0):
    """One IGRA2-style launch: surface at 830 hPa, standard and significant levels."""
    p = [830, 800, 700, 650, 500, 400, 300, 250, 200, 150, 100]
    t = np.array([22, 19, 10, 6, -5, -16, -31, -41, -53, -67, -78.0]) + warm
    td = np.array([14, 13, 4, -2, -20, -30, -45, -55, -65, -80, -90.0])
    z = [np.nan, np.nan, 3146, np.nan, 5870, 7590, 9690, 10950, 12430, 14250, 16620]
    u = [-1.0, -2.0, -8.0, np.nan, -6.0, -3.0, 0.6, 2.1, 5.0, 8.0, 10.0]
    v = [-0.5, -1.0, -5.6, np.nan, -2.0, 0.0, 0.8, -3.6, -4.0, -2.0, 0.0]
    return pd.DataFrame(
        {
            "lvltyp1": [2] + [1] * 10,
            "lvltyp2": [1] + [0] * 10,
            "pressure": p,
            "height": z,
            "temperature": t,
            "dewpoint": td,
            "u_wind": u,
            "v_wind": v,
            "date": pd.Timestamp(date),
        }
    )


def _igra(launches):
    df = pd.concat(launches, ignore_index=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pandas warns about attribute assignment
        df.units = UNITS
    header = df[["date"]].drop_duplicates()
    return df, header


@pytest.fixture
def mod(monkeypatch):
    module = load_skill("sounding-fetch", "sounding_fetch")
    calls = []

    def fake_request(igra_id, start, end):
        calls.append(igra_id)
        if igra_id != "KEM00063741":
            return None
        return _igra([_launch("2025-04-13"), _launch("2025-04-14", warm=2.0)])

    monkeypatch.setattr(module, "_station_list", lambda: STATIONS)
    monkeypatch.setattr(module, "_request", fake_request)
    module.calls = calls
    return module


def _fetch(mod, tmp_path, *extra):
    out = tmp_path / "snd.zarr"
    run_skill(
        mod.fetch,
        "--start-time",
        "2025-04-13",
        "--end-time",
        "2025-04-14",
        *extra,
        "-o",
        str(out),
    )
    return out


def test_grid_surface_and_layout(mod, tmp_path):
    out = _fetch(mod, tmp_path, "--station", "63741")
    ds = xr.open_zarr(out, consolidated=True)
    assert ds.attrs["featureType"] == "timeSeriesProfile"
    assert ds.attrs["siphon_version"] and ds.attrs["metpy_version"]
    assert ds["t"].dims == ("station_id", "time", "level")
    assert ds["level"].attrs["units"] == "hPa"
    one = ds.sel(station_id="KEM00063741").isel(time=0)
    t = one["t"]
    assert float(t.sel(level=700)) == pytest.approx(10.0)  # a reported level is kept
    assert np.isnan(float(t.sel(level=850)))  # below the 830 hPa surface
    assert np.isnan(float(t.sel(level=1000)))
    assert float(one["sp"]) == 830.0
    assert float(one["t2m"]) == 22.0 and float(one["d2m"]) == 14.0
    # Heights only at standard levels: interpolated between them in log-p.
    assert 3146 < float(one["gh"].sel(level=600)) < 5870
    assert float(ds["u"].isel(time=0).sel(level=700).squeeze()) == pytest.approx(-8.0)
    assert ds["u"].attrs["units"] == "m s-1"
    assert float(ds["t"].isel(time=1).sel(level=700).squeeze()) == pytest.approx(12.0)


def test_mandatory_levels(mod, tmp_path):
    out = _fetch(mod, tmp_path, "--station", "KEM00063741", "--levels", "mandatory")
    levels = xr.open_zarr(out, consolidated=True)["level"].values
    np.testing.assert_array_equal(levels, mod.MANDATORY)


def test_bbox_skips_inactive_stations(mod, tmp_path):
    _fetch(mod, tmp_path, "--bbox", "0/34/-5/42")
    # Mombasa's record ended in 1984, so only Nairobi is requested for 2025.
    assert mod.calls == ["KEM00063741"]


def test_unknown_station_exit_2(mod, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _fetch(mod, tmp_path, "--station", "99999")
    assert excinfo.value.code == 2
    assert "not in the IGRA2 station list" in capsys.readouterr().err


def test_no_launches_exit_1(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "_request", lambda igra_id, start, end: None)
    with pytest.raises(SystemExit) as excinfo:
        _fetch(mod, tmp_path, "--station", "63741")
    assert excinfo.value.code == 1
    assert "no launches" in capsys.readouterr().err


def test_feeds_the_metpy_sounding_skills(mod, tmp_path):
    out = _fetch(mod, tmp_path, "--station", "63741")
    indices = load_skill("sounding-indices-metpy", "sounding_indices_metpy")
    idx = tmp_path / "idx.zarr"
    run_skill(
        indices.sounding_indices,
        "-i",
        str(out),
        "--index",
        "sbcape",
        "--index",
        "lcl",
        "-o",
        str(idx),
    )
    res = xr.open_zarr(idx, consolidated=True)
    assert res["sbcape"].dims == ("station_id", "time")
    # The parcel starts at the 830 hPa surface, so the LCL lies above it.
    assert (res["lcl_pressure"] < 830).all()
    skewt = load_skill("plot-skewt-metpy", "plot_skewt_metpy")
    png = tmp_path / "skewt.png"
    run_skill(
        skewt.plot_skewt,
        "-i",
        str(out),
        "--station",
        "KEM00063741",
        "--isel",
        "time=1",
        "-o",
        str(png),
    )
    assert png.read_bytes()[:8] == PNG


def test_station_whose_record_ended_is_skipped(mod, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _fetch(mod, tmp_path, "--station", "63820")  # Mombasa: 1973-1984
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "records only for 1973-1984; skipped" in err
    assert mod.calls == []
