"""Correctness tests for era5-timeseries-fetch (no network)."""

import pandas as pd
import pytest
from conftest import load_skill, run_skill


@pytest.fixture(scope="module")
def mod():
    return load_skill("era5-timeseries-fetch", "fetch")


def test_parse_years_ranges_and_lists(mod):
    assert mod._parse_years("2006-2008,1997, 2007") == [1997, 2006, 2007, 2008]


@pytest.mark.parametrize("text", ["2010-2006", "abc", ","])
def test_parse_years_rejects_bad_input(mod, text):
    from weather_skills_core import UsageError

    with pytest.raises(UsageError):
        mod._parse_years(text)


def test_shift_to_year_keeps_length_across_new_year(mod):
    start, end = mod._parse_window("1997-12-15/1998-01-15")
    s, e = mod._shift_to_year(start, end, 2015)
    assert (s, e) == (pd.Timestamp("2015-12-15"), pd.Timestamp("2016-01-15"))


def test_shift_to_year_feb_29(mod):
    start, end = mod._parse_window("2024-02-29/2024-03-02")
    s, e = mod._shift_to_year(start, end, 2023)
    assert (s, e) == (pd.Timestamp("2023-02-28"), pd.Timestamp("2023-03-02"))


def test_hourly_times_joins_windows_and_filters_hours(mod):
    windows = [
        mod._parse_window("2020-01-01/2020-01-02"),
        mod._parse_window("2020-01-02/2020-01-03"),
    ]
    times = mod._hourly_times(windows, [0, 12])
    assert len(times) == 6  # 3 distinct days x 2 hours; the shared day is read once
    assert times.is_monotonic_increasing and times.is_unique
    assert len(mod._hourly_times(windows[:1], None)) == 48


@pytest.mark.parametrize(
    "argv",
    [
        ["--start-time", "2020-01-01", "--end-time", "2020-01-02", "--bbox", "1/1/0/2"],  # no -v
        ["-v", "t2m", "--start-time", "2020-01-01", "--end-time", "2020-01-02"],  # no area
        ["-v", "t2m", "--bbox", "1/1/0/2"],  # no time
        ["-v", "t2m", "--bbox", "1/1/0/2", "--point", "0,1", "--window", "2020-01-01/2020-01-02"],
        ["-v", "t2m", "--point", "0,1", "--window", "2020-01-05/2020-01-02"],
        ["-v", "t2m", "--point", "0,1", "--window", "2020-01-01/2020-01-02", "--hours", "25"],
    ],
)
def test_usage_errors_exit_2_before_network(tmp_path, mod, argv):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, *argv, "-o", str(tmp_path / "o.zarr"))
    assert exc.value.code == 2


def test_missing_key_exits_2(tmp_path, mod, monkeypatch):
    monkeypatch.delenv("ECMWF_DATASTORES_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch, "-v", "t2m", "--point", "0,1", "--window", "2020-01-01/2020-01-02",
            "-o", str(tmp_path / "o.zarr"),
        )  # fmt: skip
    assert exc.value.code == 2
