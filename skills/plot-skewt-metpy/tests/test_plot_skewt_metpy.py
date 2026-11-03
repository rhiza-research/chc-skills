"""Tests for plot-skewt-metpy on pressure-level forecasts."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_zarr

LEVELS = np.array([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100.0])
T_C = np.array([28, 23, 19, 10, 3, -5, -16, -31, -41, -53, -67, -78.0])
RH = np.array([70, 75, 75, 65, 50, 35, 30, 30, 30, 30, 20, 10.0])
Z_M = np.array([110, 760, 1460, 3010, 4200, 5570, 7180, 9160, 10360, 11780, 13610, 16180.0])
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def mod():
    return load_skill("plot-skewt-metpy", "plot_skewt_metpy")


def _field(like, values, **attrs):
    """``like``'s shape filled with ``values``, carrying exactly ``attrs``."""
    out = xr.zeros_like(like) + values
    out.attrs = attrs
    return out


def _profiles(members=3, winds=True):
    ds = make_forecast(n_step=2, members=members, name="t", fill=0.0)
    ds = ds.expand_dims(level=LEVELS).transpose("number", "step", "level", ...).copy()
    ds["level"].attrs = {"units": "hPa", "standard_name": "air_pressure"}
    lev = {"dims": "level", "coords": {"level": LEVELS}}
    spread = xr.DataArray(np.linspace(-1, 1, members), dims="number")
    ds["t"] = _field(ds["t"], xr.DataArray(T_C, **lev) + spread, units="degree_Celsius")
    ds["r"] = _field(ds["t"], xr.DataArray(RH, **lev), units="%", standard_name="relative_humidity")
    if winds:
        z = xr.DataArray(Z_M, **lev)
        ds["gh"] = _field(ds["t"], z, units="m", standard_name="geopotential_height")
        ds["u"] = _field(ds["t"], 0.003 * z, units="m s-1")
        ds["v"] = _field(ds["t"], 5.0 * np.sin(z / 4000.0), units="m s-1")
    return ds


def test_ensemble_skewt_with_hodograph(mod, tmp_path, capsys):
    inp = write_zarr(_profiles(), tmp_path / "lev.zarr")
    out = tmp_path / "skewt.png"
    run_skill(
        mod.plot_skewt,
        "-i",
        str(inp),
        "--lat",
        "1.2",
        "--lon",
        "10.9",
        "--isel",
        "step=1",
        "-o",
        str(out),
    )
    assert out.read_bytes()[:8] == PNG
    assert "temperature=t, relative_humidity=r" in capsys.readouterr().err


def test_surface_pressure_hides_levels_below_ground(mod, tmp_path):
    ds = _profiles(members=1, winds=False).isel(number=0)
    base = ds["t"].isel(level=0, drop=True)
    ds["sp"] = _field(base, 830.0, units="hPa", standard_name="surface_air_pressure")
    ds["t2m"] = _field(base, 22.0, units="degree_Celsius")
    ds["d2m"] = _field(base, 12.0, units="degree_Celsius")
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    out = tmp_path / "skewt.png"
    run_skill(
        mod.plot_skewt,
        "-i",
        str(inp),
        "--lat",
        "1",
        "--lon",
        "10",
        "--isel",
        "step=0",
        "--title",
        "Nairobi",
        "-o",
        str(out),
    )
    assert out.read_bytes()[:8] == PNG


def test_needs_a_point(mod, tmp_path, capsys):
    inp = write_zarr(_profiles(), tmp_path / "lev.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.plot_skewt, "-i", str(inp), "-o", str(tmp_path / "x.png"))
    assert excinfo.value.code == 2
    assert "--lat and --lon" in capsys.readouterr().err


def test_needs_one_lead(mod, tmp_path, capsys):
    inp = write_zarr(_profiles(), tmp_path / "lev.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.plot_skewt,
            "-i",
            str(inp),
            "--lat",
            "1",
            "--lon",
            "10",
            "-o",
            str(tmp_path / "x.png"),
        )
    assert excinfo.value.code == 2
    assert "--isel step=0" in capsys.readouterr().err
