"""Correctness tests for icon-eps-fetch (DWD listing, downloads and GRIB decoding mocked)."""

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from weather_skills_core.provenance import load_history

INIT = "2026102100"
# ICON-EPS publishing pattern: hourly to 48 h, 3-hourly to 72 h, 6-hourly to 120 h,
# then 12-hourly to 180 h.
LEADS = (
    list(range(49)) + list(range(51, 73, 3)) + list(range(78, 121, 6)) + list(range(132, 181, 12))
)
MEMBERS = 3
RATE_MM_DAY = 2.0


@pytest.fixture(scope="module")
def fetch_mod():
    return load_skill("icon-eps-fetch", "fetch")


@pytest.fixture
def grid():
    """Scattered cell centres over East Africa, standing in for the triangular grid."""
    rng = np.random.default_rng(0)
    clat = rng.uniform(-12.0, 12.0, 6000)
    clon = rng.uniform(28.0, 48.0, 6000)
    return clat, clon


@pytest.fixture
def mocked(fetch_mod, grid, monkeypatch):
    clat, clon = grid
    requested = []

    def fake_field(run, folder, init, step, tmp):
        requested.append((folder, step))
        if folder == "tot_prec":
            values = np.full((MEMBERS, clat.size), RATE_MM_DAY * step / 24.0)
            units = "kg m**-2"
        else:
            values = np.full((MEMBERS, clat.size), 300.0)
            units = "K"
        return xr.DataArray(
            values,
            dims=("number", "values"),
            coords={"number": np.arange(1, MEMBERS + 1)},
            attrs={"units": units, "GRIB_name": "x"},
        )

    monkeypatch.setattr(fetch_mod, "_published", lambda run, folder: {INIT: LEADS})
    monkeypatch.setattr(fetch_mod, "_fetch_grid", lambda run, init, tmp: (clat, clon))
    monkeypatch.setattr(fetch_mod, "_fetch_field", fake_field)
    return requested


def test_fetch_tp_is_daily_rates_with_history(tmp_path, fetch_mod, mocked):
    out = tmp_path / "icon.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "5/34/-5/42", "-o", str(out))

    assert sorted({s for _, s in mocked}) == list(range(0, 169, 24))
    with xr.open_zarr(out, consolidated=True) as ds:
        tp = ds["tp"]
        assert tp.attrs["units"] == "mm day-1"
        assert tp.attrs.get("data_interval")
        assert ds.sizes["number"] == MEMBERS
        assert ds.sizes["step"] == 7
        assert ds["step"].values[0] == np.timedelta64(0, "ns")
        np.testing.assert_allclose(tp.values, RATE_MM_DAY, rtol=1e-5)
        assert ds["time"].values == np.datetime64("2026-10-21T00:00", "ns")
        assert float(ds["latitude"].max()) == 5.0 and float(ds["longitude"].min()) == 34.0
        assert ds.attrs["weather_skills_source"] == f"dwd-icon-eps:00/{INIT}"
    assert load_history(out)[-1]["skill"] == "icon-eps-fetch"


def test_dwd_names_nearest_and_kelvin(tmp_path, fetch_mod, mocked):
    out = tmp_path / "icon_t2m.zarr"
    run_skill(
        fetch_mod.fetch,
        *("--bbox", "2/36/-2/40", "-v", "t_2m", "--regrid", "nearest"),
        *("--step-interval", "6h", "--max-lead", "168h", "-o", str(out)),
    )
    with xr.open_zarr(out, consolidated=True) as ds:
        assert list(ds.data_vars) == ["t2m"]
        assert ds["t2m"].attrs["units"] == "degree_Celsius"
        np.testing.assert_allclose(ds["t2m"].values, 300.0 - 273.15, rtol=1e-5)
        hours = (ds["step"].values / np.timedelta64(1, "h")).astype(int).tolist()
    # 6-hourly leads stop at 120 h; after that only the 12-hourly ones exist
    assert hours == list(range(0, 121, 6)) + [132, 144, 156, 168]


def test_unknown_variable_exits_2(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "-v", "nope", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


def test_date_not_on_server_exits_1(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2026-10-01", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 1


def test_probe_latest_prints_date(fetch_mod, mocked, capsys):
    run_skill(fetch_mod.fetch, "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-10-21"


def test_linear_regridder_is_exact_for_linear_fields(fetch_mod, grid):
    clat, clon = grid
    lats = np.arange(5.0, -5.01, -0.5)
    lons = np.arange(34.0, 42.01, 0.5)
    regrid = fetch_mod._Regridder(clat, clon, lats, lons, "linear")
    field = (2.0 * clat + 0.5 * clon)[None, :]
    out = regrid(field)[0]
    lon2d, lat2d = np.meshgrid(lons, lats)
    np.testing.assert_allclose(out, 2.0 * lat2d + 0.5 * lon2d, atol=1e-3)


def test_targets_outside_the_cells_take_the_nearest(fetch_mod, grid):
    clat, clon = grid
    # 12.5N lies north of every cell centre, outside the triangulation
    regrid = fetch_mod._Regridder(clat, clon, np.array([12.5]), np.array([38.0]), "linear")
    field = np.arange(clat.size, dtype="float64")[None, :]
    nearest = np.argmin((clat - 12.5) ** 2 + (clon - 38.0) ** 2)
    assert regrid(field)[0, 0, 0] == field[0, nearest]
