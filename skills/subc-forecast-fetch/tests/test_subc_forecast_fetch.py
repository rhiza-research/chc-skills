"""Tests for subc-forecast-fetch (mocked remote opens and listings; no live network)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill


def _fake_forecast(
    var: str,
    *,
    init: str = "2026-11-09",
    members=(1, 2, 3, 4),
    n_lead: int = 5,
    member_first: bool = False,
    timedelta_lead: bool = False,
    descending_lat: bool = True,
    levels=None,
    fill: float = 1.0,
    units: str = "kg m-2 s-1",
    standard_name: str = "precipitation_flux",
):
    """IRIDL/CHC-like forecast file: S, M, [P,] L, Y, X (or M first)."""
    lats = np.arange(90.0, -91.0, -30.0) if descending_lat else np.arange(-90.0, 91.0, 30.0)
    lons = np.arange(0.0, 360.0, 30.0)
    lead = np.arange(n_lead) + 0.5
    lead_coord = (
        (lead * 86400e9).astype("timedelta64[ns]") if timedelta_lead else lead.astype(np.float32)
    )
    dims = ["M", "S"] if member_first else ["S", "M"]
    coords = {
        "S": np.array([np.datetime64(init, "ns")]),
        "M": np.asarray(members, dtype=np.float32),
        "L": lead_coord,
        "Y": lats.astype(np.float32),
        "X": lons.astype(np.float32),
    }
    if levels is not None:
        dims.append("P")
        coords["P"] = np.asarray(levels, dtype=np.int32)
    dims += ["L", "Y", "X"]
    shape = [len(coords[d]) for d in dims]
    data = np.full(shape, fill, dtype=np.float32)
    ds = xr.Dataset({var: (dims, data)}, coords=coords, attrs={"Conventions": "IRIDL"})
    ds[var].attrs.update(units=units, standard_name=standard_name, long_name=var)
    if levels is not None:
        ds["P"].attrs.update(units="hPa", standard_name="air_pressure")
    return ds


@pytest.fixture
def mod(monkeypatch):
    m = load_skill("subc-forecast-fetch", "fetch")

    def no_mirror(var):
        raise FileNotFoundError("no mirror in tests")

    # Offline by default: the mirror is unreachable, so reads fall back to the CHC path.
    monkeypatch.setattr(m, "_open_mirror", no_mirror)
    return m


def _serve(mod, monkeypatch, files: dict[str, xr.Dataset]):
    def fake_open(url):
        for var, ds in files.items():
            if f"/{var}/daily/" in url:
                return ds
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(mod, "_open_remote", fake_open)


def test_file_url(mod):
    assert mod._file_url("NCEP-CFSv2", "pr", date(2026, 11, 9)) == (
        "ftp://ftp.chc.ucsb.edu/experimental/SubC/forecasts"
        "/pr/daily/NCEP-CFSv2/11/pr_NCEP-CFSv2_20261109.daily.nc"
    )


def test_fetch_pr_cfsv2_layout(mod, monkeypatch, tmp_path):
    # 1.157e-5 kg m-2 s-1 ~= 1 mm/day
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr", fill=1 / 86400)})
    out = tmp_path / "out.zarr"
    run_skill(mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-o", str(out))
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["pr"].dims == ("number", "step", "latitude", "longitude")
    assert list(ds["number"].values) == [1, 2, 3, 4]
    assert list(ds["step"].values / np.timedelta64(1, "D")) == [0, 1, 2, 3, 4]
    assert ds["time"].values == np.datetime64("2026-11-09", "ns")
    assert ds["valid_time"].values[1] == np.datetime64("2026-11-10", "ns")
    assert np.all(np.diff(ds["latitude"].values) > 0)
    assert ds["longitude"].min() >= -180 and ds["longitude"].max() < 180
    assert ds["pr"].attrs["units"] == "mm day-1"
    assert ds["pr"].attrs["standard_name"] == "lwe_precipitation_rate"
    assert ds["pr"].attrs["data_interval"] == "1 day"
    assert float(ds["pr"].mean()) == pytest.approx(1.0, rel=1e-4)
    assert ds.attrs["model"] == "NCEP-CFSv2"
    assert ds.attrs["weather_skills_source"] == "chc-subc-forecast"


def test_eccc_layout_and_temperature(mod, monkeypatch, tmp_path):
    common = {
        "init": "2026-10-29",
        "members": range(21),
        "member_first": True,
        "timedelta_lead": True,
        "descending_lat": False,
    }
    files = {
        "pr": _fake_forecast("pr", **common),
        "tas": _fake_forecast(
            "tas", fill=300.0, units="K", standard_name="air_temperature", **common
        ),
    }
    _serve(mod, monkeypatch, files)
    out = tmp_path / "eccc.zarr"
    run_skill(
        mod.fetch, "--model", "ECCC-GEPS8", "--date", "2026-10-29", "-v", "pr", "-v", "tas",
        "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["tas"].dims == ("number", "step", "latitude", "longitude")
    assert ds["number"].values[0] == 0 and ds.sizes["number"] == 21
    assert list(ds["step"].values / np.timedelta64(1, "D")) == [0, 1, 2, 3, 4]
    assert ds["tas"].attrs["units"] == "degree_Celsius"
    assert float(ds["tas"].mean()) == pytest.approx(300 - 273.15, abs=1e-3)


def test_pressure_levels(mod, monkeypatch, tmp_path):
    zg = _fake_forecast(
        "zg", levels=(500, 200), fill=5800.0, units="m", standard_name="geopotential_height"
    )
    _serve(mod, monkeypatch, {"zg": zg})
    out = tmp_path / "zg.zarr"
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-v", "zg", "-o", str(out)
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["zg"].dims == ("number", "step", "level", "latitude", "longitude")
    assert list(ds["level"].values) == [500.0, 200.0]
    assert ds["level"].attrs["units"] == "hPa"
    assert ds["zg"].attrs["units"] == "m"
    assert ds["zg"].attrs["standard_name"] == "geopotential_height"


def test_bbox_reads_only_overlapping_cells(mod, monkeypatch, tmp_path):
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr")})
    out = tmp_path / "bbox.zarr"
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "--bbox", "5/34/-5/42",
        "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["longitude"].values) == [30.0]
    assert list(ds["latitude"].values) == [0.0]


def test_bbox_across_greenwich(mod, monkeypatch, tmp_path):
    # 0..359 source longitudes: a -40..20 box must take 330, 0 and 30 (cell overlap pad).
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr")})
    out = tmp_path / "gw.zarr"
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "--bbox", "10/-40/-10/20",
        "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert list(ds["longitude"].values) == [-30.0, 0.0, 30.0]


def _http_error(status):
    class _Resp(Exception):
        def __init__(self):
            super().__init__(f"{status}, message='HTTP {status}'")
            self.status = status

    err = FileNotFoundError("ftp://ftp.chc.ucsb.edu/...")
    err.__cause__ = _Resp()
    return err


def test_missing_init_lists_available(mod, monkeypatch, tmp_path, capsys):
    def fake_open(url):
        raise _http_error(404)

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    monkeypatch.setattr(
        mod,
        "_list_inits",
        lambda model, var, month: [date(2025, 10, 30), date(2026, 10, 26), date(2026, 10, 29)],
    )
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--model", "ECCC-GEPS8", "--date", "2026-10-30",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "2026-10-26, 2026-10-29" in err
    assert "2025-10-30" not in err


def test_429_retries(mod, monkeypatch, tmp_path):
    calls = []

    def fake_open(url):
        calls.append(url)
        if len(calls) < 2:
            raise _http_error(429)
        return _fake_forecast("pr")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    monkeypatch.setattr(mod._time, "sleep", lambda s: None)
    out = tmp_path / "r.zarr"
    run_skill(mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-o", str(out))
    assert len(calls) == 2


def test_probe_latest_walks_back_months(mod, monkeypatch, capsys):
    from datetime import UTC, datetime

    today = datetime.now(UTC).date()
    this_month, last_month = today.month, 12 if today.month == 1 else today.month - 1
    last_year = date(today.year - 1, this_month, 1)
    recent = date(today.year - (1 if this_month == 1 else 0), last_month, 1)
    listings = {this_month: [last_year], last_month: [recent]}

    def fake_list(model, var, month):
        assert model == "ECCC-GEPS8" and var == "pr"
        return listings.get(month, [])

    monkeypatch.setattr(mod, "_list_inits", fake_list)
    run_skill(mod.fetch, "--model", "ECCC-GEPS8", "--probe-latest")
    assert capsys.readouterr().out.strip() == recent.isoformat()


def test_list_inits_parses_ftp_listing(mod, monkeypatch):
    names = [
        "pr_NCEP-CFSv2_20251109.daily.nc",
        "pr_NCEP-CFSv2_20261109.daily.nc",
        "pr_NCEP-CFSv2_20261109.daily.nc.bak",
        "tas_NCEP-CFSv2_20261108.daily.nc",
    ]
    monkeypatch.setattr(mod, "_ftp_list", lambda url: names)
    assert mod._list_inits("NCEP-CFSv2", "pr", 11) == [date(2025, 11, 9), date(2026, 11, 9)]


def test_unknown_variable(mod, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-v", "rain",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 2


def _wrapped(cause: BaseException) -> FileNotFoundError:
    """fsspec's shape: a bare FileNotFoundError(url) with the real error as the cause."""
    err = FileNotFoundError("ftp://ftp.chc.ucsb.edu/...")
    err.__cause__ = cause
    return err


def test_wrapped_disconnect_is_retried(mod, monkeypatch, tmp_path):
    calls = []

    class ServerDisconnectedError(Exception):
        pass

    def fake_open(url):
        calls.append(url)
        if len(calls) < 2:
            raise _wrapped(ServerDisconnectedError("Server disconnected"))
        return _fake_forecast("pr")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    monkeypatch.setattr(mod._time, "sleep", lambda s: None)
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-o", str(tmp_path / "d.zarr")
    )
    assert len(calls) == 2


def test_unreadable_error_names_root_cause(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        mod, "_open_remote", lambda url: (_ for _ in ()).throw(_wrapped(ValueError("bad HDF5")))
    )
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1
    assert "unreadable" in (err := capsys.readouterr().err) and "ValueError: bad HDF5" in err


def test_ftp_reply_codes_map_to_retry_rules(mod):
    assert mod._FTPError("550 No such file or directory").status == 404
    assert mod._FTPError("421 Too many connections").status == 429
    assert mod._is_transient(mod._FTPError("425 Can't open data connection"))
    assert not mod._is_transient(mod._FTPError("530 Login incorrect"))


def test_open_remote_downloads_then_cleans_up(mod, monkeypatch, tmp_path):
    seen = {}

    def fake_download(url, dest):
        seen["path"] = dest
        _fake_forecast("pr").to_netcdf(dest, engine="h5netcdf")

    monkeypatch.setattr(mod, "_download", fake_download)
    with mod._open_remote(mod.CHC_BASE + "/x/y.daily.nc") as ds:
        assert seen["path"].name == "y.daily.nc"
        assert "pr" in ds.data_vars
    assert not seen["path"].exists()


def test_iridl_kelvin_scale_units(mod, monkeypatch, tmp_path):
    tas = _fake_forecast("tas", fill=300.0, units="Kelvin_scale", standard_name="air_temperature")
    _serve(mod, monkeypatch, {"tas": tas})
    out = tmp_path / "k.zarr"
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-v", "tas", "-o", str(out)
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["tas"].attrs["units"] == "degree_Celsius"
    assert float(ds["tas"].mean()) == pytest.approx(300 - 273.15, abs=1e-3)


def _mirror_store(mod, tmp_path, model="NCEP-CFSv2", inits=("2026-11-08", "2026-11-09")):
    """A plain-Zarr stand-in for the Icechunk mirror: one group per model, inits on time."""
    lats = np.arange(-90.0, 91.0, 30.0, dtype=np.float32)
    lons = np.arange(-180.0, 180.0, 30.0, dtype=np.float32)
    times = np.array([np.datetime64(d, "ns") for d in inits])
    steps = (np.arange(5) * 86400e9).astype("timedelta64[ns]")
    data = np.full((len(times), 4, 5, lats.size, lons.size), 2.0, dtype=np.float32)
    ds = xr.Dataset(
        {"pr": (("time", "number", "step", "latitude", "longitude"), data)},
        coords={
            "time": times, "number": np.arange(1, 5), "step": steps,
            "latitude": lats, "longitude": lons,
        },
    )  # fmt: skip
    ds["pr"].attrs.update(units="mm day-1", standard_name="lwe_precipitation_rate")
    ds = ds.assign_coords(valid_time=ds["time"] + ds["step"])
    path = tmp_path / "mirror.zarr"
    ds.to_zarr(path, group=model, consolidated=False)
    return str(path)


def test_reads_from_mirror_first(mod, monkeypatch, tmp_path):
    store = _mirror_store(mod, tmp_path)
    monkeypatch.setattr(mod, "_open_mirror", lambda var: store)
    monkeypatch.setattr(
        mod, "_open_remote", lambda url: (_ for _ in ()).throw(AssertionError("CHC was read"))
    )
    out = tmp_path / "m.zarr"
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "--bbox", "5/34/-5/42",
        "-o", str(out),
    )  # fmt: skip
    ds = xr.open_zarr(out, consolidated=True)
    assert ds["pr"].dims == ("number", "step", "latitude", "longitude")
    assert ds["time"].values == np.datetime64("2026-11-09", "ns")
    assert list(ds["longitude"].values) == [30.0] and list(ds["latitude"].values) == [0.0]
    assert ds["pr"].attrs["units"] == "mm day-1"
    assert float(ds["pr"].mean()) == pytest.approx(2.0)


def test_init_missing_from_mirror_falls_back_to_chc(mod, monkeypatch, tmp_path, capsys):
    store = _mirror_store(mod, tmp_path, inits=("2026-11-08",))
    monkeypatch.setattr(mod, "_open_mirror", lambda var: store)
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr", fill=1 / 86400)})
    out = tmp_path / "f.zarr"
    run_skill(mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-o", str(out))
    assert "not on the mirror yet" in capsys.readouterr().err
    assert float(xr.open_zarr(out)["pr"].mean()) == pytest.approx(1.0, rel=1e-4)


def test_source_mirror_does_not_fall_back(mod, monkeypatch, tmp_path):
    store = _mirror_store(mod, tmp_path, inits=("2026-11-08",))
    monkeypatch.setattr(mod, "_open_mirror", lambda var: store)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "--source", "mirror",
            "-o", str(tmp_path / "x.zarr"),
        )  # fmt: skip
    assert excinfo.value.code == 1


def test_source_chc_skips_mirror(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(
        mod, "_open_mirror", lambda var: (_ for _ in ()).throw(AssertionError("mirror opened"))
    )
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr")})
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "--source", "chc",
        "-o", str(tmp_path / "c.zarr"),
    )  # fmt: skip


def test_mirror_env_none_disables_mirror(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("SUBC_FORECAST_MIRROR", "none")
    monkeypatch.setattr(
        mod, "_open_mirror", lambda var: (_ for _ in ()).throw(AssertionError("mirror opened"))
    )
    _serve(mod, monkeypatch, {"pr": _fake_forecast("pr")})
    run_skill(
        mod.fetch, "--model", "NCEP-CFSv2", "--date", "2026-11-09", "-o", str(tmp_path / "n.zarr")
    )
