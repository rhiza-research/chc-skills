"""Tests for subc-mme-fetch (mocked remote opens; no live network)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill


def _fake_nc(*, kind: str, var: str, fill: float, window_days: int) -> xr.Dataset:
    """Minimal SubC-like in-memory Dataset."""
    lats = np.arange(-90.0, 91.0, 30.0)
    lons = np.arange(0.0, 360.0, 30.0)
    data = np.full((lats.size, lons.size), fill, dtype=np.float32)
    src = "mme_mean" if kind == "mean" else "mme_anom"
    ds = xr.Dataset(
        {src: (("Y", "X"), data)},
        coords={"Y": lats.astype(np.float32), "X": lons.astype(np.float32)},
        attrs={
            "window_start": "2025-12-01",
            "window_end": "2025-12-07",
            "window_days": window_days,
            "operation": "sum" if var == "pr" else "mean",
        },
    )
    ds[src].attrs["units"] = "mm" if var == "pr" else "K"
    # Mimic source NetCDFs, which embed window length in long_name.
    kind_label = "mean" if kind == "mean" else "anomaly"
    ds[src].attrs["long_name"] = (
        f"MME {window_days}-day window {kind_label} (forecast mean minus climo)"
        if kind == "anom"
        else f"MME {window_days}-day window mean"
    )
    return ds


@pytest.fixture
def mod():
    return load_skill("subc-mme-fetch", "fetch")


def test_url_builder(mod):
    url = mod._url("07_day", "mean", "ts", "7d", date(2025, 12, 1))
    assert url.endswith("/07_day/global/archive/mme_mean_ts_7d_20251201.nc")


def test_normalize_field(mod):
    ds = _fake_nc(kind="anom", var="ts", fill=1.5, window_days=7)
    da = mod._normalize_field(ds, kind="anom", var="ts")
    assert da.name == "ts_anomaly"
    assert "latitude" in da.dims and "longitude" in da.dims


def test_fetch_stitches_leads_and_vars(mod, monkeypatch, tmp_path):
    init = date(2025, 12, 1)
    by_url = {}
    for folder, lead_tag, days in mod.LEADS:
        for var in ("ts", "pr"):
            for kind in ("mean", "anom"):
                url = mod._url(folder, kind, var, lead_tag, init)
                fill = float(days) + (0.1 if kind == "anom" else 0.0)
                by_url[url] = _fake_nc(kind=kind, var=var, fill=fill, window_days=days)

    def fake_open(url: str):
        if url not in by_url:
            raise FileNotFoundError(url)
        return by_url[url]

    monkeypatch.setattr(mod, "_open_remote", fake_open)

    out = tmp_path / "out.zarr"
    run_skill(
        mod.fetch,
        "--date",
        "2025-12-01",
        "-v",
        "ts",
        "-v",
        "pr",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert set(ds.data_vars) == {"ts", "ts_anomaly", "pr", "pr_anomaly"}
    steps = [np.timedelta64(d, "D") for d in (7, 15, 30)]
    assert list(ds["step"].values) == steps
    assert np.issubdtype(ds["step"].dtype, np.timedelta64)
    assert ds["time"].values == np.datetime64("2025-12-01", "ns")
    assert float(ds["ts"].isel(step=0).mean()) == pytest.approx(7.0)
    assert float(ds["ts"].isel(step=2).mean()) == pytest.approx(30.0)
    assert float(ds["ts_anomaly"].isel(step=0).mean()) == pytest.approx(7.1)
    assert ds["ts_anomaly"].attrs["long_name"] == "SubC MME ts_anomaly"
    assert "7-day" not in ds["ts_anomaly"].attrs["long_name"]
    assert ds["pr"].attrs["long_name"] == "SubC MME pr"


def test_missing_file_raises(mod, monkeypatch, tmp_path):
    from weather_skills_core import DataError

    def fake_open(url: str):
        raise DataError(f"SubC archive file not found: {url}")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(mod.fetch, "--date", "2025-12-01", "-v", "ts", "-o", str(tmp_path / "x.zarr"))
    assert excinfo.value.code == 1
