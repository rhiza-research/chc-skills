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
    wyoming_calls = []

    def fake_wyoming(wmo, when):
        wyoming_calls.append((wmo, when))
        return _wyoming_launch()

    monkeypatch.setattr(module, "_wyoming", fake_wyoming)
    module.calls = calls
    module.wyoming_calls = wyoming_calls
    return module


def _wyoming_launch():
    """A Wyoming-style launch: no level-type column, surface first, many levels, winds in knots."""
    p = np.linspace(828, 100, 200)
    t = np.interp(p, [100, 500, 700, 828], [-78, -6, 9, 21])
    df = pd.DataFrame(
        {
            "pressure": p,
            "height": np.linspace(1800, 16600, 200),
            "temperature": t,
            "dewpoint": t - 6,
            "u_wind": np.full(200, 10.0),
            "v_wind": np.zeros(200),
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df.units = {
            "pressure": "hPa",
            "height": "meter",
            "temperature": "degC",
            "dewpoint": "degC",
            "u_wind": "knot",
            "v_wind": "knot",
        }
    return df


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


def test_igra2_dates_make_no_wyoming_requests(mod, tmp_path):
    ds = xr.open_zarr(_fetch(mod, tmp_path, "--station", "63741"), consolidated=True)
    assert mod.wyoming_calls == []  # 2025 is long in IGRA2: no gap to fill
    assert set(ds["source"].values.ravel()) == {"igra2"}
    assert ds.attrs["weather_skills_source"] == "igra2"


def test_wyoming_only(mod, tmp_path):
    ds = xr.open_zarr(
        _fetch(mod, tmp_path, "--station", "63741", "--source", "wyoming"), consolidated=True
    )
    assert mod.calls == []  # no IGRA2 request
    # 00 and 12 UTC on both days, plus 00 UTC after the end day is not asked for.
    assert [str(w)[:13] for _, w in mod.wyoming_calls] == [
        "2025-04-13 00",
        "2025-04-13 12",
        "2025-04-14 00",
        "2025-04-14 12",
    ]
    assert all(wmo == "63741" for wmo, _ in mod.wyoming_calls)
    one = ds.sel(station_id="KEM00063741").isel(time=0)
    assert str(one["source"].values) == "wyoming"
    assert float(one["sp"]) == pytest.approx(828.0)  # surface = highest pressure
    assert float(one["u"].sel(level=500)) == pytest.approx(5.14, abs=0.01)  # 10 kt
    assert ds.attrs["weather_skills_source"] == "wyoming"


def test_auto_fills_only_the_recent_gap(mod, monkeypatch, tmp_path):
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today - pd.Timedelta(days=2)
    monkeypatch.setattr(
        mod,
        "_request",
        lambda igra_id, s, e: _igra([_launch(start)]),  # IGRA2 has only the first launch
    )
    monkeypatch.setattr(
        mod,
        "_station_list",
        lambda: STATIONS.assign(last_year=today.year),
    )
    out = tmp_path / "snd.zarr"
    run_skill(
        mod.fetch,
        "--start-time",
        str(start.date()),
        "--end-time",
        str(today.date()),
        "--station",
        "63741",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    asked = [w for _, w in mod.wyoming_calls]
    assert asked and min(asked) > start  # only launches after IGRA2's latest
    sources = ds["source"].sel(station_id="KEM00063741").values
    assert sources[0] == "igra2" and set(sources[1:]) == {"wyoming"}
    assert ds.attrs["weather_skills_source"] == "igra2+wyoming"


def test_overloaded_wyoming_keeps_igra2(mod, monkeypatch, tmp_path, capsys):
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today - pd.Timedelta(days=1)
    monkeypatch.setattr(mod, "_request", lambda igra_id, s, e: _igra([_launch(start)]))
    monkeypatch.setattr(mod, "_station_list", lambda: STATIONS.assign(last_year=today.year))

    def busy(wmo, when):
        raise mod.DataError("Wyoming sounding service unreachable (503)")

    monkeypatch.setattr(mod, "_wyoming", busy)
    out = tmp_path / "snd.zarr"
    run_skill(
        mod.fetch,
        "--start-time",
        str(start.date()),
        "--end-time",
        str(today.date()),
        "--station",
        "63741",
        "-o",
        str(out),
    )
    assert "keeping the IGRA2 launches" in capsys.readouterr().err
    ds = xr.open_zarr(out, consolidated=True)
    assert set(ds["source"].values.ravel()) == {"igra2"}


def test_auto_uses_wyoming_when_igra2_is_down(mod, monkeypatch, tmp_path, capsys):
    def down(igra_id, s, e):
        raise mod.DataError("IGRA2 download failed for KEM00063741 after 3 tries (timeout)")

    monkeypatch.setattr(mod, "_request", down)
    ds = xr.open_zarr(_fetch(mod, tmp_path, "--station", "63741"), consolidated=True)
    assert "trying Wyoming for every launch" in capsys.readouterr().err
    assert len(mod.wyoming_calls) == 4  # the whole window, not only the last days
    assert set(ds["source"].values.ravel()) == {"wyoming"}


def test_igra2_only_fails_when_igra2_is_down(mod, monkeypatch, tmp_path):
    def down(igra_id, s, e):
        raise mod.DataError("IGRA2 download failed")

    monkeypatch.setattr(mod, "_request", down)
    with pytest.raises(SystemExit) as excinfo:
        _fetch(mod, tmp_path, "--station", "63741", "--source", "igra2")
    assert excinfo.value.code == 1
    assert mod.wyoming_calls == []


def test_station_list_cache(mod, monkeypatch, tmp_path, capsys):
    import requests

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "weather-skills" / "igra2-station-list.txt"
    cache.parent.mkdir(parents=True)
    cache.write_text("cached list")
    # A fresh cache is used without a download.
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: pytest.fail("downloaded"))
    assert mod._station_list_text() == "cached list"

    # A stale cache covers an NCEI outage.
    old = cache.stat().st_mtime - 8 * 86400
    import os

    os.utime(cache, (old, old))

    def unreachable(*a, **k):
        raise requests.ConnectTimeout("timed out")

    monkeypatch.setattr(mod.requests, "get", unreachable)
    assert mod._station_list_text() == "cached list"
    assert "using the cached IGRA2 station list" in capsys.readouterr().err
