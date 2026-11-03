"""Tests for metar-fetch with the IEM download replaced by fixed reports."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import load_skill, run_skill

CSV = """station,valid,lon,lat,elevation,metar
HKMO,2026-04-15 00:00,39.6167,-4.0333,55.00,HKMO 150000Z 22003KT 9999 FEW019 26/25 Q1009 NOSIG
HKMO,2026-04-15 00:30,39.6167,-4.0333,55.00,HKMO 150030Z 22004KT 9999 SCT019 26/25 Q1009 NOSIG
HKMO,2026-04-15 01:00,39.6167,-4.0333,55.00,HKMO 150100Z 22004KT 9999 SCT019 26/24 Q1009 NOSIG
HKMO,2026-04-15 01:15,39.6167,-4.0333,55.00,HKMO 150115Z 13014G25KT 5000 TSRA FEW018CB SCT019 25/23 Q1010
HKMO,2026-04-15 02:00,39.6167,-4.0333,55.00,HKMO 150200Z VRB02KT 9999 FEW019 25/24 Q1010 NOSIG
"""


@pytest.fixture
def mod(monkeypatch):
    module = load_skill("metar-fetch", "metar_fetch")
    monkeypatch.setattr(
        module, "_download", lambda station, start, end: pd.read_csv(io.StringIO(CSV))
    )
    return module


def test_hourly_slots_values_and_flags(mod, tmp_path):
    out = tmp_path / "metar.zarr"
    run_skill(
        mod.fetch,
        "--start-time",
        "2026-04-15",
        "--end-time",
        "2026-04-15",
        "--station",
        "hkmo",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert ds.attrs["featureType"] == "timeSeries"
    assert ds.attrs["metpy_version"]
    m = ds.sel(station_id="HKMO")
    assert float(m["latitude"]) == pytest.approx(-4.0333)
    one = m.sel(time="2026-04-15T01:00")
    # The 01:00 routine report fills the slot; the 01:15 special adds its weather flags.
    assert float(one["air_temperature"]) == 26.0
    assert float(one["dew_point_temperature"]) == 24.0
    assert float(one["qnh"]) == pytest.approx(1009.0, abs=0.2)
    assert float(one["thunderstorm"]) == 1.0
    assert float(one["cumulonimbus"]) == 1.0
    assert float(one["precipitation"]) == 1.0
    assert str(one["metar"].values).startswith("HKMO 150100Z")
    zero = m.sel(time="2026-04-15T00:00")
    assert float(zero["thunderstorm"]) == 0.0
    two = m.sel(time="2026-04-15T02:00")
    assert np.isnan(float(two["wind_from_direction"]))  # VRB
    assert ds["wind_speed"].attrs["units"] == "knot"
    assert ds["qnh"].attrs["units"] == "hPa"


def test_half_hourly_slots_keep_the_special(mod, tmp_path):
    out = tmp_path / "metar.zarr"
    run_skill(
        mod.fetch,
        "--start-time",
        "2026-04-15",
        "--end-time",
        "2026-04-15",
        "--station",
        "HKMO",
        "--interval",
        "30min",
        "-o",
        str(out),
    )
    m = xr.open_zarr(out, consolidated=True).sel(station_id="HKMO")
    special = m.sel(time="2026-04-15T01:30")  # 01:15 rounds into the 01:30 slot
    assert str(special["present_weather"].values) == "TSRA"
    assert float(special["wind_speed_of_gust"]) == 25.0
    assert float(special["visibility"]) == 5000.0


def test_needs_stations(mod, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch,
            "--start-time",
            "2026-04-15",
            "--end-time",
            "2026-04-15",
            "-o",
            str(tmp_path / "m.zarr"),
        )
    assert excinfo.value.code == 2
    assert "--station" in capsys.readouterr().err


def test_no_reports_exit_1(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "_download", lambda station, start, end: pd.DataFrame())
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch,
            "--start-time",
            "2026-04-15",
            "--end-time",
            "2026-04-15",
            "--station",
            "XXXX",
            "-o",
            str(tmp_path / "m.zarr"),
        )
    assert excinfo.value.code == 1
    assert "no METARs" in capsys.readouterr().err
