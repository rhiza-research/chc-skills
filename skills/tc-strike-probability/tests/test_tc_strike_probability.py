"""Tests for tc-strike-probability on hand-made tracks."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill, write_zarr
from weather_skills_core.provenance import load_history


@pytest.fixture(scope="module")
def mod():
    return load_skill("tc-strike-probability", "tc_strike_probability")


def _tracks(paths, members=4):
    """tc-track-shaped dataset: one track per (member, [(lat, lon), ...]) entry, 6-hourly."""
    n_obs = max(len(p) for _, p in paths)
    lat = np.full((len(paths), n_obs), np.nan)
    lon = np.full_like(lat, np.nan)
    times = np.full(lat.shape, np.datetime64("NaT", "ns"))
    t0 = np.datetime64("2026-02-01T00", "ns")
    for k, (_, pts) in enumerate(paths):
        for j, (a, b) in enumerate(pts):
            lat[k, j], lon[k, j] = a, b
            times[k, j] = t0 + np.timedelta64(6 * j, "h")
    dims = ("track", "obs")
    ds = xr.Dataset(
        {"msl": (dims, np.where(np.isnan(lat), np.nan, 99000.0), {"units": "Pa"})},
        coords={
            "track": [f"m{m:03d}-{k:03d}" for k, (m, _) in enumerate(paths)],
            "number": ("track", [m for m, _ in paths]),
            "time": (dims, times),
            "latitude": (dims, lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "longitude": (dims, lon, {"units": "degrees_east", "standard_name": "longitude"}),
        },
        attrs={"featureType": "trajectory", "ensemble_members": list(range(members))},
    )
    return ds


def test_half_of_members(mod, tmp_path):
    west = [(-15.0, 60.0 - k) for k in range(6)]  # passes over (-15, 57)
    north = [(-5.0, 60.0 - k) for k in range(6)]  # 10 deg further north
    inp = write_zarr(_tracks([(0, west), (1, west), (2, north)]), tmp_path / "t.zarr")
    out_path = tmp_path / "sp.zarr"
    run_skill(
        mod.tc_strike_probability,
        "-i",
        str(inp),
        "--bbox",
        "0/50/-25/65",
        "--resolution",
        "1",
        "-o",
        str(out_path),
    )
    out = xr.open_zarr(out_path).load()
    sp = out["strike_probability"]
    assert sp.sel(latitude=-15, longitude=57).item() == 50.0  # 2 of 4 members
    assert sp.sel(latitude=-5, longitude=57).item() == 25.0
    assert sp.sel(latitude=-24, longitude=51).item() == 0.0
    assert out.attrs["strike_members"] == 4
    assert "featureType" not in out.attrs
    assert load_history(out_path)[-1]["skill"] == "tc-strike-probability"


def test_radius_edge_and_densify(mod, tmp_path):
    # Two points 8 deg apart: the path between them must still count.
    inp = write_zarr(_tracks([(0, [(0.0, 50.0), (0.0, 58.0)])], members=1), tmp_path / "t.zarr")
    out_path = tmp_path / "sp.zarr"
    run_skill(
        mod.tc_strike_probability,
        "-i",
        str(inp),
        "--bbox",
        "6/48/-6/60",
        "--resolution",
        "0.5",
        "--radius",
        "200",
        "-o",
        str(out_path),
    )
    sp = xr.open_zarr(out_path)["strike_probability"]
    assert sp.sel(latitude=0, longitude=54).item() == 100.0  # between the two fixes
    assert sp.sel(latitude=1.5, longitude=54).item() == 100.0  # 167 km away
    assert sp.sel(latitude=2.0, longitude=54).item() == 0.0  # 222 km away


def test_time_window(mod, tmp_path):
    pts = [(-15.0, 60.0 - 2 * k) for k in range(6)]
    inp = write_zarr(_tracks([(0, pts)], members=1), tmp_path / "t.zarr")
    out_path = tmp_path / "sp.zarr"
    run_skill(
        mod.tc_strike_probability,
        "-i",
        str(inp),
        "--bbox",
        "-5/45/-25/65",
        "--resolution",
        "1",
        "--start",
        "2026-02-01T12",
        "-o",
        str(out_path),
    )
    sp = xr.open_zarr(out_path)["strike_probability"]
    assert sp.sel(latitude=-15, longitude=60).item() == 0.0  # before the window
    assert sp.sel(latitude=-15, longitude=54).item() == 100.0


def test_needs_one_grid(mod, tmp_path):
    inp = write_zarr(_tracks([(0, [(0.0, 50.0), (0.0, 51.0)])]), tmp_path / "t.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.tc_strike_probability, "-i", str(inp), "-o", str(tmp_path / "o.zarr"))
    assert exc.value.code == 2


def test_only_the_tropical_part_counts_by_default(mod, tmp_path):
    pts = [(-15.0, 60.0 - 2 * k) for k in range(6)]
    ds = _tracks([(0, pts)], members=1)
    ds["phase"] = (("track", "obs"), np.array([["tropical"] * 3 + ["extratropical"] * 3]))
    inp = write_zarr(ds, tmp_path / "t.zarr")
    out_path = tmp_path / "sp.zarr"
    args = ("-i", str(inp), "--bbox", "-5/45/-25/65", "--resolution", "1", "-o", str(out_path))
    run_skill(mod.tc_strike_probability, *args)
    sp = xr.open_zarr(out_path)
    assert sp["strike_probability"].sel(latitude=-15, longitude=58).item() == 100.0
    assert sp["strike_probability"].sel(latitude=-15, longitude=50).item() == 0.0
    assert sp.attrs["strike_phase"] == "tropical"
    run_skill(mod.tc_strike_probability, *args, "--phase", "all")
    sp = xr.open_zarr(out_path)
    assert sp["strike_probability"].sel(latitude=-15, longitude=50).item() == 100.0
