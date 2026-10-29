"""Correctness tests for exceedance-probability."""

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_zarr
from weather_skills_core.provenance import load_history


@pytest.fixture(scope="module")
def exceedance_probability():
    return load_skill("exceedance-probability", "exceedance_probability").exceedance_probability


def test_percentage_of_members_above_threshold(tmp_path, exceedance_probability):
    ds = make_forecast(n_step=1, lats=(1.0,), lons=(10.0,), members=4)
    ds["tp"].values[:] = np.array([0.0, 10.0, 20.0, 30.0]).reshape(4, 1, 1, 1)
    src = write_zarr(ds, tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    run_skill(
        exceedance_probability,
        "-i",
        str(src),
        "-o",
        str(out),
        "--dim",
        "number",
        "--threshold",
        "15",
        "--comparison",
        "ge",
    )

    result = xr.open_zarr(out, consolidated=True)
    assert "number" not in result.dims
    assert float(result["tp"].isel(latitude=0, longitude=0, step=0)) == pytest.approx(50.0)
    assert result["tp"].attrs["units"] == "%"
    assert result["tp"].attrs.get("standard_name") is None
    assert load_history(out)[-1]["skill"] == "exceedance-probability"


def test_rejects_unknown_dim(tmp_path, exceedance_probability):
    src = write_zarr(make_forecast(members=2), tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    with pytest.raises(SystemExit) as exc:
        run_skill(
            exceedance_probability,
            "-i",
            str(src),
            "-o",
            str(out),
            "--dim",
            "bogus",
            "--threshold",
            "1",
            "--comparison",
            "ge",
        )
    assert exc.value.code == 2


def test_rejects_unknown_variable(tmp_path, exceedance_probability):
    src = write_zarr(make_forecast(members=2), tmp_path / "in.zarr")
    out = tmp_path / "out.zarr"

    with pytest.raises(SystemExit) as exc:
        run_skill(
            exceedance_probability,
            "-i",
            str(src),
            "-o",
            str(out),
            "--dim",
            "number",
            "--threshold",
            "1",
            "--comparison",
            "ge",
            "--variable",
            "bogus",
        )
    assert exc.value.code == 2
