"""Tests for tc-verify on hand-made model and official tracks."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import (
    TRACK_T0,
    line_track,
    load_skill,
    make_tracks,
    run_skill,
    write_zarr,
)

H6 = np.timedelta64(6, "h")


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-verify", "tc_verify")


def _run(mod, tmp_path, model, ref, *args):
    m = write_zarr(model, tmp_path / "model.zarr")
    r = write_zarr(ref, tmp_path / "ref.zarr")
    out = tmp_path / "verify.zarr"
    run_skill(mod.tc_verify, "--tracks", str(m), "--reference", str(r), "-o", str(out), *args)
    return xr.open_zarr(out).load()


def _ref():
    # Due west along 15 S, 1 degree per 6 h.
    return make_tracks(
        [("SH012026", None, 0, line_track(-15, 70, 12, dlat=0.0), 97000.0, 40.0)],
        reference=True,
    )


def test_along_and_cross_track_signs(mod, tmp_path):
    # Westward motion: ahead is further west, right of motion is north.
    ahead = line_track(-15, 69, 12, dlat=0.0)
    right = line_track(-14, 70, 12, dlat=0.0)
    model = make_tracks(
        [
            ("m000-001", 0, 0, ahead, 99000.0, 30.0),
            ("m001-001", 1, 0, right, 98000.0, 45.0),
        ],
        init=TRACK_T0,
    )
    model = model.assign_coords(storm=("track", ["SH012026", "SH012026"]))
    out = _run(mod, tmp_path, model, _ref())
    along, cross = out["along_track_error_km"].values, out["cross_track_error_km"].values
    assert np.nanmin(along[0][:-1]) > 100  # ~107 km ahead (the end is one-sided)
    assert np.nanmax(np.abs(cross[0])) < 1
    assert np.nanmin(cross[1]) > 100  # ~111 km right
    assert np.nanmax(np.abs(along[1])) < 1
    np.testing.assert_allclose(out["msl_error"].values[0][:3], 2000.0)
    np.testing.assert_allclose(out["wind_error"].values[1][:3], 5.0)
    np.testing.assert_allclose(out["lead_hours"].values[0][:3], [0.0, 6.0, 12.0])


def test_summary_by_lead_with_ensemble_mean_and_control(mod, tmp_path):
    north = line_track(-14, 70, 12, dlat=0.0)
    south = line_track(-16, 70, 12, dlat=0.0)
    model = make_tracks(
        [
            ("m000-001", 0, 0, north, 99000.0, 30.0),
            ("m001-001", 1, 0, south, 99000.0, 30.0),
        ],
        init=TRACK_T0,
    )
    model = model.assign_coords(storm=("track", ["SH012026", "SH012026"]))
    out = _run(mod, tmp_path, model, _ref())
    lead0 = out.sel(storm_id="SH012026", lead=0.0)
    assert lead0["n_tracks"].item() == 2
    assert lead0["mean_position_error_km"].item() == pytest.approx(111.2, abs=1.0)
    assert lead0["control_position_error_km"].item() == pytest.approx(111.2, abs=1.0)
    # The members straddle the official track, so the ensemble mean is on it.
    assert lead0["ensemble_mean_position_error_km"].item() < 1.0
    assert lead0["spread_km"].item() == pytest.approx(111.2, abs=1.0)


def test_observed_segment_is_not_verified(mod, tmp_path):
    model = make_tracks([("m000-001", 0, 0, line_track(-15, 69, 12, dlat=0.0), 99000.0, 30.0)])
    seg = np.array([["observed"] * 4 + ["forecast"] * 8])
    model["segment"] = (("track", "obs"), seg)
    out = _run(mod, tmp_path, model, _ref())
    dpe = out["position_error_km"].values[0]
    assert np.isnan(dpe[:4]).all()
    assert np.isfinite(dpe[4:11]).all()


def test_detection_hit_rate_and_false_alarms(mod, tmp_path):
    ref = make_tracks(
        [
            ("SH012026", None, 0, line_track(-15, 70, 12, dlat=0.0), 97000.0, 30.0),
            ("SH022026", None, 0, line_track(-12, 100, 12, dlat=0.0), 97000.0, 30.0),
            ("SH032026", None, 0, line_track(-20, 40, 12, dlat=0.0), 100500.0, 12.0),  # TD
        ],
        reference=True,
    )
    model = make_tracks(
        [
            ("001", 0, 0, line_track(-15.2, 70.2, 12, dlat=0.0), 99000.0, 20.0),  # hit
            ("002", 0, 0, line_track(-30, 10, 12, dlat=0.0), 99000.0, 20.0),  # false alarm
        ]
    )
    out = _run(mod, tmp_path, model, ref, "--mode", "detection")
    assert list(out["storm_id"].values) == ["SH012026", "SH022026"]  # the TD is not counted
    assert out.attrs["tc_verify_hit_rate"] == pytest.approx(0.5)
    assert out.attrs["tc_verify_false_alarm_ratio"] == pytest.approx(0.5)
    assert list(out["track_storm"].values) == ["SH012026", ""]
    out = _run(mod, tmp_path, model, ref, "--mode", "detection", "--bbox", "-5/50/-25/85")
    assert list(out["storm_id"].values) == ["SH012026"]
    assert out.attrs["tc_verify_hit_rate"] == pytest.approx(1.0)


def test_needs_storm_with_several_references(mod, tmp_path):
    ref = make_tracks(
        [
            ("SH012026", None, 0, line_track(-15, 70, 6), 97000.0, 30.0),
            ("SH022026", None, 0, line_track(-12, 100, 6), 97000.0, 30.0),
        ],
        reference=True,
    )
    model = make_tracks([("m000-001", 0, 0, line_track(-15, 70, 6), 99000.0, 20.0)])
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, model, ref)
    assert exc.value.code == 2
