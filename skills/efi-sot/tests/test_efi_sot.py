"""Tests for efi-sot."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("efi-sot", "efi_sot")


def _forecast(tmp_path, values, name="fc.zarr"):
    values = np.asarray(values, dtype=float)
    ds = make_forecast(n_step=2, members=values.size)
    ds["tp"] = ds["tp"] * 0 + xr.DataArray(values, dims="number")
    ds["tp"].attrs.update(units="mm", long_name="Total precipitation")
    return write_zarr(ds, tmp_path / name)


def _climate(tmp_path, samples=None, units="mm", name="clim.zarr"):
    """Climate samples on a 'year'-like 'sample' dim, same step/lat/lon as the forecast."""
    samples = np.linspace(0, 1, 1001) if samples is None else samples
    base = make_forecast(n_step=2)
    clim = base["tp"].expand_dims(sample=len(samples)) * 0 + xr.DataArray(samples, dims="sample")
    ds = clim.to_dataset(name="tp")
    ds["tp"].attrs.update(units=units)
    return write_zarr(ds, tmp_path / name)


def _run(mod, fc, clim, out, *args):
    run_skill(mod.efi_sot, "-i", str(fc), "--climate", str(clim), "-o", str(out), *args)
    return xr.open_zarr(out)


def _cell(da):
    return float(da.isel(step=0, latitude=0, longitude=0))


def test_forecast_like_climate_has_zero_efi(mod, tmp_path):
    fc = _forecast(tmp_path, np.linspace(0, 1, 101))
    res = _run(mod, fc, _climate(tmp_path), tmp_path / "e.zarr")
    assert set(res.data_vars) == {"tp_efi", "tp_sot_upper", "tp_sot_lower"}
    assert "number" not in res.dims
    assert abs(_cell(res["tp_efi"])) < 0.03
    assert res["tp_efi"].attrs["units"] == "1"
    assert _cell(res["tp_sot_upper"]) == pytest.approx((0.9 - 0.99) / 0.09, abs=1e-6)


def test_extremes(mod, tmp_path):
    wet = _run(mod, _forecast(tmp_path, [2.0] * 5), _climate(tmp_path), tmp_path / "w.zarr")
    assert _cell(wet["tp_efi"]) > 0.8
    assert _cell(wet["tp_sot_upper"]) == pytest.approx((2 - 0.99) / 0.09, rel=1e-6)
    dry = _run(
        mod, _forecast(tmp_path, [-1.0] * 5, "d.zarr"), _climate(tmp_path), tmp_path / "dr.zarr"
    )
    assert _cell(dry["tp_efi"]) == pytest.approx(-_cell(wet["tp_efi"]), abs=1e-9)
    assert _cell(dry["tp_sot_lower"]) == pytest.approx((0.01 + 1) / 0.09, rel=1e-6)


def test_quantile_climate_matches_samples(mod, tmp_path):
    fc = _forecast(tmp_path, np.linspace(0.3, 1.4, 21))
    clim_path = _climate(tmp_path)
    raw = _run(mod, fc, clim_path, tmp_path / "raw.zarr")
    q = xr.open_zarr(clim_path).quantile(np.linspace(0, 1, 101), dim="sample")
    q_path = write_zarr(q.assign_coords(quantile=np.linspace(0, 1, 101)), tmp_path / "q.zarr")
    from_q = _run(mod, fc, q_path, tmp_path / "fq.zarr")
    xr.testing.assert_allclose(raw["tp_efi"].load(), from_q["tp_efi"].load())
    xr.testing.assert_allclose(raw["tp_sot_upper"].load(), from_q["tp_sot_upper"].load())


def test_flat_tail_is_nan_and_units_converted(mod, tmp_path):
    fc = _forecast(tmp_path, [0.0, 1.0, 2.0])
    clim = _climate(tmp_path, samples=np.zeros(50))
    res = _run(mod, fc, clim, tmp_path / "e.zarr", "--sot", "upper")
    assert np.isnan(_cell(res["tp_sot_upper"]))
    assert "tp_sot_lower" not in res
    clim_m = _climate(tmp_path, samples=np.linspace(0, 0.001, 1001), units="m", name="m.zarr")
    res_m = _run(mod, _forecast(tmp_path, [2.0] * 5, "f2.zarr"), clim_m, tmp_path / "m_out.zarr")
    assert _cell(res_m["tp_sot_upper"]) == pytest.approx((2 - 0.99) / 0.09, rel=1e-6)


@pytest.mark.parametrize(
    "units,args",
    [("mm day-1", ()), ("mm", ("--member-dim", "member")), ("mm", ("--climate-dim", "nope"))],
)
def test_usage_errors(mod, tmp_path, units, args):
    fc = _forecast(tmp_path, [1.0, 2.0])
    clim = _climate(tmp_path, units=units)
    with pytest.raises(SystemExit) as exc:
        _run(mod, fc, clim, tmp_path / "e.zarr", *args)
    assert exc.value.code == 2


def _dry_climate(tmp_path, n_dry=60, name="clim.zarr"):
    """n_dry of 100 climate years dry, the rest 5-20 mm."""
    samples = np.r_[np.zeros(n_dry), np.linspace(5, 20, 100 - n_dry)]
    return _climate(tmp_path, samples=samples, name=name)


def test_dry_climate_is_masked(mod, tmp_path):
    fc, clim = _forecast(tmp_path, [0.3] * 5), _dry_climate(tmp_path)
    plain = _run(mod, fc, clim, tmp_path / "p.zarr")
    assert _cell(plain["tp_efi"]) > 0  # the artefact: drizzle reads as wet
    res = _run(mod, fc, clim, tmp_path / "m.zarr", "--dry-climate", "1 mm")
    for v in ("tp_efi", "tp_sot_upper", "tp_sot_lower"):
        assert res[v].isnull().all()
    assert "1 mm" in res["tp_efi"].attrs["dry_climate_mask"]


def test_wet_climate_is_kept(mod, tmp_path):
    clim = _dry_climate(tmp_path, n_dry=40)
    fc = _forecast(tmp_path, [0.0] * 5)
    plain = _run(mod, fc, clim, tmp_path / "p.zarr")
    res = _run(mod, fc, clim, tmp_path / "m.zarr", "--dry-climate", "0.001 m")
    assert _cell(res["tp_efi"]) == pytest.approx(_cell(plain["tp_efi"]), abs=1e-9)
    assert _cell(res["tp_efi"]) < 0  # a dry week where it usually rains


@pytest.mark.parametrize("value", ["1 day", "-1 mm", "banana"])
def test_bad_dry_climate(mod, tmp_path, value):
    fc, clim = _forecast(tmp_path, [1.0, 2.0]), _dry_climate(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(mod, fc, clim, tmp_path / "e.zarr", "--dry-climate", value)
    assert exc.value.code == 2
