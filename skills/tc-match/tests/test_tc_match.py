"""Tests for tc-match on hand-made model and official tracks."""

from __future__ import annotations

import json

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill, write_zarr

T0 = np.datetime64("2026-02-01T00", "ns")
H6 = np.timedelta64(6, "h")


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-match", "tc_match")


def _tracks(rows, members=None, init=None, reference=False):
    """Track file from rows of (id, number, first step index, [(lat, lon), ...], msl, wind)."""
    n_obs = max(len(r[3]) for r in rows)
    shape = (len(rows), n_obs)
    lat, lon = np.full(shape, np.nan), np.full(shape, np.nan)
    msl, wind = np.full(shape, np.nan), np.full(shape, np.nan)
    times = np.full(shape, np.datetime64("NaT", "ns"))
    for k, (_, _, start, pts, p, w) in enumerate(rows):
        for j, (a, b) in enumerate(pts):
            lat[k, j], lon[k, j] = a, b
            times[k, j] = T0 + (start + j) * H6
            msl[k, j], wind[k, j] = p, w
    dims = ("track", "obs")
    ds = xr.Dataset(
        {
            "msl": (dims, msl, {"units": "Pa"}),
            "wind_max": (dims, wind, {"units": "m s-1"}),
        },
        coords={
            "track": [r[0] for r in rows],
            "time": (dims, times),
            "latitude": (dims, lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "longitude": (dims, lon, {"units": "degrees_east", "standard_name": "longitude"}),
        },
        attrs={"featureType": "trajectory"},
    )
    if reference:
        ds = ds.assign_coords(
            storm=("track", [r[0] for r in rows]),
            storm_name=("track", [f"NAME{k}" for k in range(len(rows))]),
            kind=("track", ["best_track"] * len(rows)),
        )
    else:
        ds = ds.assign_coords(number=("track", [r[1] for r in rows]))
        ds.attrs["ensemble_members"] = members or sorted({r[1] for r in rows})
        if init is not None:
            ds.attrs["forecast_reference_time"] = str(init)[:19]
    return ds


def _line(lat0, lon0, n, dlat=-0.5, dlon=-1.0):
    return [(lat0 + dlat * k, lon0 + dlon * k) for k in range(n)]


def _run(mod, tmp_path, model, ref, *args):
    m = write_zarr(model, tmp_path / "model.zarr")
    r = write_zarr(ref, tmp_path / "ref.zarr")
    out = tmp_path / "matched.zarr"
    run_skill(mod.tc_match, "--tracks", str(m), "--reference", str(r), "-o", str(out), *args)
    return xr.open_zarr(out).load()


def test_nearest_track_per_member_and_far_track_rejected(mod, tmp_path):
    ref = _tracks([("SH012026", None, 0, _line(-15, 60, 12), 98000.0, 30.0)], reference=True)
    model = _tracks(
        [
            ("m000-001", 0, 0, _line(-15.5, 60.5, 12), 99000.0, 20.0),  # ~75 km off
            ("m000-002", 0, 0, _line(-14.0, 61.0, 12), 99000.0, 20.0),  # ~150 km off
            ("m001-001", 1, 0, _line(-15.0, 61.0, 12), 99000.0, 20.0),
            ("m002-001", 2, 0, _line(-35.0, 20.0, 12), 99000.0, 20.0),  # far away
        ],
        members=[0, 1, 2],
    )
    out = _run(mod, tmp_path, model, ref)
    assert sorted(out["track"].values) == ["m000-001", "m001-001"]
    assert set(out["storm"].values) == {"SH012026"}
    assert (out["match_distance_km"].values < 150).all()
    assert (out["match_gap_hours"].values == 0).all()
    assert json.loads(out.attrs["tc_match_missing_members"]) == {"SH012026": [2]}


def test_two_storms_and_storm_filter(mod, tmp_path):
    ref = _tracks(
        [
            ("SH012026", None, 0, _line(-15, 60, 10), 98000.0, 30.0),
            ("SH022026", None, 0, _line(-12, 90, 10), 98000.0, 30.0),
        ],
        reference=True,
    )
    model = _tracks(
        [
            ("m000-001", 0, 0, _line(-15.2, 60.2, 10), 99000.0, 20.0),
            ("m000-002", 0, 0, _line(-12.2, 90.2, 10), 99000.0, 20.0),
        ]
    )
    out = _run(mod, tmp_path, model, ref)
    assert dict(zip(out["track"].values, out["storm"].values, strict=True)) == {
        "m000-001": "SH012026",
        "m000-002": "SH022026",
    }
    out = _run(mod, tmp_path, model, ref, "--storm", "NAME1")
    assert list(out["track"].values) == ["m000-002"]
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, model, ref, "--storm", "NOBODY")
    assert exc.value.code == 2


def test_genesis_after_the_reference_starts(mod, tmp_path):
    ref = _tracks([("SH012026", None, 0, _line(-15, 60, 16), 98000.0, 30.0)], reference=True)
    late = _line(-15, 60, 16)[6:]  # the model forms the storm 36 h later
    model = _tracks([("m000-001", 0, 6, late, 99000.0, 20.0)])
    out = _run(mod, tmp_path, model, ref)
    assert list(out["track"].values) == ["m000-001"]


def test_realtime_gap_extrapolates_the_last_motion(mod, tmp_path):
    full = _line(-15, 60, 12)
    ref = _tracks([("SH012026", None, 0, full[:4], 98000.0, 30.0)], reference=True)
    # The official record ends at step 3; the forecast starts at step 7 (24 h later).
    model = _tracks([("m000-001", 0, 7, full[7:], 99000.0, 20.0)])
    out = _run(mod, tmp_path, model, ref)
    assert out["match_gap_hours"].values[0] == pytest.approx(24.0)
    assert out["match_distance_km"].values[0] < 10
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, model, ref, "--max-gap", "12")
    assert exc.value.code == 1


def test_blend_append_puts_the_observed_history_first(mod, tmp_path):
    ref = _tracks([("SH012026", None, 0, _line(-15, 60, 12), 97000.0, 40.0)], reference=True)
    model = _tracks(
        [("m000-001", 0, 4, _line(-15, 60, 12)[4:], 99000.0, 20.0)],
        init=T0 + 4 * H6,
    )
    out = _run(mod, tmp_path, model, ref, "--blend", "append")
    seg = out["segment"].values[0]
    ok = ~np.isnat(out["time"].values[0])
    assert list(seg[ok]) == ["observed"] * 4 + ["forecast"] * 8
    assert out["msl"].values[0][0] == 97000.0  # observed values in the observed part
    assert out["msl"].values[0][4] == 99000.0
    assert out["time"].values[0][0] == T0


def test_blend_adjust_starts_at_the_observed_state_and_fades(mod, tmp_path):
    ref = _tracks([("SH012026", None, 0, _line(-15, 60, 12), 97000.0, 40.0)], reference=True)
    # Model storm 1 degree south, 2000 Pa too weak and 20 m/s too slow at the init.
    model = _tracks(
        [("m000-001", 0, 4, _line(-16, 60, 12)[4:], 99000.0, 20.0)],
        init=T0 + 4 * H6,
    )
    out = _run(mod, tmp_path, model, ref, "--blend", "adjust")
    k = 4  # the first forecast point
    assert out["latitude"].values[0][k] == pytest.approx(-17.0, abs=1e-4)  # -15 -0.5*4
    assert out["msl"].values[0][k] == pytest.approx(97000.0)
    assert out["wind_max"].values[0][k] == pytest.approx(40.0)
    # 24 h later the position offset is gone; the intensity offset is half gone at 24 h.
    assert out["latitude"].values[0][k + 4] == pytest.approx(-16 - 0.5 * 8, abs=1e-4)
    assert out["msl"].values[0][k + 4] == pytest.approx(98000.0)
    assert out["msl"].values[0][k + 6] == pytest.approx(98500.0)  # 36 h: a quarter left
    assert out["blend_offset_km"].values[0] == pytest.approx(111.2, abs=1.0)
    assert out["blend_offset_msl"].values[0] == pytest.approx(-2000.0)


def test_assign_is_one_to_one(mod):
    scores = [(50.0, 0, 0, "a", 0.0), (60.0, 1, 0, "a", 0.0), (70.0, 1, 1, "a", 0.0)]
    got = mod.assign(scores, 400.0)
    assert got == {0: (0, 50.0, 0.0), 1: (1, 70.0, 0.0)}


def test_track_cut_at_an_implausible_jump(mod, tmp_path):
    ref = _tracks([("SH012026", None, 0, _line(-15, 60, 12), 98000.0, 30.0)], reference=True)
    hop = _line(-15, 60, 6) + [(-20.0, 50.0), (-20.5, 49.0)]  # ~1200 km in 6 h at step 6
    model = _tracks([("m000-001", 0, 0, hop, 99000.0, 20.0)])
    out = _run(mod, tmp_path, model, ref)
    assert np.isfinite(out["latitude"].values[0]).sum() == 6
    assert out["points_cut"].values[0] == 2
    out = _run(mod, tmp_path, model, ref, "--max-speed", "0")
    assert np.isfinite(out["latitude"].values[0]).sum() == 8


def test_fast_motion_poleward_of_30_is_kept(mod):
    t = T0 + np.arange(4) * H6
    row = {
        "time": t,
        "latitude": np.array([35.0, 37.0, 39.0, 41.0]),
        "longitude": np.array([140.0, 145.0, 150.0, 155.0]),  # ~25 m/s, extratropical
    }
    _, removed = mod.cut_fast(row, 20.0)
    assert removed == 0
