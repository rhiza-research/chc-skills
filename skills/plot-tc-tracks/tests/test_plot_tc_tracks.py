"""Tests for plot-tc-tracks on synthetic tc-track and tc-phase-space output."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import load_skill, make_tc_dataset, run_skill, write_zarr
from weather_skills_core.provenance import load_figure_history


@pytest.fixture(scope="module")
def mods():
    return (
        load_skill("tc-track", "tc_track"),
        load_skill("tc-strike-probability", "tc_strike_probability"),
        load_skill("tc-phase-space", "tc_phase_space"),
        load_skill("plot-tc-tracks", "plot_tc_tracks"),
    )


@pytest.fixture(scope="module")
def tracks(mods, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tc")
    src = write_zarr(
        make_tc_dataset(members=3, levels=(300.0, 500.0, 600.0, 900.0)), tmp / "fc.zarr"
    )
    out = tmp / "tracks.zarr"
    run_skill(mods[0].tc_track, "-i", str(src), "-o", str(out))
    return src, out


def _strike(mods, trk, tmp_path):
    sp = tmp_path / "sp.zarr"
    run_skill(
        mods[1].tc_strike_probability,
        "-i",
        str(trk),
        "--bbox",
        "0/35/-30/75",
        "--resolution",
        "1",
        "-o",
        str(sp),
    )
    return sp


def test_track_map(mods, tracks, tmp_path):
    _, trk = tracks
    png = tmp_path / "map.png"
    run_skill(mods[3].plot_tc_tracks, "-i", str(trk), "--color-by", "wind", "-o", str(png))
    assert png.stat().st_size > 10_000
    assert load_figure_history(png)[-1]["skill"] == "plot-tc-tracks"


def test_strike_map(mods, tracks, tmp_path):
    _, trk = tracks
    sp = _strike(mods, trk, tmp_path)
    png = tmp_path / "strike.png"
    run_skill(
        mods[3].plot_tc_tracks,
        "-i",
        str(trk),
        "--kind",
        "strike",
        "--strike-prob",
        str(sp),
        "-o",
        str(png),
    )
    assert png.stat().st_size > 10_000
    hist = load_figure_history(png)
    assert hist[-1]["skill"] == "plot-tc-tracks"


def test_strike_map_needs_strike_and_map_refuses_it(mods, tracks, tmp_path):
    _, trk = tracks
    for args in (
        ("--kind", "strike"),
        ("--strike-prob", str(_strike(mods, trk, tmp_path))),
    ):
        with pytest.raises(SystemExit) as exc:
            run_skill(mods[3].plot_tc_tracks, "-i", str(trk), *args, "-o", str(tmp_path / "x.png"))
        assert exc.value.code == 2


def test_storm_groups_and_mean_track(mods):
    import numpy as np
    import xarray as xr

    t0 = np.datetime64("2026-02-01T00", "ns")
    times = t0 + np.arange(4) * np.timedelta64(6, "h")
    # Members 0-2 follow one storm (a 1 degree spread), member 3 a storm 20 degrees away.
    lat = np.array([[-15.0, -15.5, -16.0, -16.5]] * 4)
    lat[1] += 1.0
    lat[2] -= 1.0
    lat[3] += 20.0
    lon = np.tile([60.0, 59.0, 58.0, 57.0], (4, 1))
    dims = ("track", "obs")
    ds = xr.Dataset(
        {"msl": (dims, np.full(lat.shape, 99000.0))},
        coords={
            "track": ["m000-001", "m001-001", "m002-001", "m003-001"],
            "number": ("track", [0, 1, 2, 3]),
            "time": (dims, np.tile(times, (4, 1))),
            "latitude": (dims, lat),
            "longitude": (dims, lon),
        },
    )
    groups = mods[3].storm_groups(ds)
    assert sorted(map(sorted, groups)) == [[0, 1, 2], [3]]
    t, mlat, mlon = mods[3].ensemble_mean_track(ds, groups[0], min_members=3)
    assert t.size == 4
    np.testing.assert_allclose(mlat, lat[0], atol=0.01)
    np.testing.assert_allclose(mlon, lon[0], atol=1e-6)
    t, _, _ = mods[3].ensemble_mean_track(ds, groups[0], min_members=4)
    assert t.size == 0


def test_phase_diagram(mods, tracks, tmp_path):
    src, trk = tracks
    ph = tmp_path / "phase.zarr"
    run_skill(mods[2].tc_phase_space, "--tracks", str(trk), "-i", str(src), "-o", str(ph))
    png = tmp_path / "phase.png"
    run_skill(mods[3].plot_tc_tracks, "-i", str(ph), "--kind", "phase", "-o", str(png))
    assert png.stat().st_size > 10_000


def test_phase_needs_phase_space_output(mods, tracks, tmp_path):
    _, trk = tracks
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mods[3].plot_tc_tracks, "-i", str(trk), "--kind", "phase", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2


def test_map_by_category_wide_extent(mods, tracks, tmp_path):
    _, trk = tracks
    png = tmp_path / "cat.png"
    run_skill(
        mods[3].plot_tc_tracks,
        "-i",
        str(trk),
        "--color-by",
        "category",
        "--bbox",
        "60/-180/-50/180",
        "-o",
        str(png),
    )
    assert png.stat().st_size > 10_000
    assert load_figure_history(png)[-1]["skill"] == "plot-tc-tracks"


def _official(tmp_path):
    from conftest import line_track, make_tracks

    ref = make_tracks(
        [
            ("SH012026", None, 0, line_track(-15, 70, 12, dlat=0.0), 97000.0, 40.0),
            ("SH022026", None, 0, line_track(-10, 95, 8, dlat=0.0), 99000.0, 20.0),
        ],
        reference=True,
    )
    ref["category"] = (("track", "obs"), np.where(np.isfinite(ref["wind_max"].values), 1.0, np.nan))
    model = make_tracks(
        [
            ("m000-001", 0, 0, line_track(-15.3, 69.8, 12, dlat=0.0), 99000.0, 20.0),
            ("m001-001", 1, 0, line_track(-14.8, 70.3, 12, dlat=0.0), 99000.0, 20.0),
        ],
        init="2026-02-01T00",
    )
    model = model.assign_coords(storm=("track", ["SH012026", "SH012026"]))
    seg = np.array([["observed"] * 3 + ["forecast"] * 9] * 2)
    model["segment"] = (("track", "obs"), seg)
    return (
        write_zarr(model, tmp_path / "model.zarr"),
        write_zarr(ref, tmp_path / "ref.zarr"),
        model,
        ref,
    )


def test_map_with_reference_and_storm(mods, tmp_path):
    model, ref, _, _ = _official(tmp_path)
    png = tmp_path / "ref_map.png"
    run_skill(
        mods[3].plot_tc_tracks,
        "-i",
        str(model),
        "--reference",
        str(ref),
        "--storm",
        "SH012026",
        "--color-by",
        "wind",
        "-o",
        str(png),
    )
    assert png.stat().st_size > 10_000
    with pytest.raises(SystemExit) as exc:
        run_skill(mods[3].plot_tc_tracks, "-i", str(model), "--storm", "NOBODY", "-o", str(png))
    assert exc.value.code == 2


def test_split_observed(mods, tmp_path):
    _, _, model, _ = _official(tmp_path)
    fc, observed = mods[3].split_observed(model)
    assert np.isnan(fc["latitude"].values[:, :3]).all()
    assert np.isfinite(fc["latitude"].values[:, 3:]).all()
    assert observed[0].size == 3


def test_errors_plot_from_tc_verify(mods, tmp_path):
    model, ref, _, _ = _official(tmp_path)
    verify = load_skill("tc-verify", "tc_verify")
    out = tmp_path / "verify.zarr"
    run_skill(verify.tc_verify, "--tracks", str(model), "--reference", str(ref), "-o", str(out))
    png = tmp_path / "errors.png"
    run_skill(mods[3].plot_tc_tracks, "-i", str(out), "--kind", "errors", "-o", str(png))
    assert png.stat().st_size > 10_000
    with pytest.raises(SystemExit) as exc:
        run_skill(mods[3].plot_tc_tracks, "-i", str(model), "--kind", "errors", "-o", str(png))
    assert exc.value.code == 2
