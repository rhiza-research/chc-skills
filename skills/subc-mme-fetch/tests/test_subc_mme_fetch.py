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
    if var == "pr":
        ds[src].attrs["standard_name"] = "precipitation_flux"
    kind_label = "mean" if kind == "mean" else "anomaly"
    ds[src].attrs["long_name"] = (
        f"MME {window_days}-day window {kind_label} (forecast mean minus climo)"
        if kind == "anom"
        else f"MME {window_days}-day window mean"
    )
    return ds


def _archive_html(stamps):
    return "".join(f'<a href="{s}/">{s}/</a>' for s in stamps)


def _init_html(stamp, variables, lead_tag="7d"):
    names = [f"mme_{k}_{v}_{lead_tag}_{stamp}.nc" for v in variables for k in ("mean", "anom")]
    return "".join(f'<a href="{n}">{n}</a>' for n in names)


@pytest.fixture
def mod(monkeypatch):
    m = load_skill("subc-mme-fetch", "fetch")
    # Default archive: every variable published for the 2025-12-01 init.
    pages = {
        "07_day/global/archive": _archive_html(["20251201"]),
        "07_day/global/archive/20251201": _init_html("20251201", m.VARIABLES),
    }
    monkeypatch.setattr(m, "_chc_listing", lambda path: pages.get(path, ""))
    return m


def test_url_builder(mod):
    url = mod._url("07_day", "mean", "ts", "7d", date(2025, 12, 1))
    assert url.startswith("https://storage.googleapis.com/sheerwater-public-datalake/")
    assert url.endswith(
        "/chc-mirror/experimental/SubC/07_day/global/archive/mme_mean_ts_7d_20251201.nc"
    )
    chc = (
        "https://data.chc.ucsb.edu/experimental/SubC"
        "/07_day/global/archive/20251201/mme_mean_ts_7d_20251201.nc"
    )
    assert mod._candidate_urls(url) == [chc, url]


def test_normalize_field(mod):
    ds = _fake_nc(kind="anom", var="ts", fill=1.5, window_days=7)
    da = mod._normalize_field(ds, kind="anom", var="ts")
    assert da.name == "ts_anomaly"
    assert "latitude" in da.dims and "longitude" in da.dims


def test_fetch_one_outlook(mod, monkeypatch, tmp_path):
    init = date(2025, 12, 1)
    folder, lead_tag, days = mod.OUTLOOKS["7d"]
    by_url = {}
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
        "--outlook",
        "7d",
        "-v",
        "ts",
        "-v",
        "pr",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert set(ds.data_vars) == {"ts", "ts_anomaly", "pr", "pr_anomaly"}
    assert list(ds["step"].values) == [np.timedelta64(0, "D")]
    assert np.issubdtype(ds["step"].dtype, np.timedelta64)
    assert ds["time"].values == np.datetime64("2025-12-01", "ns")
    assert ds.attrs["initialization_date"] == "2025-12-01"
    assert ds.attrs["outlook_valid_date"] == "2025-12-08"
    assert ds.attrs["outlook"] == "7d"
    assert float(ds["ts"].mean()) == pytest.approx(7.0)
    assert float(ds["ts_anomaly"].mean()) == pytest.approx(7.1)
    assert ds["ts_anomaly"].attrs["long_name"] == "Surface temperature anomaly"
    assert ds["ts_anomaly"].attrs["units"] == "delta_degC"
    assert ds["pr_anomaly"].attrs["long_name"] == "Precipitation anomaly"
    assert ds["pr"].attrs["long_name"] == "Precipitation"
    for name in ds.data_vars:
        assert ds[name].attrs["aggregation_period"] == "7 day"
    assert ds["pr"].attrs["cell_methods"] == "time: sum"
    assert ds["ts"].attrs["cell_methods"] == "time: mean"
    assert float(ds["pr"].mean()) == pytest.approx(7.0)
    assert float(ds["pr_anomaly"].mean()) == pytest.approx(7.1)
    assert ds["pr"].attrs["units"] == "mm"
    assert ds["pr_anomaly"].attrs["units"] == "mm"
    assert ds["pr"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"
    assert ds["pr_anomaly"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"


def test_missing_file_raises(mod, monkeypatch, tmp_path):
    from weather_skills_core import DataError

    def fake_open(url: str):
        raise DataError(f"SubC archive file not found: {url}")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch,
            "--date",
            "2025-12-01",
            "--outlook",
            "7d",
            "-v",
            "ts",
            "-o",
            str(tmp_path / "x.zarr"),
        )
    assert excinfo.value.code == 1


def test_missing_init_fails_before_download_and_lists_nearby(mod, monkeypatch, tmp_path, capsys):
    pages = {
        "07_day/global/archive": _archive_html(["20261029", "20261101", "20261108", "20261112"]),
    }
    monkeypatch.setattr(mod, "_chc_listing", lambda path: pages.get(path, ""))

    def fail_open(url: str):
        raise AssertionError(f"downloaded {url}")

    monkeypatch.setattr(mod, "_open_remote", fail_open)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch,
            "--date",
            "2026-11-05",
            "--outlook",
            "7d",
            "-v",
            "pr",
            "-o",
            str(tmp_path / "x.zarr"),
        )
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "init 2026-11-05" in err
    assert "2026-10-29, 2026-11-01, 2026-11-08, 2026-11-12" in err


def test_init_without_the_variable_counts_as_missing(mod, monkeypatch):
    pages = {"07_day/global/archive/20261105": _init_html("20261105", ["pr", "tas"])}
    monkeypatch.setattr(mod, "_chc_listing", lambda path: pages.get(path, ""))
    assert mod._init_has_files("07_day", "7d", date(2026, 11, 5), ["pr"])
    assert not mod._init_has_files("07_day", "7d", date(2026, 11, 5), ["pr", "tdps"])


def test_probe_latest_with_variable(mod, monkeypatch, capsys):
    pages = {
        "07_day/global/archive": _archive_html(["20251201", "20251208", "20251215"]),
        "07_day/global/archive/20251215": _init_html("20251215", ["pr"]),
        "07_day/global/archive/20251208": _init_html("20251208", ["pr", "ts"]),
    }
    monkeypatch.setattr(mod, "_chc_listing", lambda path: pages.get(path, ""))
    run_skill(mod.fetch, "--outlook", "7d", "--probe-latest", "ts")
    captured = capsys.readouterr()
    assert captured.out.strip() == "2025-12-08"
    # The 2025-12-08 + 7d window is long over: say so on stderr, keep stdout a bare date.
    assert "window ended 2025-12-15" in captured.err


def test_bbox_keeps_cells_overlapping_the_edge(mod, monkeypatch, tmp_path):
    # 30-degree fake grid: no cell centre lies inside 5/34/-5/42, but the cell
    # centred on 30E (15E-45E) overlaps it and must be kept.
    by_url = {}
    folder, lead_tag, days = mod.OUTLOOKS["7d"]
    for kind in ("mean", "anom"):
        url = mod._url(folder, kind, "pr", lead_tag, date(2025, 12, 1))
        by_url[url] = _fake_nc(kind=kind, var="pr", fill=1.0, window_days=days)
    monkeypatch.setattr(mod, "_open_remote", lambda url: by_url[url])

    out = tmp_path / "bbox.zarr"
    run_skill(
        mod.fetch,
        "--date",
        "2025-12-01",
        "--outlook",
        "7d",
        "-v",
        "pr",
        "--bbox",
        "5/34/-5/42",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["longitude"].values) == [30.0]
    assert list(ds["latitude"].values) == [0.0]


def test_bbox_drops_cells_that_only_touch_the_edge(mod, monkeypatch, tmp_path):
    # The cell centred on 0N spans 15S-15N: it only touches the box's 15N south edge.
    by_url = {}
    folder, lead_tag, days = mod.OUTLOOKS["7d"]
    for kind in ("mean", "anom"):
        url = mod._url(folder, kind, "pr", lead_tag, date(2025, 12, 1))
        by_url[url] = _fake_nc(kind=kind, var="pr", fill=1.0, window_days=days)
    monkeypatch.setattr(mod, "_open_remote", lambda url: by_url[url])

    out = tmp_path / "bbox.zarr"
    run_skill(
        mod.fetch,
        "--date",
        "2025-12-01",
        "--outlook",
        "7d",
        "-v",
        "pr",
        "--bbox",
        "25/34/15/42",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["latitude"].values) == [30.0]


def test_probe_latest_all_vars_needs_every_variable(mod, monkeypatch, capsys):
    pages = {
        "15_day/global/archive": _archive_html(["20251201", "20251208", "20251215"]),
        "15_day/global/archive/20251215": _init_html("20251215", ["pr", "tas"], "15d"),
        "15_day/global/archive/20251208": _init_html("20251208", mod.VARIABLES, "15d"),
    }
    monkeypatch.setattr(mod, "_chc_listing", lambda path: pages.get(path, ""))
    run_skill(mod.fetch, "--outlook", "15d", "--probe-latest")
    assert capsys.readouterr().out.strip() == "2025-12-08"


def test_list_init_dates_parses_chc_index(mod, monkeypatch):
    html = (
        '<a href="../">..</a><a href="?C=N;O=D">Name</a>'
        + _archive_html(["20251208", "20251201"])
        + '<a href="pr_mme_latest.png">png</a>'
    )
    monkeypatch.setattr(mod, "_chc_listing", lambda path: html)
    assert mod._list_init_dates("07_day") == [date(2025, 12, 1), date(2025, 12, 8)]
