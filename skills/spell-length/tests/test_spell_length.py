"""Correctness tests for spell-length."""

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr
from weather_skills_core.provenance import load_history


@pytest.fixture(scope="module")
def spell_length():
    return load_skill("spell-length", "spell_length").spell_length


def test_longest_dry_run_over_time(tmp_path, spell_length):
    ds = make_gridded(n_time=5, lats=(1.0,), lons=(10.0,))
    ds["precip"].values[:] = np.array([0.0, 0.0, 5.0, 0.0, 0.0]).reshape(5, 1, 1)
    src = write_zarr(ds, tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    run_skill(
        spell_length, "-i", str(src), "-o", str(out), "--threshold", "1", "--comparison", "lt"
    )

    result = xr.open_zarr(out, consolidated=True)
    assert "time" not in result.dims
    assert float(result["precip"].isel(latitude=0, longitude=0)) == pytest.approx(2.0)
    assert result["precip"].attrs["units"] == "1"
    assert result["precip"].attrs.get("standard_name") is None
    assert load_history(out)[-1]["skill"] == "spell-length"


def test_falls_back_to_lead_time_dim(tmp_path, spell_length):
    ds = make_forecast(n_step=4, lats=(1.0,), lons=(10.0,))
    ds["tp"].values[:] = np.array([0.0, 0.0, 0.0, 5.0]).reshape(4, 1, 1)
    src = write_zarr(ds, tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    run_skill(
        spell_length, "-i", str(src), "-o", str(out), "--threshold", "1", "--comparison", "lt"
    )

    result = xr.open_zarr(out, consolidated=True)
    assert "step" not in result.dims
    assert float(result["tp"].isel(latitude=0, longitude=0)) == pytest.approx(3.0)


def test_rejects_unknown_time_dim(tmp_path, spell_length):
    src = write_zarr(make_gridded(), tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    with pytest.raises(SystemExit) as exc:
        run_skill(
            spell_length,
            "-i",
            str(src),
            "-o",
            str(out),
            "--threshold",
            "1",
            "--comparison",
            "lt",
            "--time-dim",
            "bogus",
        )
    assert exc.value.code == 2
