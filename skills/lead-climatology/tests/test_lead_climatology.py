"""Correctness tests for lead-climatology."""

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr


@pytest.fixture(scope="module")
def lead_climatology():
    return load_skill("lead-climatology", "lead_climatology").lead_climatology


def _obs(start, days):
    """Daily obs whose value is the day's ordinal (days since 2000-01-01)."""
    ds = make_gridded(n_time=days, start=start)
    ordinal = (ds["time"].values - np.datetime64("2000-01-01")) / np.timedelta64(1, "D")
    ds["precip"] = ds["precip"] * 0 + ordinal[:, None, None]
    return ds


def _like(tmp_path, init="2026-10-22", n_step=3):
    fc = make_forecast(
        n_step=n_step, init=init, lats=(1.0, 2.0, 3.0), lons=(10.0, 11.0, 12.0, 13.0)
    )
    # Steps from 0 days (start-of-day labels, as dynamical-fetch writes them).
    fc = fc.assign_coords(step=fc["step"].values - fc["step"].values[0])
    return str(write_zarr(fc, tmp_path / "like.zarr"))


def test_lays_each_year_on_the_lead_days(tmp_path, lead_climatology):
    a = write_zarr(_obs("2020-10-20", 10), tmp_path / "a.zarr")
    b = write_zarr(_obs("2021-10-20", 10), tmp_path / "b.zarr")
    out = tmp_path / "out.zarr"
    run_skill(
        lead_climatology, "-i", str(a), "-i", str(b), "--like", _like(tmp_path),
        "--years", "2020-2021", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["precip"].dims == ("year", "step", "latitude", "longitude")
    assert list(ds["year"].values) == [2020, 2021]
    first = (np.datetime64("2020-10-22") - np.datetime64("2000-01-01")) / np.timedelta64(1, "D")
    assert list(ds["precip"].isel(year=0, latitude=0, longitude=0).values) == [
        first,
        first + 1,
        first + 2,
    ]
    assert str(ds["init_time"].values[1])[:10] == "2021-10-22"


def test_drops_years_with_missing_days(tmp_path, lead_climatology, capsys):
    full = write_zarr(_obs("2019-10-20", 40 + 365 * 2), tmp_path / "full.zarr")
    short = write_zarr(_obs("2022-10-20", 3), tmp_path / "short.zarr")
    out = tmp_path / "out.zarr"
    run_skill(
        lead_climatology, "-i", str(full), "-i", str(short), "--like", _like(tmp_path),
        "--years", "2019-2022", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["year"].values) == [2019, 2020, 2021]
    assert "2022 (2 missing days)" in capsys.readouterr().err


def test_too_few_years_errors(tmp_path, lead_climatology):
    a = write_zarr(_obs("2020-10-20", 10), tmp_path / "a.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            lead_climatology, "-i", str(a), "--like", _like(tmp_path), "--years", "2020-2021",
            "-o", str(tmp_path / "out.zarr"),
        )  # fmt: skip
    assert exc.value.code == 1


def test_sub_daily_input_errors(tmp_path, lead_climatology, capsys):
    ds = _obs("2020-10-20", 10)
    ds = ds.assign_coords(
        time=ds["time"].values.astype("datetime64[h]")[0] + np.arange(10) * np.timedelta64(6, "h")
    )
    a = write_zarr(ds, tmp_path / "a.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            lead_climatology, "-i", str(a), "--like", _like(tmp_path), "--years", "2020",
            "-o", str(tmp_path / "out.zarr"),
        )  # fmt: skip
    assert exc.value.code == 2
    assert "sub-daily" in capsys.readouterr().err


def test_feb_29_init_uses_feb_28(tmp_path, lead_climatology):
    a = write_zarr(_obs("2023-02-20", 20), tmp_path / "a.zarr")
    b = write_zarr(_obs("2024-02-20", 20), tmp_path / "b.zarr")
    out = tmp_path / "out.zarr"
    run_skill(
        lead_climatology, "-i", str(a), "-i", str(b), "--like", _like(tmp_path, init="2028-02-29"),
        "--years", "2023-2024", "-o", str(out),
    )  # fmt: skip
    inits = [str(t)[:10] for t in xr.open_zarr(out, consolidated=True)["init_time"].values]
    assert inits == ["2023-02-28", "2024-02-29"]
