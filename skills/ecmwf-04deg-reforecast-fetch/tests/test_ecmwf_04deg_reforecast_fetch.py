"""Correctness tests for ecmwf-04deg-reforecast-fetch (GCS listing and store access mocked)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from weather_skills_core.provenance import load_history

INITS = ["2025-01-01", "2025-10-07", "2025-10-09", "2025-10-11", "2025-12-31"]
YEARS = [2005, 2006, 2007, 2008]
MEMBERS = 3
STEPS = 47  # 0..46 days, accumulated
STORE = "sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr"


@pytest.fixture(scope="module")
def fetch_mod():
    return load_skill("ecmwf-04deg-reforecast-fetch", "fetch")


def hindcast_store():
    """Stand-in store: daily rain = lead day + 100*member + 1000*year index + 0.1*init
    index, accumulated since init (zero at step 0), as the real store holds it."""
    lats = np.arange(8.0, -8.01, -0.4)
    lons = np.arange(30.0, 46.01, 0.4)
    daily = (
        np.arange(STEPS - 1)[None, None, None, :]
        + 100.0 * np.arange(MEMBERS)[None, None, :, None]
        + 1000.0 * np.arange(len(YEARS))[None, :, None, None]
        + 0.1 * np.arange(len(INITS))[:, None, None, None]
    )
    acc = np.concatenate([np.zeros(daily.shape[:3] + (1,)), np.cumsum(daily, axis=3)], axis=3)
    values = acc[..., None, None] + np.zeros((lats.size, lons.size))
    return xr.Dataset(
        {
            "tp": (
                ("init", "year", "number", "step", "latitude", "longitude"),
                values.astype("float32"),
                {"units": "kg m**-2"},
            )
        },
        coords={
            "init": pd.to_datetime(INITS).values,
            "year": YEARS,
            "number": np.arange(MEMBERS),
            "step": pd.to_timedelta(np.arange(STEPS), unit="D").values,
            "latitude": lats,
            "longitude": lons,
        },
    )


@pytest.fixture
def mocked(fetch_mod, monkeypatch):
    filled = {i: set(range(len(YEARS))) for i in range(len(INITS))}
    monkeypatch.setattr(fetch_mod, "_list_stores", lambda: {2025: STORE})
    monkeypatch.setattr(fetch_mod, "_open_store", lambda path: hindcast_store())
    monkeypatch.setattr(fetch_mod, "_filled_years", lambda path, i: filled[i])
    return filled


def test_layout_rates_and_history(tmp_path, fetch_mod, mocked):
    out = tmp_path / "rf.zarr"
    run_skill(
        fetch_mod.fetch,
        "--date",
        "2026-10-10",
        "--bbox",
        "5.5/33.5/-5/42.5",
        "--max-lead",
        "14D",
        "-o",
        str(out),
    )
    with xr.open_zarr(out, consolidated=True) as ds:
        tp = ds["tp"]
        assert tp.dims == ("init_time", "number", "step", "latitude", "longitude")
        assert tp.attrs["units"] == "mm day-1"
        assert tp.attrs["data_interval"] == "1 day"
        # 2025-10-09 is the closest init: one run per hindcast year, dated in that year
        assert list(ds["init_time"].values.astype("datetime64[D]").astype(str)) == [
            f"{y}-10-09" for y in YEARS
        ]
        assert list(ds["hindcast_year"].values) == YEARS
        assert set(ds["reforecast_init"].values.astype("datetime64[D]").astype(str)) == {
            "2025-10-09"
        }
        assert ds.sizes["step"] == 14 and ds.sizes["number"] == MEMBERS
        assert ds["step"].values[-1] == np.timedelta64(13, "D")
        assert ds["valid_time"].values[1, 1] == np.datetime64("2006-10-10T00:00", "ns")
        # the accumulation is turned back into each day's amount
        np.testing.assert_allclose(
            tp.isel(init_time=1, number=2).mean(["latitude", "longitude"]),
            np.arange(14) + 200 + 1000 + 0.2,
            rtol=1e-5,
        )
        assert float(ds["latitude"].max()) <= 5.5 and float(ds["latitude"].min()) >= -5.0
        assert ds["latitude"].values[0] > ds["latitude"].values[-1]
        assert ds.attrs["weather_skills_source"] == "sheerwater-ecmwf-0_4-hindcast:2025:20251009"
        assert ds.attrs["reforecast_model_cycle"] == "2025"
    assert load_history(out)[-1]["skill"] == "ecmwf-04deg-reforecast-fetch"


def test_window_pools_inits_across_new_year_and_years_subset(tmp_path, fetch_mod, mocked):
    out = tmp_path / "w.zarr"
    run_skill(
        fetch_mod.fetch,
        "--date",
        "2027-01-02",
        "--window-days",
        "4",
        "--years",
        "2006-2007",
        "--max-lead",
        "7D",
        "-o",
        str(out),
    )
    with xr.open_zarr(out, consolidated=True) as ds:
        starts = list(ds["init_time"].values.astype("datetime64[D]").astype(str))
        assert starts == ["2006-01-01", "2006-12-31", "2007-01-01", "2007-12-31"]
        assert sorted(set(ds["hindcast_year"].values)) == [2006, 2007]


def test_far_from_any_init_exits_2(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2026-06-15", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


def test_unfilled_init_is_skipped_then_exit_1(tmp_path, fetch_mod, mocked):
    mocked[2] = set()  # 2025-10-09 not filled yet
    out = tmp_path / "s.zarr"
    run_skill(fetch_mod.fetch, "--date", "2026-10-10", "--window-days", "2", "-o", str(out))
    with xr.open_zarr(out, consolidated=True) as ds:
        assert set(ds["reforecast_init"].values.astype("datetime64[D]").astype(str)) == {
            "2025-10-11"
        }
    mocked[3] = set()
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2026-10-10", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 1


def test_probe_latest_prints_newest_filled_init(fetch_mod, mocked, capsys):
    mocked[4] = set()
    run_skill(fetch_mod.fetch, "--probe-latest")
    assert capsys.readouterr().out.strip() == "2025-10-11"


@pytest.mark.parametrize(
    "args",
    [
        ["--date", "2026-10-10", "-v", "t2m"],
        ["--date", "2026-10-10", "--max-lead", "36h"],
        ["--date", "2026-10-10", "--years", "1990"],
        ["--date", "2026-10-10", "--window-days", "-1"],
        [],
    ],
)
def test_bad_arguments_exit_2(tmp_path, fetch_mod, mocked, args):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, *args, "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


def test_store_choice(fetch_mod, monkeypatch):
    monkeypatch.setattr(fetch_mod, "_list_stores", lambda: {2025: "a", 2026: "b"})
    assert fetch_mod._pick_store(2026) == (2026, "b")
    assert fetch_mod._pick_store(2030) == (2026, "b")
    assert fetch_mod._pick_store(None) == (2026, "b")


def test_leap_day_run_date(fetch_mod):
    assert fetch_mod._run_date(2006, date(2024, 2, 29)) == np.datetime64("2006-02-28", "ns")
    assert fetch_mod._run_date(2008, date(2024, 2, 29)) == np.datetime64("2008-02-29", "ns")


def test_key_json_env_order(fetch_mod, monkeypatch):
    for name in fetch_mod._CREDENTIAL_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEURAL_GCM_SERVICE_CREDENTIALS", '{"client_email": "neural"}')
    assert fetch_mod._token() == {"client_email": "neural"}
    monkeypatch.setenv("GCLOUD_SERVICE_CREDENTIALS", '{"client_email": "gcloud"}')
    assert fetch_mod._token() == {"client_email": "gcloud"}


def test_bad_key_json_names_the_variable_not_the_value(fetch_mod, monkeypatch):
    monkeypatch.setenv("GCLOUD_SERVICE_CREDENTIALS", "not-json-secret")
    with pytest.raises(fetch_mod.DataError) as exc:
        fetch_mod._token()
    assert "GCLOUD_SERVICE_CREDENTIALS" in str(exc.value)
    assert "not-json-secret" not in str(exc.value)
