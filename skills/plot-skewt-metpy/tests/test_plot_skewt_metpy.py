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


def _station_soundings(n_time=6, sp=957.1):
    """sounding-fetch's layout: (station_id, time, level), irregular times with time_bounds."""
    times = np.array(
        ["2026-10-27T03", "2026-10-28T03", "2026-10-29T03", "2026-10-30T15", "2026-10-31T03",
         "2026-11-01T03"][:n_time], dtype="datetime64[ns]",
    )  # fmt: skip
    shape = (1, n_time, LEVELS.size)
    lev = np.broadcast_to(T_C - 10, shape)
    ds = xr.Dataset(
        {
            "t": (("station_id", "time", "level"), lev.copy(), {"units": "degree_Celsius"}),
            "dewpoint": (("station_id", "time", "level"), lev - 4, {"units": "degree_Celsius"}),
            "u": (("station_id", "time", "level"), np.full(shape, 5.0), {"units": "m s-1"}),
            "v": (("station_id", "time", "level"), np.full(shape, 2.0), {"units": "m s-1"}),
            "gh": (("station_id", "time", "level"), np.broadcast_to(Z_M, shape).copy(),
                   {"units": "m"}),
            "sp": (("station_id", "time"), np.full((1, n_time), sp), {"units": "hPa"}),
            "t2m": (("station_id", "time"), np.full((1, n_time), 6.0), {"units": "degree_Celsius"}),
            "d2m": (("station_id", "time"), np.full((1, n_time), 1.9), {"units": "degree_Celsius"}),
            "time_bounds": (("time", "nv"), np.stack([times, times + np.timedelta64(1, "h")], 1)),
        },
        coords={
            "station_id": ["AUM00011120"],
            "time": ("time", times, {"standard_name": "time", "bounds": "time_bounds"}),
            "level": ("level", LEVELS, {"units": "hPa", "standard_name": "air_pressure"}),
        },
    )  # fmt: skip
    return ds


def _model_profile(sp=874.4):
    ds = _profiles(members=1, winds=True).isel(number=0)
    base = ds["t"].isel(level=0, drop=True)
    ds["q"] = _field(ds["t"], 0.004, units="kg kg-1", standard_name="specific_humidity")
    ds = ds.drop_vars("r")
    ds["sp"] = _field(base, sp, units="hPa", standard_name="surface_air_pressure")
    ds["t2m"] = _field(base, 1.4, units="degree_Celsius")
    ds["d2m"] = _field(base, -1.5, units="degree_Celsius")
    return ds


def _stats(err):
    """[(label, {index: value})] from the per-profile stderr lines, in order."""
    out = []
    for line in err.splitlines():
        if "sbcape=" not in line:
            continue
        head, _, rest = line.removeprefix("plot-skewt-metpy: ").rpartition("sbcape=")
        label = head.rstrip(": ").strip() or "-"
        out.append((label, dict(kv.split("=") for kv in ("sbcape=" + rest).split())))
    return out


def test_time_bounds_need_no_isel(mod, tmp_path):
    inp = write_zarr(_station_soundings(), tmp_path / "snd.zarr")
    out = tmp_path / "skewt.png"
    run_skill(
        mod.plot_skewt, "-i", str(inp), "--station", "AUM00011120", "--isel", "time=5",
        "-o", str(out),
    )  # fmt: skip
    assert out.read_bytes()[:8] == PNG


def test_sounding_indices_keep_time_bounds(tmp_path):
    indices = load_skill("sounding-indices-metpy", "sounding_indices_metpy")
    inp = write_zarr(_station_soundings(), tmp_path / "snd.zarr")
    out = tmp_path / "idx.zarr"
    run_skill(indices.sounding_indices, "-i", str(inp), "--index", "sbcape", "-o", str(out))
    res = xr.open_zarr(out, consolidated=True)
    assert res["sbcape"].dims == ("station_id", "time")
    bounds = res["time"].attrs.get("bounds")
    assert bounds is None or bounds in res.variables


def _two_profiles(tmp_path):
    snd = write_zarr(_station_soundings(), tmp_path / "snd.zarr")
    hres = write_zarr(_model_profile(), tmp_path / "hres.zarr")
    balloon = f"label=Balloon 11120;input={snd};station=AUM00011120;isel=time=5"
    model = f"label=ECMWF HRES +3 h;input={hres};lat=1.2;lon=10.9;isel=step=1"
    return snd, hres, balloon, model


def test_overlay_matches_single_runs(mod, tmp_path, capsys):
    snd, hres, balloon, model = _two_profiles(tmp_path)
    out = tmp_path / "overlay.png"
    run_skill(mod.plot_skewt, "--profile", balloon, "--profile", model, "-o", str(out))
    assert out.read_bytes()[:8] == PNG
    both = dict(_stats(capsys.readouterr().err))
    assert set(both) == {"Balloon 11120", "ECMWF HRES +3 h"}
    # Each profile on its own surface: the balloon from 957.1 hPa, the model from 874.4 hPa.
    assert float(both["Balloon 11120"]["bottom"]) == pytest.approx(957.1)
    assert float(both["ECMWF HRES +3 h"]["bottom"]) == pytest.approx(874.4)

    run_skill(
        mod.plot_skewt, "-i", str(snd), "--station", "AUM00011120", "--isel", "time=5",
        "-o", str(tmp_path / "a.png"),
    )  # fmt: skip
    run_skill(
        mod.plot_skewt, "-i", str(hres), "--lat", "1.2", "--lon", "10.9", "--isel", "step=1",
        "-o", str(tmp_path / "b.png"),
    )  # fmt: skip
    singles = [values for _, values in _stats(capsys.readouterr().err)]
    assert both["Balloon 11120"] == singles[0]
    assert both["ECMWF HRES +3 h"] == singles[1]

    from weather_skills_core import provenance

    history = provenance.load_figure_history(out)
    text = str(history)
    assert "snd.zarr" in text and "hres.zarr" in text


def test_profiles_file(mod, tmp_path, capsys):
    import json

    snd, hres, _, _ = _two_profiles(tmp_path)
    spec = tmp_path / "profiles.json"
    spec.write_text(
        json.dumps(
            [
                {"label": "Balloon", "input": str(snd), "station": "AUM00011120",
                 "isel": {"time": 5}},
                {"label": "HRES", "input": [str(hres)], "lat": 1.2, "lon": 10.9,
                 "isel": ["step=1"], "var": {"specific_humidity": "q"}},
            ]
        )
    )  # fmt: skip
    out = tmp_path / "overlay.png"
    run_skill(mod.plot_skewt, "--profiles-file", str(spec), "-o", str(out))
    assert out.read_bytes()[:8] == PNG
    assert [label for label, _ in _stats(capsys.readouterr().err)] == ["Balloon", "HRES"]


def test_input_and_profile_exclusive(mod, tmp_path, capsys):
    snd, _, balloon, _ = _two_profiles(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.plot_skewt, "-i", str(snd), "--profile", balloon, "-o", str(tmp_path / "x.png")
        )
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.plot_skewt, "--profile", balloon, "--isel", "time=1",
            "-o", str(tmp_path / "x.png"),
        )  # fmt: skip
    assert excinfo.value.code == 2


def test_styling_options_and_layout(mod, tmp_path):
    import matplotlib.image as mpimg

    _, _, balloon, model = _two_profiles(tmp_path)
    out = tmp_path / "styled.png"
    run_skill(
        mod.plot_skewt, "--profile", balloon, "--profile", model,
        "--shade", "all", "--barbs", "all", "--shade-alpha", "0.6", "--cape-color", "orange",
        "--pressure-range", "960/550", "--temperature-range", "-15/10",
        "--fiducial-style", "bold", "--fiducial-labels", "--mixing-lines-top", "300",
        "--freezing-isotherm", "--inset", "on", "--dpi", "80", "-o", str(out),
    )  # fmt: skip
    assert out.read_bytes()[:8] == PNG
    # Without the hodograph the figure is about square, not the wide hodograph layout.
    flat = tmp_path / "flat.png"
    run_skill(
        mod.plot_skewt, "--profile", balloon, "--profile", model, "--no-hodograph",
        "-o", str(flat),
    )  # fmt: skip
    h, w = mpimg.imread(flat).shape[:2]
    assert 0.85 < w / h < 1.2
