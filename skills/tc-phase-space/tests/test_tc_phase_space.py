"""Tests for tc-phase-space on synthetic vortices with known thermal structure."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_tc_dataset, run_skill, storm_path, write_zarr

LEVELS = np.arange(300.0, 901.0, 50.0)


@pytest.fixture(scope="module")
def track_mod():
    return load_skill("tc-track", "tc_track")


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-phase-space", "tc_phase_space")


def _heights(ds, kind="warm", tilt=0.0, levels=LEVELS):
    """Geopotential height on 900-300 hPa around the synthetic storm in ``ds``.

    ``warm``: the height depression shrinks upward (warm core); ``cold``: it deepens upward.
    ``tilt`` adds a north-south 600-900 hPa thickness gradient (m per degree).
    """
    lat, lon = storm_path(ds.sizes["time"], 6)
    LA, LO = np.meshgrid(ds["latitude"].values, ds["longitude"].values, indexing="ij")
    z = np.empty((ds.sizes["time"], levels.size, *LA.shape))
    for t in range(ds.sizes["time"]):
        dx = (LO - lon[t]) * np.cos(np.radians(lat[t]))
        shape = np.exp(-((LA - lat[t]) ** 2 + dx**2) / (2 * 2.0**2))
        for k, p in enumerate(levels):
            amp = 80.0 * (p - 200.0) / 700.0 if kind == "warm" else 80.0 * (1000.0 - p) / 700.0
            base = 7.0e3 * np.log(1000.0 / p)
            lift = tilt * (LA - lat[t]) * np.clip((900.0 - p) / 300.0, 0, 1)
            z[t, k] = base - amp * shape + lift
    return xr.Dataset(
        {"gh": (("time", "level", "latitude", "longitude"), z, {"units": "m"})},
        coords={
            "time": ds["time"],
            "level": ("level", levels, {"units": "hPa"}),
            "latitude": ds["latitude"],
            "longitude": ds["longitude"],
        },
    )


def _run(track_mod, mod, tmp_path, kind="warm", tilt=0.0, extra=(), levels=LEVELS):
    ds = make_tc_dataset()
    src = write_zarr(ds[["msl", "u10", "v10"]], tmp_path / "msl.zarr")
    tracks = tmp_path / "tracks.zarr"
    run_skill(track_mod.tc_track, "-i", str(src), "--no-warm-core", "-o", str(tracks))
    z = write_zarr(_heights(ds, kind, tilt, levels), tmp_path / f"z_{kind}.zarr")
    out = tmp_path / f"phase_{kind}.zarr"
    run_skill(mod.tc_phase_space, "--tracks", str(tracks), "-i", str(z), "-o", str(out), *extra)
    return xr.open_zarr(out).load()


def test_symmetric_warm_core(track_mod, mod, tmp_path):
    out = _run(track_mod, mod, tmp_path)
    assert out.sizes["track"] == 1
    assert np.nanmin(out["neg_vtl"].values) > 20
    assert np.nanmin(out["neg_vtu"].values) > 20
    assert np.nanmax(np.abs(out["B"].values)) < 3
    assert out.attrs["phase_space_radius_km"] == 500.0


def test_cold_core(track_mod, mod, tmp_path):
    out = _run(track_mod, mod, tmp_path, kind="cold")
    assert np.nanmax(out["neg_vtl"].values) < -20
    assert np.nanmax(out["neg_vtu"].values) < -20


def test_frontal_asymmetry(track_mod, mod, tmp_path):
    out = _run(track_mod, mod, tmp_path, tilt=10.0, extra=("--no-smooth",))
    assert np.nanmin(np.abs(out["B"].values)) > 10
    assert out.attrs["phase_space_smoothing_hours"] == 0.0


def test_missing_levels_exit_1(track_mod, mod, tmp_path):
    ds = make_tc_dataset()
    src = write_zarr(ds[["msl", "u10", "v10"]], tmp_path / "msl.zarr")
    tracks = tmp_path / "tracks.zarr"
    run_skill(track_mod.tc_track, "-i", str(src), "--no-warm-core", "-o", str(tracks))
    z = write_zarr(ds[["gh"]], tmp_path / "z.zarr")  # 300 and 500 hPa only
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.tc_phase_space,
            "--tracks",
            str(tracks),
            "-i",
            str(z),
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert exc.value.code == 1


def test_running_mean(mod):
    vals = np.array([0.0, 4.0, 8.0, np.nan, 4.0])
    hours = np.array([0.0, 6.0, 12.0, 18.0, 24.0])
    out = mod.running_mean(vals, hours)
    assert out[1] == pytest.approx(4.0)
    assert np.isnan(out[3])
    assert out[4] == pytest.approx(6.0)


def test_symmetric_warm_core_is_tropical(track_mod, mod, tmp_path):
    out = _run(track_mod, mod, tmp_path)
    assert np.nanmin(out["tropical"].values) == 1
    assert out.attrs["phase_space_lower_layer_approximate"] == 0
    assert out.attrs["phase_space_b_threshold_m"] == pytest.approx(10.0)


def test_lower_layer_only_levels(track_mod, mod, tmp_path):
    # dynamical's IFS-ENS: 925, 850 and 500 hPa only.
    out = _run(track_mod, mod, tmp_path, levels=np.array([500.0, 850.0, 925.0]))
    assert out.attrs["phase_space_lower_layer_hpa"] == [925.0, 500.0]
    assert out.attrs["phase_space_lower_layer_approximate"] == 1
    scale = np.log(925 / 500) / np.log(900 / 600)
    assert out.attrs["phase_space_b_threshold_m"] == pytest.approx(10.0 * scale)
    assert np.isnan(out["neg_vtu"].values).all()
    assert np.nanmin(out["neg_vtl"].values) > 20
    assert np.nanmin(out["tropical"].values) == 1


def test_tropical_only_drops_frontal_and_cold(track_mod, mod, tmp_path):
    warm = _run(track_mod, mod, tmp_path, extra=("--tropical-only",))
    assert warm.sizes["track"] == 1
    assert np.isfinite(warm["latitude"].values).sum() == 17
    frontal = _run(track_mod, mod, tmp_path, tilt=10.0, extra=("--tropical-only",))
    assert frontal.sizes["track"] == 0
    cold = _run(track_mod, mod, tmp_path, kind="cold", extra=("--tropical-only",))
    assert cold.sizes["track"] == 0


def test_keep_tropical_span(mod):
    hours = np.arange(10) * 6.0
    mask = np.array([0, 0, 1, 1, 0, 1, 1, 0, 0, 0], bool)
    np.testing.assert_array_equal(
        mod.keep_tropical(mask, hours, 24.0), [0, 0, 1, 1, 1, 1, 1, 0, 0, 0]
    )
    assert not mod.keep_tropical(mask, hours, 30.0).any()  # 4 points x 6 h = 24 h only


def test_keep_tropical_stops_at_transition(mod):
    hours = np.arange(16) * 6.0
    # Tropical 5 points, extratropical for 30 h, then warm and symmetric again (seclusion).
    mask = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], bool)
    np.testing.assert_array_equal(mod.keep_tropical(mask, hours, 24.0), [1] * 5 + [0] * 11)
    # A short-lived first spell is skipped for the first one that lasts long enough.
    mask = np.array([1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0], bool)
    np.testing.assert_array_equal(mod.keep_tropical(mask, hours, 24.0), [0] * 5 + [1] * 5 + [0] * 6)
