"""Correctness tests for cropland-fetch (synthetic Global Synergy Cropland-style GeoTIFFs)."""

import json

import numpy as np
import pytest
import rasterio
import xarray as xr
from conftest import load_skill, run_skill
from rasterio.transform import from_origin

# The source stores the 15" cell size rounded, as the real file does.
STORED_RES = 0.0041666667
RES = 1 / 240
NODATA = -3.4028230607370965e38
NORTH, SIZE = 6.0, 480  # 2° x 2° tile, 6°N..4°N


@pytest.fixture(scope="module")
def mod():
    return load_skill("cropland-fetch", "fetch")


def write_tif(path, fill=0.5, sea_cols=0, west=33.0):
    """A cropland-map-like float32 tile: `fill` fraction, the first `sea_cols` columns nodata."""
    data = np.full((SIZE, SIZE), fill, dtype="float32")
    data[:, :sea_cols] = NODATA
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, NORTH, STORED_RES, STORED_RES),
        nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
    return path


def fetch(mod, out, *argv):
    run_skill(mod.fetch, "-o", str(out), *argv)
    return xr.open_zarr(out, consolidated=True)


def cell_area(lat):
    return (6371.0088 * np.deg2rad(RES)) ** 2 * np.cos(np.deg2rad(lat))


def test_native_box(tmp_path, mod):
    src = write_tif(tmp_path / "crop.tif")
    ds = fetch(mod, tmp_path / "o.zarr", "--source-file", str(src), "--bbox", "5.5/33.5/4.5/34.5")
    assert ds.sizes == {"time": 1, "latitude": 240, "longitude": 240}
    assert ds["time"].values[0] == np.datetime64("2010-01-01", "ns")
    lat = ds["latitude"].values
    assert (np.diff(lat) > 0).all()
    assert lat[0] == pytest.approx(4.5 + RES / 2, abs=1e-5)
    assert ds["longitude"].values[0] == pytest.approx(33.5 + RES / 2, abs=1e-5)
    np.testing.assert_allclose(ds["cropland_fraction"].values, 0.5)
    assert ds["cropland_fraction"].attrs["units"] == "1"
    assert ds["cropland_area"].attrs["units"] == "km2"
    a = float(ds["cropland_area"].isel(time=0, latitude=120, longitude=0))
    assert a == pytest.approx(0.5 * cell_area(5.0), rel=1e-3)
    assert ds.attrs["license"].startswith("CC0")


def test_resolution_averages_fraction_and_sums_area(tmp_path, mod):
    src = write_tif(tmp_path / "crop.tif")
    ds = fetch(
        mod,
        tmp_path / "o.zarr",
        "--source-file",
        str(src),
        "--bbox",
        "5.6/33.4/4.6/34.4",
        "--resolution",
        "0.25",
    )
    np.testing.assert_allclose(
        ds["longitude"].values, [33.375, 33.625, 33.875, 34.125, 34.375], atol=1e-5
    )
    np.testing.assert_allclose(ds["cropland_fraction"].values, 0.5, rtol=1e-6)
    a = float(ds["cropland_area"].isel(time=0, latitude=0, longitude=0))
    assert a == pytest.approx(0.5 * 60 * 60 * cell_area(4.625), rel=1e-3)


def test_sea_is_nan_and_fraction_is_over_land(tmp_path, mod):
    src = write_tif(tmp_path / "crop.tif", sea_cols=30)  # half of the first 0.25° cell
    ds = fetch(
        mod,
        tmp_path / "o.zarr",
        "--source-file",
        str(src),
        "--bbox",
        "6/33/5.75/33.5",
        "--resolution",
        "0.25",
    )
    frac = ds["cropland_fraction"].isel(time=0, latitude=0).values
    np.testing.assert_allclose(frac, [0.5, 0.5], rtol=1e-6)  # not diluted by the sea half
    area = ds["cropland_area"].isel(time=0, latitude=0).values
    assert area[0] == pytest.approx(area[1] / 2, rel=1e-3)


def test_all_sea_box_is_a_data_error(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "crop.tif", sea_cols=30)
    with pytest.raises(SystemExit) as exc:
        fetch(mod, tmp_path / "o.zarr", "--source-file", str(src), "--bbox", "6/33/5/33.1")
    assert exc.value.code == 1
    assert "no land cells" in capsys.readouterr().err


def test_mask_geojson(tmp_path, mod):
    src = write_tif(tmp_path / "crop.tif")
    poly = {
        "type": "Polygon",
        "coordinates": [[[33.0, 4.0], [34.0, 4.0], [34.0, 6.0], [33.0, 6.0], [33.0, 4.0]]],
    }
    gj = tmp_path / "half.geojson"
    gj.write_text(json.dumps(poly))
    ds = fetch(
        mod,
        tmp_path / "o.zarr",
        "--source-file",
        str(src),
        "--bbox",
        "6/33/4/35",
        "--mask-geojson",
        str(gj),
    )
    frac = ds["cropland_fraction"].isel(time=0)
    assert np.isfinite(frac.sel(longitude=slice(33, 34)).values).all()
    assert np.isnan(frac.sel(longitude=slice(34.01, 35)).values).all()


def test_default_reads_bundled_kenya(tmp_path, mod, monkeypatch, capsys):
    monkeypatch.delenv("CROPLAND_FILE", raising=False)
    monkeypatch.setattr(mod, "_REMOTE", "https://example.invalid/crop.tif")
    ds = fetch(mod, tmp_path / "o.zarr", "--bbox", "1.25/34.75/0.75/35.25", "--resolution", "0.25")
    assert "bundled Kenya cut-out" in capsys.readouterr().err
    assert ds.sizes == {"time": 1, "latitude": 2, "longitude": 2}
    # Trans Nzoia / Kitale farmland.
    assert float(ds["cropland_fraction"].mean()) > 0.3


def test_default_outside_kenya_reads_hosted(tmp_path, mod, monkeypatch, capsys):
    monkeypatch.delenv("CROPLAND_FILE", raising=False)
    tile = write_tif(tmp_path / "crop.tif", west=31.0)
    monkeypatch.setattr(mod, "_REMOTE", str(tile))
    ds = fetch(mod, tmp_path / "o.zarr", "--bbox", "5/32/4/32.5")
    assert "hosted" in capsys.readouterr().err
    assert ds.sizes == {"time": 1, "latitude": 240, "longitude": 120}


def test_env_var_source(tmp_path, mod, monkeypatch):
    monkeypatch.setenv("CROPLAND_FILE", str(write_tif(tmp_path / "crop.tif", fill=0.25)))
    ds = fetch(mod, tmp_path / "o.zarr", "--bbox", "5/33/4/34")
    np.testing.assert_allclose(ds["cropland_fraction"].values, 0.25)


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--bbox", "5/33/4/34", "--resolution", "0.003"], "whole multiple"),
        (["--bbox", "-10/50/-20/60"], "reaches outside"),
    ],
)
def test_usage_errors(tmp_path, mod, capsys, argv, needle):
    argv = ["--source-file", str(write_tif(tmp_path / "crop.tif")), *argv]
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, *argv, "-o", str(tmp_path / "o.zarr"))
    assert exc.value.code == 2
    assert needle in capsys.readouterr().err


def test_missing_source_file(tmp_path, mod, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch,
            "--source-file",
            str(tmp_path / "nope.tif"),
            "--bbox",
            "5/33/4/34",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert exc.value.code == 2
    assert "does not exist" in capsys.readouterr().err
