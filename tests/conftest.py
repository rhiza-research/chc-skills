"""Shared helpers for per-skill correctness tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import xarray as xr

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

# Minimal valid 1x1 PNG (for fetcher/figure mocks).
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_gridded(
    n_time=2,
    lats=(1.0, 2.0, 3.0),
    lons=(10.0, 11.0, 12.0, 13.0),
    name="precip",
    fill=1.0,
    start="2026-01-01",
):
    """Small gridded Dataset with CF-style lat/lon/time coords."""
    times = np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(n_time, "D"))
    data = np.full((n_time, len(lats), len(lons)), fill, dtype=np.float64)
    ds = xr.Dataset(
        {name: (("time", "latitude", "longitude"), data)},
        coords={
            "time": times.astype("datetime64[ns]"),
            "latitude": list(lats),
            "longitude": list(lons),
        },
    )
    ds[name].attrs.update(units="mm day-1", standard_name="lwe_precipitation_rate")
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    ds["time"].attrs.update(standard_name="time", axis="T")
    return ds


def make_forecast(
    n_step=3,
    lats=(1.0, 2.0),
    lons=(10.0, 11.0),
    name="tp",
    fill=1.0,
    init="2026-01-01",
    members=None,
):
    """Classic forecast: scalar ``time`` init + ``step`` lead dim (+ optional ``number``)."""
    steps = np.array([np.timedelta64(d, "D") for d in range(1, n_step + 1)])
    dims = ["step", "latitude", "longitude"]
    shape = [n_step, len(lats), len(lons)]
    coords = {
        "time": np.datetime64(init, "ns"),
        "step": steps,
        "latitude": list(lats),
        "longitude": list(lons),
    }
    if members is not None:
        dims = ["number", *dims]
        shape = [members, *shape]
        coords["number"] = list(range(members))
    data = np.full(shape, fill, dtype=np.float64)
    ds = xr.Dataset({name: (dims, data)}, coords=coords)
    ds[name].attrs.update(units="mm", long_name="Total precipitation")
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    ds["step"].attrs.update(
        standard_name="forecast_period", long_name="time since forecast_reference_time"
    )
    ds["time"].attrs.update(standard_name="forecast_reference_time", axis="T")
    return ds


def make_point_obs(
    n_time=3,
    n_points=2,
    name="precip",
    fill=1.0,
    start="2026-01-01",
):
    """Small point_obs Dataset with point_id + time."""
    times = np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(n_time, "D"))
    point_ids = [f"S{i}" for i in range(n_points)]
    lats = np.linspace(1.0, 2.0, n_points)
    lons = np.linspace(10.0, 11.0, n_points)
    data = np.full((n_points, n_time), fill, dtype=np.float64)
    ds = xr.Dataset(
        {name: (("point_id", "time"), data)},
        coords={
            "point_id": point_ids,
            "time": times.astype("datetime64[ns]"),
            "latitude": ("point_id", lats),
            "longitude": ("point_id", lons),
        },
    )
    ds[name].attrs.update(units="mm day-1", standard_name="lwe_precipitation_rate")
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east")
    ds["time"].attrs.update(standard_name="time", axis="T")
    return ds


def write_era5_archive(folder, lats, lons, end="2026-02-28", start_year=2006, seed=0):
    """Synthetic ERA5 archive as in gs://africa-forecasting-data/era5/: per-year
    era5_olr_<YYYY>.nc (time, latitude, longitude), era5_u_15S15N_<YYYY>.nc (time,
    longitude) and BoM's rmm.74toRealtime.txt, daily up to ``end``."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    end = np.datetime64(end)
    lats = np.sort(np.asarray(lats, dtype=float))[::-1]
    lons = np.asarray(lons, dtype=float)
    for year in range(start_year, int(str(end)[:4]) + 1):
        last = min(end, np.datetime64(f"{year}-12-31"))
        times = np.arange(np.datetime64(f"{year}-01-01"), last + np.timedelta64(1, "D"))
        doy = (times - times.astype("datetime64[Y]")).astype(int)
        season = np.cos(2 * np.pi * doy / 365.25)[:, None, None]
        olr = 240 + 15 * season + rng.normal(0, 10, (len(times), len(lats), len(lons)))
        source = ("time", np.full(len(times), "wb2"))
        xr.Dataset(
            {"olr": (("time", "latitude", "longitude"), olr.astype("float32"))},
            coords={
                "time": times.astype("datetime64[ns]"),
                "latitude": lats,
                "longitude": lons,
                "source": source,
            },
        ).to_netcdf(folder / f"era5_olr_{year}.nc")
        u = {
            name: (
                ("time", "longitude"),
                (scale * season[:, :, 0] + rng.normal(0, scale, (len(times), len(lons)))).astype(
                    "float32"
                ),
            )
            for name, scale in (("u850", 2.0), ("u200", 5.0))
        }
        xr.Dataset(
            u, coords={"time": times.astype("datetime64[ns]"), "longitude": lons, "source": source}
        ).to_netcdf(folder / f"era5_u_15S15N_{year}.nc")
    days = np.arange(np.datetime64(f"{start_year}-01-01"), end + np.timedelta64(1, "D"))
    lines = ["RMM values (synthetic)", "year, month, day, RMM1, RMM2, phase, amplitude."]
    for day, (x, y) in zip(days, rng.normal(0, 1, (len(days), 2)), strict=True):
        y_, m, d = str(day).split("-")
        lines.append(f"{y_} {int(m)} {int(d)} {x:.5f} {y:.5f} 1 {np.hypot(x, y):.5f} synthetic")
    (folder / "rmm.74toRealtime.txt").write_text("\n".join(lines) + "\n")
    return folder


WAVE_LONS = tuple(float(x) for x in range(-180, 180, 30))
WAVE_KEYS = ("low", "mjo", "kelvin", "er", "mrg_td")


def make_wave_dataset(
    init="2026-03-01",
    members=3,
    days_before=20,
    n_fc=14,
    lats=(-7.5, 0.0, 7.5),
    lons=WAVE_LONS,
    seed=0,
):
    """Small Dataset shaped like olr-wave-filter output."""
    rng = np.random.default_rng(seed)
    init = np.datetime64(init, "D")
    times = np.arange(init - days_before, init + n_fc).astype("datetime64[ns]")
    shape = (members, len(times), len(lats), len(lons))
    data = {
        "olr_anomaly": (
            ("number", "time", "latitude", "longitude"),
            rng.normal(0, 30, shape).astype("float32"),
            {"units": "W m-2"},
        )
    }
    for w in WAVE_KEYS:
        data[f"olr_{w}"] = (
            ("number", "time", "latitude", "longitude"),
            rng.normal(0, 20, shape).astype("float32"),
            {"units": "W m-2", "amplitude_std_a": 8.0, "amplitude_std_b": 4.0},
        )
        data[f"olr_{w}_obs_only"] = (
            ("time", "latitude", "longitude"),
            rng.normal(0, 10, shape[1:]).astype("float32"),
            {"units": "W m-2"},
        )
        data[f"olr_{w}_local_std_a"] = (
            ("longitude",),
            np.full(len(lons), 8.0, "float32"),
            {"units": "W m-2"},
        )
        data[f"olr_{w}_local_std_b"] = (
            ("longitude",),
            np.full(len(lons), 4.0, "float32"),
            {"units": "W m-2 day-1"},
        )
        data[f"olr_{w}_clim_amp_p90"] = (
            ("phase", "longitude"),
            np.full((8, len(lons)), 2.0, "float32"),
            {"units": "1"},
        )
        data[f"olr_{w}_clim_amp_p99"] = (
            ("phase", "longitude"),
            np.full((8, len(lons)), 3.0, "float32"),
            {"units": "1"},
        )
        data[f"olr_{w}_clim_amp_p90_all"] = (
            ("longitude",),
            np.full(len(lons), 2.0, "float32"),
            {"units": "1"},
        )
    ds = xr.Dataset(
        data,
        coords={
            "number": np.arange(members),
            "time": times,
            "latitude": list(lats),
            "longitude": [float(x) for x in lons],
            "phase": np.arange(1, 9),
            "is_forecast": ("time", times >= init),
        },
        attrs={"forecast_reference_time": str(init)},
    )
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    ds["time"].attrs.update(standard_name="time", axis="T")
    return ds


def make_rmm_dataset(init="2026-03-01", members=3, obs_days=10, n_fc=21, seed=0):
    """Small Dataset shaped like mjo-rmm-index output."""
    rng = np.random.default_rng(seed)
    init = np.datetime64(init, "D")
    times = np.arange(init - obs_days, init + n_fc).astype("datetime64[ns]")
    rmm = rng.normal(0, 1, (len(times), members, 2))
    is_fc = times >= init
    bom = np.where(is_fc[:, None], np.nan, rmm.mean(axis=1))
    ds = xr.Dataset(
        {
            "rmm1": (("time", "number"), rmm[..., 0]),
            "rmm2": (("time", "number"), rmm[..., 1]),
            "rmm1_mean": ("time", rmm[..., 0].mean(1)),
            "rmm2_mean": ("time", rmm[..., 1].mean(1)),
            "rmm1_bom": ("time", bom[:, 0]),
            "rmm2_bom": ("time", bom[:, 1]),
        },
        coords={"time": times, "number": np.arange(members), "is_forecast": ("time", is_fc)},
        attrs={"forecast_reference_time": str(init), "eof_years": "2006-2022"},
    )
    ds["time"].attrs.update(standard_name="time", axis="T")
    return ds


def write_zarr(ds, path):
    """Write a consolidated Zarr store."""
    ds.to_zarr(path, mode="w", consolidated=True)
    return path


def load_skill(skill_dir_name, script_stem):
    """Import a skill script module by directory name and script stem."""
    path = SKILLS_ROOT / skill_dir_name / "scripts" / f"{script_stem}.py"
    if not path.is_file():
        raise FileNotFoundError(path)
    mod_name = f"skill_{skill_dir_name}_{script_stem}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load skill script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_skill(fn, *argv):
    """Invoke a decorated skill with an argv list (in-process)."""
    return fn(list(argv))
