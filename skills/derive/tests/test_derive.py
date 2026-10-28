"""Tests for derive: MetPy calculations on forecast, level and station envelopes."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_point_obs, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("derive", "derive")


def _surface_forecast(t=30.0, td=20.0):
    """Ensemble forecast with t2m and d2m in degree_Celsius."""
    ds = make_forecast(n_step=2, members=3, name="t2m", fill=t)
    ds["t2m"].attrs = {"units": "degree_Celsius", "standard_name": "air_temperature"}
    ds["d2m"] = xr.full_like(ds["t2m"], td)
    ds["d2m"].attrs = {"units": "degree_Celsius", "standard_name": "dew_point_temperature"}
    return ds


def _magnus_rh(t, td):
    """Relative humidity (%) from Bolton (1980); MetPy's formula is within 0.5 % of it."""

    def es(x):
        return 6.112 * np.exp(17.67 * x / (x + 243.5))

    return 100 * es(td) / es(t)


def test_relative_humidity_matched_from_names(mod, tmp_path, capsys):
    inp = write_zarr(_surface_forecast(), tmp_path / "fc.zarr")
    out = tmp_path / "rh.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "relative_humidity_from_dewpoint", "-o", str(out))
    assert "temperature=t2m, dewpoint=d2m" in capsys.readouterr().err
    result = xr.open_zarr(out, consolidated=True)
    assert set(result.data_vars) == {"relative_humidity"}
    rh = result["relative_humidity"]
    assert rh.dims == ("number", "step", "latitude", "longitude")
    assert rh.attrs["units"] == "%"
    assert rh.attrs["standard_name"] == "relative_humidity"
    assert "metpy.calc.relative_humidity_from_dewpoint" in rh.attrs["comment"]
    np.testing.assert_allclose(rh.values, _magnus_rh(30.0, 20.0), rtol=5e-3)


def test_heat_index_on_stations_masks_undefined(mod, tmp_path):
    ds = make_point_obs(n_time=2, n_points=2, name="t2m", fill=0.0)
    ds["t2m"].values[:] = [[22.0, 22.0], [32.0, 32.0]]  # below / above the heat-index range
    ds["t2m"].attrs = {"units": "degree_Celsius"}
    ds["rh"] = xr.full_like(ds["t2m"], 70.0)
    ds["rh"].attrs = {"units": "%"}
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "hi.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "heat_index", "-o", str(out))
    hi = xr.open_zarr(out, consolidated=True)["heat_index"]
    assert hi.dims == ("point_id", "time")
    assert hi.attrs["units"] == "degree_Celsius"
    assert np.isnan(hi.values[0]).all()
    # NOAA heat index at 32 °C and 70 % RH is about 41 °C.
    np.testing.assert_allclose(hi.values[1], 41.0, atol=1.0)


def test_heat_index_keeps_temperature_when_unmasked(mod, tmp_path):
    ds = make_point_obs(n_time=1, n_points=1, name="t2m", fill=22.0)
    ds["t2m"].attrs = {"units": "degree_Celsius"}
    ds["rh"] = xr.full_like(ds["t2m"], 70.0)
    ds["rh"].attrs = {"units": "%"}
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "hi.zarr"
    run_skill(
        mod.derive,
        "-i",
        str(inp),
        "-f",
        "heat_index",
        "--arg",
        "mask_undefined=false",
        "-o",
        str(out),
    )
    hi = xr.open_zarr(out, consolidated=True)["heat_index"]
    np.testing.assert_allclose(hi.values, 22.0, atol=1.0)


def test_potential_temperature_uses_level_coordinate(mod, tmp_path):
    levels = np.array([1000.0, 850.0, 500.0])
    ds = make_forecast(n_step=1, name="t", fill=0.0)
    ds = ds.expand_dims(level=levels).copy()
    ds["level"].attrs = {"units": "hPa", "standard_name": "air_pressure"}
    ds["t"] = xr.full_like(ds["t"], 280.0)
    ds["t"].attrs = {"units": "K", "standard_name": "air_temperature"}
    inp = write_zarr(ds, tmp_path / "lev.zarr")
    out = tmp_path / "theta.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "potential_temperature", "-o", str(out))
    theta = xr.open_zarr(out, consolidated=True)["theta"]
    assert "level" in theta.dims
    assert theta.attrs["units"] == "K"
    expected = 280.0 * (1000.0 / levels) ** 0.2857
    np.testing.assert_allclose(theta.isel(step=0, latitude=0, longitude=0), expected, rtol=1e-3)


def test_pressure_as_constant(mod, tmp_path):
    ds = make_forecast(n_step=1, name="t", fill=280.0)
    ds["t"].attrs = {"units": "K"}
    inp = write_zarr(ds, tmp_path / "t850.zarr")
    out = tmp_path / "theta.zarr"
    run_skill(
        mod.derive,
        "-i",
        str(inp),
        "-f",
        "potential_temperature",
        "--arg",
        "pressure=850 hPa",
        "--name",
        "theta850",
        "-o",
        str(out),
    )
    theta = xr.open_zarr(out, consolidated=True)["theta850"]
    np.testing.assert_allclose(theta.values, 280.0 * (1000 / 850) ** 0.2857, rtol=1e-3)


def _wind_grid(u_of_lat):
    lats = tuple(float(x) for x in np.arange(-4.0, 4.1, 1.0))
    lons = tuple(float(x) for x in np.arange(30.0, 38.1, 1.0))
    ds = make_forecast(n_step=2, members=2, lats=lats, lons=lons, name="u", fill=0.0)
    ds["u"] = xr.zeros_like(ds["u"]) + u_of_lat(ds["latitude"])
    ds["u"].attrs = {"units": "m s-1", "standard_name": "eastward_wind"}
    ds["v"] = xr.zeros_like(ds["u"])
    ds["v"].attrs = {"units": "m s-1", "standard_name": "northward_wind"}
    return ds


def test_vorticity_of_meridional_shear(mod, tmp_path):
    # u = A * latitude (degrees) gives relative vorticity ~ -A / (111 km per degree).
    a = 2.0
    inp = write_zarr(_wind_grid(lambda lat: a * lat), tmp_path / "uv.zarr")
    out = tmp_path / "vort.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "vorticity", "-o", str(out))
    vort = xr.open_zarr(out, consolidated=True)["vorticity"]
    assert vort.dims == ("number", "step", "latitude", "longitude")
    assert vort.attrs["units"] == "s-1"
    centre = float(vort.isel(number=0, step=0).sel(latitude=0.0, longitude=34.0))
    assert centre == pytest.approx(-a / 111_195.0, rel=0.02)


def test_divergence_of_uniform_wind_is_zero(mod, tmp_path):
    inp = write_zarr(_wind_grid(lambda lat: 5.0 + 0 * lat), tmp_path / "uv.zarr")
    out = tmp_path / "div.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "divergence", "-o", str(out))
    div = xr.open_zarr(out, consolidated=True)["divergence"]
    np.testing.assert_allclose(div.values, 0.0, atol=1e-9)


def test_wind_components_two_outputs_and_keep_inputs(mod, tmp_path):
    ds = make_point_obs(n_time=1, n_points=2, name="wind_speed", fill=10.0)
    ds["wind_speed"].attrs = {"units": "m s-1"}
    ds["wind_direction"] = xr.full_like(ds["wind_speed"], 90.0)  # from the east
    ds["wind_direction"].attrs = {"units": "degree"}
    inp = write_zarr(ds, tmp_path / "st.zarr")
    out = tmp_path / "uv.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "wind_components", "--keep-inputs", "-o", str(out))
    result = xr.open_zarr(out, consolidated=True)
    assert {"u", "v", "wind_speed", "wind_direction"} <= set(result.data_vars)
    np.testing.assert_allclose(result["u"].values, -10.0, atol=1e-6)
    np.testing.assert_allclose(result["v"].values, 0.0, atol=1e-6)


def test_two_inputs_are_merged(mod, tmp_path):
    full = _surface_forecast()
    a = write_zarr(full[["t2m"]], tmp_path / "t.zarr")
    b = write_zarr(full[["d2m"]], tmp_path / "td.zarr")
    out = tmp_path / "rh.zarr"
    run_skill(
        mod.derive,
        "-i",
        str(a),
        "-i",
        str(b),
        "-f",
        "relative_humidity_from_dewpoint",
        "-o",
        str(out),
    )
    assert "relative_humidity" in xr.open_zarr(out, consolidated=True)


def test_ambiguous_variables_exit_2(mod, tmp_path, capsys):
    ds = _surface_forecast()
    ds["t"] = ds["t2m"].copy()
    inp = write_zarr(ds, tmp_path / "fc.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.derive,
            "-i",
            str(inp),
            "-f",
            "saturation_vapor_pressure",
            "-o",
            str(tmp_path / "out.zarr"),
        )
    assert excinfo.value.code == 2
    assert "--arg temperature=VAR" in capsys.readouterr().err


def test_grid_function_on_stations_exit_2(mod, tmp_path, capsys):
    ds = make_point_obs(name="u")
    ds["u"].attrs = {"units": "m s-1"}
    ds["v"] = ds["u"].copy()
    inp = write_zarr(ds, tmp_path / "st.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.derive, "-i", str(inp), "-f", "vorticity", "-o", str(tmp_path / "o.zarr"))
    assert excinfo.value.code == 2
    assert "needs a lat/lon grid" in capsys.readouterr().err


def test_unknown_param_exit_2(mod, tmp_path, capsys):
    inp = write_zarr(_surface_forecast(), tmp_path / "fc.zarr")
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.derive,
            "-i",
            str(inp),
            "-f",
            "heat_index",
            "--arg",
            "speed=t2m",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert excinfo.value.code == 2
    assert "not a parameter" in capsys.readouterr().err


def test_list_functions_matches_metpy(mod, capsys):
    import metpy.calc as mpcalc

    run_skill(mod.derive, "--list-functions")
    listed = capsys.readouterr().out.splitlines()[1:]
    assert len(listed) == len(mod.CALCS)
    missing = [name for name in mod.CALCS if not hasattr(mpcalc, name)]
    assert not missing


def test_advection_of_zonal_gradient(mod, tmp_path):
    # T rising 1 K per degree eastward, carried by a 10 m/s westerly: -u dT/dx < 0 (cooling).
    ds = _wind_grid(lambda lat: 10.0 + 0 * lat)
    ds["t"] = xr.zeros_like(ds["u"]) + 280.0 + (ds["longitude"] - 34.0)
    ds["t"].attrs = {"units": "K"}
    inp = write_zarr(ds, tmp_path / "tuv.zarr")
    out = tmp_path / "adv.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "advection", "--arg", "scalar=t", "-o", str(out))
    adv = xr.open_zarr(out, consolidated=True)["t_advection"]
    assert adv.attrs["units"] == "K s-1"
    centre = float(adv.isel(number=0, step=0).sel(latitude=0.0, longitude=34.0))
    assert centre == pytest.approx(-10.0 / 111_320.0, rel=0.02)


def test_smoothing_names_output_after_input(mod, tmp_path):
    ds = _wind_grid(lambda lat: lat)
    inp = write_zarr(ds[["u"]], tmp_path / "u.zarr")
    out = tmp_path / "smooth.zarr"
    run_skill(mod.derive, "-i", str(inp), "-f", "smooth_gaussian", "--arg", "n=3", "-o", str(out))
    result = xr.open_zarr(out, consolidated=True)
    assert set(result.data_vars) == {"u_smoothed"}
    assert result["u_smoothed"].dims == ("number", "step", "latitude", "longitude")
