"""Tests for subc-climatology-fetch (mocked remote opens; no live network)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill

PCTS = ("p05", "p20", "p33", "p67", "p80", "p95")


def _fake_clim(var: str, *, n_lead: int = 4, fill: float = 2.0, descending_lat: bool = True):
    """CHC-like climatology file: M/L/Y/X dims, ens_mean + percentiles + window fields."""
    lats = np.arange(90.0, -91.0, -30.0) if descending_lat else np.arange(-90.0, 91.0, 30.0)
    lons = np.arange(0.0, 360.0, 30.0)
    lead = np.arange(n_lead) + 0.5
    shape3 = (n_lead, lats.size, lons.size)
    shape2 = (lats.size, lons.size)
    daily_units = "mm/day" if var == "pr" else "K"
    std = "precipitation_flux" if var == "pr" else "air_temperature"
    data = {
        var: (("M", "L", "Y", "X"), np.full((2, *shape3), fill, np.float32)),
        f"{var}_ens_mean": (("L", "Y", "X"), np.full(shape3, fill, np.float32)),
        f"{var}_n_draws": (("L", "Y", "X"), np.full(shape3, 300, np.int32)),
    }
    for i, p in enumerate(PCTS):
        data[f"{var}_ens_{p}"] = (("L", "Y", "X"), np.full(shape3, fill + i, np.float32))
    kind = "acc" if var == "pr" else "avg"
    for n in (7, 15, 30):
        scale = n if var == "pr" else 1
        data[f"{var}_{kind}{n}_ens_mean"] = (("Y", "X"), np.full(shape2, fill * scale, np.float32))
        for i, p in enumerate(PCTS):
            data[f"{var}_{kind}{n}_{p}"] = (
                ("Y", "X"),
                np.full(shape2, fill * scale + i, np.float32),
            )
    ds = xr.Dataset(
        data,
        coords={
            "M": [1, 2],
            "L": lead.astype(np.float32),
            "Y": lats.astype(np.float32),
            "X": lons.astype(np.float32),
        },
        attrs={"title": "SubC daily climatology", "years": "1999-2016", "window_days": 10},
    )
    for name in ds.data_vars:
        window = f"_{kind}" in name
        ds[name].attrs["units"] = ("mm" if var == "pr" else "K") if window else daily_units
        ds[name].attrs["standard_name"] = std
    return ds


@pytest.fixture
def mod():
    return load_skill("subc-climatology-fetch", "fetch")


def _serve(mod, monkeypatch, files: dict[str, xr.Dataset]):
    def fake_open(url):
        for var, ds in files.items():
            if f"/{var}/" in url:
                return ds
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(mod, "_open_remote", fake_open)


def test_file_url(mod):
    url = mod._file_url("NCEP-CFSv2", "pr", date(2026, 1, 1))
    assert url == (
        "https://data.chc.ucsb.edu/experimental/SubC/climatology/1999_2016"
        "/pr/NCEP-CFSv2/01/pr_NCEP-CFSv2_0101.daily.nc"
    )
    assert mod._file_url("ECCC-GEPS8", "tas", date(2024, 2, 29)).endswith(
        "/tas/ECCC-GEPS8/02/tas_ECCC-GEPS8_0229.daily.nc"
    )


def test_source_names(mod):
    assert mod._source_names("pr", None, False, "mean") == ["pr_ens_mean"]
    assert mod._source_names("pr", 7, True, "mean") == ["pr_acc7_ens_mean"]
    assert mod._source_names("tas", 30, True, "mean") == ["tas_avg30_ens_mean"]
    assert mod._source_names("ts", None, False, "quantiles")[0] == "ts_ens_p05"
    assert mod._source_names("pr", 15, True, "quantiles")[-1] == "pr_acc15_p95"
    # A window CHC does not precompute is built from the daily means.
    assert mod._source_names("pr", 10, False, "mean") == ["pr_ens_mean"]


def test_plan(mod):
    assert mod._plan("daily", 0, "quantiles") == (None, 0, False)
    assert mod._plan("7d", 0, "mean") == (7, 0, True)
    assert mod._plan("7d", 7, "mean") == (7, 7, False)
    assert mod._plan("10d", 0, "mean") == (10, 0, False)
    assert mod._plan("14 days", 14, "mean") == (14, 14, False)
    from weather_skills_core import UsageError

    for args in (
        ("10d", 0, "quantiles"),
        ("7d", 7, "quantiles"),
        ("daily", 3, "mean"),
        ("weekly", 0, "mean"),
        ("0d", 0, "mean"),
        ("7d", -1, "mean"),
    ):
        with pytest.raises(UsageError):
            mod._plan(*args)


def test_daily_mean(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {"pr": _fake_clim("pr"), "tas": _fake_clim("tas", fill=290.0)})
    out = tmp_path / "out.zarr"
    run_skill(
        mod.fetch, "--date", "2026-11-09", "--model", "NCEP-CFSv2",
        "-v", "pr", "-v", "tas", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert set(ds.data_vars) == {"pr", "tas"}
    assert ds["pr"].dims == ("step", "latitude", "longitude")
    steps = ds["step"].values / np.timedelta64(1, "D")
    assert list(steps) == [0, 1, 2, 3]
    assert ds["time"].values == np.datetime64("2026-11-09", "ns")
    assert np.all(np.diff(ds["latitude"].values) > 0)
    assert ds["longitude"].min() >= -180 and ds["longitude"].max() < 180
    assert ds["pr"].attrs["units"] == "mm day-1"
    assert ds["pr"].attrs["standard_name"] == "lwe_precipitation_rate"
    assert ds["tas"].attrs["units"] == "degree_Celsius"
    assert float(ds["tas"].mean()) == pytest.approx(290.0 - 273.15, abs=1e-3)
    assert float(ds["pr"].mean()) == pytest.approx(2.0)
    assert ds.attrs["model"] == "NCEP-CFSv2"
    assert ds.attrs["calendar_day"] == "11-09"
    assert ds.attrs["weather_skills_source"] == "chc-subc-climatology"


def test_window_quantiles(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {"pr": _fake_clim("pr", descending_lat=False)})
    out = tmp_path / "q.zarr"
    run_skill(
        mod.fetch, "--date", "2026-01-01", "--model", "ECCC-GEPS8", "-v", "pr",
        "--outlook", "7d", "--stat", "quantiles", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["pr"].dims == ("quantile", "step", "latitude", "longitude")
    assert list(ds["step"].values) == [np.timedelta64(0, "ns")]
    np.testing.assert_allclose(ds["quantile"].values, [0.05, 0.2, 1 / 3, 2 / 3, 0.8, 0.95])
    # fill*7 + percentile index
    np.testing.assert_allclose(ds["pr"].mean(["step", "latitude", "longitude"]), 14 + np.arange(6))
    assert ds["pr"].attrs["units"] == "mm"
    assert ds["pr"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"
    assert ds["pr"].attrs["aggregation_period"] == "7 day"
    assert ds["pr"].attrs["cell_methods"] == "time: sum"


def test_bbox_keeps_overlapping_cells(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {"pr": _fake_clim("pr")})
    out = tmp_path / "bbox.zarr"
    run_skill(
        mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
        "--bbox", "5/34/-5/42", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["longitude"].values) == [30.0]
    assert list(ds["latitude"].values) == [0.0]


def test_variable_not_published_for_model(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--date", "2026-01-01", "--model", "GMAO-GEOS_V3", "-v", "tdps",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 2


def _http_error(status):
    class _Resp(Exception):
        def __init__(self):
            super().__init__(f"{status}, message='HTTP {status}'")
            self.status = status

    try:
        raise _Resp()
    except _Resp as cause:
        err = FileNotFoundError("https://data.chc.ucsb.edu/...")
        err.__cause__ = cause
        return err


def test_404_is_data_error(mod, monkeypatch, tmp_path):
    def fake_open(url):
        raise _http_error(404)

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1


def test_429_retries_then_succeeds(mod, monkeypatch, tmp_path):
    calls = []

    def fake_open(url):
        calls.append(url)
        if len(calls) < 3:
            raise _http_error(429)
        return _fake_clim("pr")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    monkeypatch.setattr(mod._time, "sleep", lambda s: None)
    out = tmp_path / "r.zarr"
    run_skill(
        mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
        "-o", str(out),
    )  # fmt: skip
    assert len(calls) == 3
    assert "pr" in xr.open_zarr(out, consolidated=True)


def test_429_gives_up_with_actionable_error(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "_open_remote", lambda url: (_ for _ in ()).throw(_http_error(429)))
    monkeypatch.setattr(mod._time, "sleep", lambda s: None)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1
    assert "rate-limiting" in capsys.readouterr().err


def _ramp_clim(var: str, n_lead: int = 30):
    """Daily ens_mean equal to the lead-day index (0, 1, 2, ...), so window sums are checkable."""
    ds = _fake_clim(var, n_lead=n_lead)
    ramp = xr.DataArray(np.arange(n_lead, dtype=np.float32), dims="L")
    ds[f"{var}_ens_mean"] = (ds[f"{var}_ens_mean"] * 0 + ramp).assign_attrs(
        ds[f"{var}_ens_mean"].attrs
    )
    return ds


def test_custom_window_from_daily_means(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {"pr": _ramp_clim("pr"), "tas": _ramp_clim("tas")})
    out = tmp_path / "w.zarr"
    run_skill(
        mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr", "-v", "tas",
        "--outlook", "14d", "--window-start", "14", "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["step"].values) == [np.timedelta64(14, "D").astype("timedelta64[ns]")]
    # pr: sum of leads 14..27 (rate x 1 day) -> mm; tas: mean of leads 14..27, in K -> degC
    assert float(ds["pr"].mean()) == pytest.approx(sum(range(14, 28)))
    assert ds["pr"].attrs["units"] == "mm"
    assert ds["pr"].attrs["aggregation_period"] == "14 day"
    assert ds["pr"].attrs["cell_methods"] == "time: sum"
    assert ds["pr"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"
    assert float(ds["tas"].mean()) == pytest.approx(np.mean(range(14, 28)) - 273.15, abs=1e-3)
    assert ds["tas"].attrs["cell_methods"] == "time: mean"
    assert ds.attrs["outlook"] == "14d"
    assert ds.attrs["window_source"] == "computed from daily ensemble means"


def test_window_past_last_lead_is_data_error(mod, monkeypatch, tmp_path, capsys):
    _serve(mod, monkeypatch, {"pr": _ramp_clim("pr", n_lead=30)})
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
            "--outlook", "10d", "--window-start", "25", "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1
    assert "lead days 0-29" in capsys.readouterr().err


def _wrapped(cause: BaseException) -> FileNotFoundError:
    """fsspec's shape: a bare FileNotFoundError(url) with the real error as the cause."""
    err = FileNotFoundError("https://data.chc.ucsb.edu/...")
    err.__cause__ = cause
    return err


def test_wrapped_timeout_is_retried(mod, monkeypatch, tmp_path):
    calls = []

    def fake_open(url):
        calls.append(url)
        if len(calls) < 2:
            raise _wrapped(TimeoutError())
        return _fake_clim("pr")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    monkeypatch.setattr(mod._time, "sleep", lambda s: None)
    run_skill(
        mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
        "-o", str(tmp_path / "t.zarr"),
    )  # fmt: skip
    assert len(calls) == 2


def test_unreadable_error_names_root_cause(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        mod, "_open_remote", lambda url: (_ for _ in ()).throw(_wrapped(ValueError("bad HDF5")))
    )
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--date", "2026-01-01", "--model", "NCEP-CFSv2", "-v", "pr",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1
    assert "unreadable" in (err := capsys.readouterr().err) and "ValueError: bad HDF5" in err
