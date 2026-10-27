"""Correctness tests for planette-reforecast-fetch (no network)."""

import pandas as pd
import pytest
from conftest import load_skill, run_skill


@pytest.fixture(scope="module")
def mod():
    return load_skill("planette-reforecast-fetch", "fetch")


def test_pick_inits_takes_the_nearest_init_per_year(mod):
    # Inits every other day, as in the store.
    inits = pd.date_range("2006-01-03", "2008-12-31", freq="2D")
    picks = mod._pick_inits(inits, pd.Timestamp("2026-10-22"), range(2006, 2010), 4)
    assert sorted(picks) == [2006, 2007, 2008]  # 2009 is past the store's end
    for year, i in picks.items():
        assert abs((inits[i] - pd.Timestamp(year=year, month=10, day=22)).days) <= 1


def test_pick_inits_respects_max_gap(mod):
    inits = pd.DatetimeIndex(["2010-10-10", "2011-10-21"])
    picks = mod._pick_inits(inits, pd.Timestamp("2026-10-22"), range(2010, 2012), 4)
    assert list(picks) == [2011]


def test_pick_inits_feb_29_uses_feb_28(mod):
    inits = pd.DatetimeIndex(["2007-02-28", "2008-02-29"])
    picks = mod._pick_inits(inits, pd.Timestamp("2028-02-29"), range(2007, 2009), 0)
    assert picks == {2007: 0, 2008: 1}


@pytest.mark.parametrize("text", ["2010-2006", "abc", "2006-xx"])
def test_bad_years_exit_2(tmp_path, mod, text):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch, "--date", "2026-10-22", "--years", text, "-o", str(tmp_path / "o.zarr")
        )
    assert exc.value.code == 2


def test_bad_lead_days_exit_2(tmp_path, mod):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch, "--date", "2026-10-22", "--lead-days", "50", "-o", str(tmp_path / "o.zarr")
        )
    assert exc.value.code == 2


def test_ocean_and_atmosphere_mix_exit_2(tmp_path, mod):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch,
            "--date",
            "2026-10-22",
            "-v",
            "sss",
            "-v",
            "pr",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert exc.value.code == 2
