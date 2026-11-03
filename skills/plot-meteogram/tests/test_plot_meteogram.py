"""Tests for plot-meteogram on a small synthetic 6-hourly ensemble."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill, write_zarr
from weather_skills_core.provenance import load_figure_history


@pytest.fixture(scope="module")
def mod():
    return load_skill("plot-meteogram", "plot_meteogram")


def make_meteogram_dataset(variables=("t2m", "tp", "u10", "v10", "tcc"), members=6, days=5):
    """Forecast with a diurnal 2 m temperature, showers, wind and cloud near Nairobi."""
    rng = np.random.default_rng(0)
    steps = np.arange(0, days * 24 + 1, 6).astype("timedelta64[h]").astype("timedelta64[ns]")
    hours = steps / np.timedelta64(1, "h")
    shape = (members, len(steps), 2, 2)
    diurnal = 18 + 7 * np.sin(2 * np.pi * (hours - 9) / 24)
    fields = {
        "t2m": (diurnal[None, :, None, None] + rng.normal(0, 1.5, shape), "degree_Celsius"),
        "tp": (rng.gamma(0.6, 6.0, shape), "mm day-1"),
        "u10": (rng.normal(6, 3, shape), "m s-1"),
        "v10": (rng.normal(3, 3, shape), "m s-1"),
        "fg10": (rng.normal(12, 3, shape), "m s-1"),
        "tcc": (rng.uniform(0, 100, shape), "%"),
    }
    ds = xr.Dataset(
        {
            name: (("number", "step", "latitude", "longitude"), fields[name][0])
            for name in variables
        },
        coords={
            "time": np.datetime64("2026-11-01T00", "ns"),
            "step": steps,
            "number": np.arange(members),
            "latitude": [-1.0, -1.5],
            "longitude": [36.5, 37.0],
        },
    )
    for name in variables:
        ds[name].attrs["units"] = fields[name][1]
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    ds["step"].attrs.update(standard_name="forecast_period")
    ds["time"].attrs.update(standard_name="forecast_reference_time", axis="T")
    return ds


def _run(mod, tmp_path, ds, *extra):
    inp = write_zarr(ds, tmp_path / "fc.zarr")
    out = tmp_path / "meteogram.png"
    run_skill(
        mod.plot_meteogram,
        "-i",
        str(inp),
        "--lat",
        "-1.29",
        "--lon",
        "36.82",
        "-o",
        str(out),
        *extra,
    )
    return out


def test_all_panels(mod, tmp_path, capsys):
    out = _run(mod, tmp_path, make_meteogram_dataset(), "--label", "Nairobi")
    assert out.stat().st_size > 0
    assert load_figure_history(out)[-1]["skill"] == "plot-meteogram"
    err = capsys.readouterr().err
    assert "grid point -1.50, 37.00" in err
    assert "no cloud" not in err and "no rain" not in err


def test_gust_and_options(mod, tmp_path):
    ds = make_meteogram_dataset(("t2m", "tp", "u10", "v10", "fg10"))
    out = _run(
        mod,
        tmp_path,
        ds,
        "--utc",
        "--max-lead",
        "3D",
        "--precip-interval",
        "12h",
        "--precip-thresholds",
        "0.5,2,10",
        "--title",
        "test",
    )
    assert out.exists()


def test_fields_from_two_inputs(mod, tmp_path):
    a = write_zarr(make_meteogram_dataset(("t2m",)), tmp_path / "t.zarr")
    b = write_zarr(make_meteogram_dataset(("tp", "tcc")), tmp_path / "p.zarr")
    out = tmp_path / "two.png"
    run_skill(
        mod.plot_meteogram,
        *("-i", str(a), "-i", str(b), "--lat", "-1.3", "--lon", "36.8", "-o", str(out)),
    )
    assert out.exists()


def test_missing_fields_drop_panels(mod, tmp_path, capsys):
    out = _run(mod, tmp_path, make_meteogram_dataset(("t2m",)))
    assert out.exists()
    err = capsys.readouterr().err
    assert "no precipitation" in err and "no cloud cover" in err


def test_local_time_and_sun(mod):
    import pandas as pd

    tz, name = mod._timezone(-1.29, 36.82, utc=False)
    assert name == "Africa/Nairobi"
    rise, sset = mod._sun_times(-1.29, 36.82, pd.Timestamp("2026-11-03"))
    local = mod._to_local(pd.DatetimeIndex([rise, sset]), tz)
    assert local[0].hour == 6 and local[1].hour == 18  # ~06:20 and ~18:30 EAT
    assert mod._sun_times(80.0, 15.0, pd.Timestamp("2026-12-21")) is None  # polar night


def test_needs_temperature(mod, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, make_meteogram_dataset(("tp",)))
    assert exc.value.code == 2


def test_rejects_precip_amounts(mod, tmp_path):
    ds = make_meteogram_dataset(("t2m", "tp"))
    ds["tp"].attrs["units"] = "mm"
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp_path, ds)
    assert exc.value.code == 2
