"""Tests for sounding-indices-metpy on pressure-level forecasts and surface fields."""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest
import xarray as xr
from conftest import SKILLS_ROOT, load_skill, make_forecast, run_skill, write_zarr

LEVELS = np.array([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100.0])
T_C = np.array([28, 23, 19, 10, 3, -5, -16, -31, -41, -53, -67, -78.0])
TD_C = np.array([22, 19, 15, 4, -6, -20, -30, -45, -55, -65, -80, -90.0])
# Rough standard-atmosphere heights of the levels (m).
Z_M = np.array([110, 760, 1460, 3010, 4200, 5570, 7180, 9160, 10360, 11780, 13610, 16180.0])


@pytest.fixture
def mod():
    return load_skill("sounding-indices-metpy", "sounding_indices_metpy")


def _field(like, values, **attrs):
    """``like``'s shape filled with ``values``, carrying exactly ``attrs`` (xarray keeps
    the template's attrs through arithmetic, so a units-only field would inherit them)."""
    out = xr.zeros_like(like) + values
    out.attrs = attrs
    return out


def _levels_forecast(members=2, warm=(0.0, 2.0)):
    """Forecast on pressure levels: t (K) and q, the second member warmer near the ground."""
    import metpy.calc as mpcalc
    from metpy.units import units as mu

    ds = make_forecast(n_step=2, members=members, name="t", fill=0.0)
    ds = ds.expand_dims(level=LEVELS).transpose("number", "step", "level", ...).copy()
    ds["level"].attrs = {"units": "hPa", "standard_name": "air_pressure"}
    shift = xr.DataArray(np.array(warm[:members]), dims="number") * xr.DataArray(
        np.exp(-(1000 - LEVELS) / 150), dims="level", coords={"level": LEVELS}
    )
    t_c = xr.DataArray(T_C, dims="level", coords={"level": LEVELS}) + shift
    ds["t"] = _field(ds["t"], t_c + 273.15, units="K", standard_name="air_temperature")
    q = mpcalc.specific_humidity_from_dewpoint(LEVELS * mu.hPa, TD_C * mu.degC).m_as("")
    ds["q"] = _field(
        ds["t"], xr.DataArray(q, dims="level"), units="kg kg-1", standard_name="specific_humidity"
    )
    return ds


def _direct(p, t, td):
    """MetPy on one profile, for comparison."""
    import metpy.calc as mpcalc
    from metpy.units import units as mu

    p, t, td = p * mu.hPa, t * mu.degC, td * mu.degC
    prof = mpcalc.parcel_profile(p, t[0], td[0])
    cape, cin = mpcalc.cape_cin(p, t, td, prof)
    li = mpcalc.lifted_index(p, t, prof)
    return {
        "sbcape": cape.m_as("J/kg"),
        "sbcin": cin.m_as("J/kg"),
        "lifted_index": float(np.ravel(li.m_as("delta_degC"))[0]),
        "precipitable_water": mpcalc.precipitable_water(p, td).m_as("mm"),
        "k_index": mpcalc.k_index(p, t, td).m_as("degC"),
    }


def test_default_indices_match_metpy(mod, tmp_path, capsys):
    ds = _levels_forecast()
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    out = tmp_path / "idx.zarr"
    run_skill(mod.sounding_indices, "-i", str(inp), "-o", str(out))
    assert "temperature=t, specific_humidity=q" in capsys.readouterr().err
    res = xr.open_zarr(out, consolidated=True)
    assert set(res.data_vars) == {
        "sbcape",
        "sbcin",
        "lifted_index",
        "lcl_pressure",
        "lcl_temperature",
        "k_index",
        "total_totals_index",
        "precipitable_water",
    }
    assert res["sbcape"].dims == ("number", "step", "latitude", "longitude")
    assert res["sbcape"].attrs["units"] == "J kg-1"
    assert res["precipitable_water"].attrs["units"] == "mm"
    assert res.attrs["metpy_version"]
    col = ds.isel(number=1, step=0, latitude=0, longitude=0)
    td = TD_C  # q was built from TD_C
    ref = _direct(LEVELS, col["t"].values - 273.15, td)
    got = res.isel(number=1, step=0, latitude=0, longitude=0)
    for name, value in ref.items():
        assert float(got[name]) == pytest.approx(value, rel=0.02, abs=0.5), name
    # The warmer member is more unstable.
    assert float(res["sbcape"].isel(number=1).mean()) > float(res["sbcape"].isel(number=0).mean())


def test_surface_drops_levels_below_ground(mod, tmp_path):
    ds = _levels_forecast(members=1)
    base = ds["t"].isel(level=0, drop=True)
    surface = xr.Dataset(
        {
            "t2m": _field(base, 22.0, units="degree_Celsius"),
            "d2m": _field(base, 12.0, units="degree_Celsius"),
            "sp": _field(base, 830.0, units="hPa", standard_name="surface_air_pressure"),
        }
    )
    a = write_zarr(ds, tmp_path / "lev.zarr")
    b = write_zarr(surface, tmp_path / "sfc.zarr")
    out = tmp_path / "idx.zarr"
    run_skill(
        mod.sounding_indices,
        "-i",
        str(a),
        "-i",
        str(b),
        "--index",
        "sbcape",
        "--index",
        "lcl",
        "--index",
        "precipitable_water",
        "-o",
        str(out),
    )
    got = xr.open_zarr(out, consolidated=True).isel(number=0, step=0, latitude=0, longitude=0)
    above = LEVELS < 830
    p = np.concatenate([[830.0], LEVELS[above]])
    t = np.concatenate(
        [[22.0], ds["t"].isel(number=0, step=0, latitude=0, longitude=0).values[above] - 273.15]
    )
    td = np.concatenate([[12.0], TD_C[above]])
    ref = _direct(p, t, td)
    assert float(got["sbcape"]) == pytest.approx(ref["sbcape"], rel=0.02, abs=0.5)
    assert float(got["precipitable_water"]) == pytest.approx(ref["precipitable_water"], rel=0.02)
    assert float(got["lcl_pressure"]) < 830.0


def test_bulk_shear_and_helicity(mod, tmp_path):
    ds = _levels_forecast(members=1)
    z = xr.DataArray(Z_M, dims="level", coords={"level": LEVELS})
    ds["gh"] = _field(ds["t"], z, units="m", standard_name="geopotential_height")
    ds["u"] = _field(ds["t"], 0.002 * (z - Z_M[0]), units="m s-1")
    ds["v"] = _field(ds["t"], 0.0, units="m s-1")
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    out = tmp_path / "idx.zarr"
    run_skill(
        mod.sounding_indices,
        "-i",
        str(inp),
        "--index",
        "bulk_shear",
        "--index",
        "srh",
        "-o",
        str(out),
    )
    got = xr.open_zarr(out, consolidated=True)
    # u rises 2 m/s per km: 12 m/s of shear over 6 km.
    np.testing.assert_allclose(got["bulk_shear_0_6km"].values, 12.0, rtol=0.05)
    assert got["srh_0_1km"].attrs["units"] == "m2 s-2"
    assert np.isfinite(got["srh_0_3km"].values).all()


def test_no_pressure_levels_exit_2(mod, tmp_path, capsys):
    ds = make_forecast(name="t2m")
    ds["t2m"].attrs = {"units": "K"}
    inp = write_zarr(ds, tmp_path / "sfc.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.sounding_indices, "-i", str(inp), "-o", str(tmp_path / "o.zarr"))
    assert excinfo.value.code == 2
    assert "no pressure-level dim" in capsys.readouterr().err


def test_shear_without_winds_exit_2(mod, tmp_path, capsys):
    inp = write_zarr(_levels_forecast(members=1), tmp_path / "lev.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.sounding_indices,
            "-i",
            str(inp),
            "--index",
            "srh",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert excinfo.value.code == 2
    assert "need u, v and geopotential height" in capsys.readouterr().err


def test_workers_give_the_same_result(tmp_path):
    inp = write_zarr(_levels_forecast(), tmp_path / "lev.zarr")
    script = SKILLS_ROOT / "sounding-indices-metpy" / "scripts" / "sounding_indices_metpy.py"
    outs = {}
    for workers in (1, 2):
        out = tmp_path / f"w{workers}.zarr"
        subprocess.run(
            [
                sys.executable,
                str(script),
                "-i",
                str(inp),
                "--index",
                "sbcape",
                "--workers",
                str(workers),
                "-o",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        outs[workers] = xr.open_zarr(out, consolidated=True)["sbcape"].values
    np.testing.assert_allclose(outs[1], outs[2])
