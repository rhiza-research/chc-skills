"""Correctness tests for landscan-population-fetch (synthetic LandScan-style GeoTIFFs)."""

import json
import zipfile

import numpy as np
import pytest
import rasterio
import xarray as xr
from conftest import load_skill, run_skill
from rasterio.transform import from_origin

RES = 1 / 120
NODATA = -2147483647
# A 2° x 2° tile with its origin on the global 30" grid: 33°E..35°E, 6°N..4°N.
WEST, NORTH, SIZE = 33.0, 6.0, 240


@pytest.fixture(scope="module")
def mod():
    return load_skill("landscan-population-fetch", "fetch")


def write_tif(path, fill=5, sea_cols=0, west=WEST):
    """A LandScan-like int32 tile: `fill` people per cell, the first `sea_cols` columns nodata."""
    data = np.full((SIZE, SIZE), fill, dtype="int32")
    data[:, :sea_cols] = NODATA
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=1,
        dtype="int32",
        crs="EPSG:4326",
        transform=from_origin(west, NORTH, RES, RES),
        nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
    return path


def fetch(mod, src, out, *argv):
    run_skill(mod.fetch, "--source-file", str(src), "-o", str(out), *argv)
    return xr.open_zarr(out, consolidated=True)


def test_native_box_counts_and_coords(tmp_path, mod):
    src = write_tif(tmp_path / "landscan-global-2026.tif")
    ds = fetch(mod, src, tmp_path / "out.zarr", "--bbox", "5.5/33.5/4.5/34.5")
    assert ds.sizes == {"time": 1, "latitude": 120, "longitude": 120}
    assert ds["time"].values[0] == np.datetime64("2026-01-01", "ns")
    lat = ds["latitude"].values
    assert (np.diff(lat) > 0).all()  # south to north
    assert lat[0] == pytest.approx(4.5 + RES / 2, abs=1e-5)
    assert float(ds["population"].sum()) == pytest.approx(5 * 120 * 120)
    assert ds["population"].attrs["units"] == "1"
    assert ds["population_density"].attrs["units"] == "km-2"
    # A 30" cell near 5°N is ~0.857 km2: 5 people -> ~5.8 per km2.
    dens = float(ds["population_density"].isel(time=0, latitude=60, longitude=0))
    assert dens == pytest.approx(
        5 / ((6371.0088 * np.deg2rad(RES)) ** 2 * np.cos(np.deg2rad(5))), rel=1e-3
    )
    np.testing.assert_allclose(
        ds["population_density_log10"].values, np.log10(ds["population_density"].values), rtol=1e-5
    )


def test_resolution_sums_and_aligns_to_grid(tmp_path, mod):
    src = write_tif(tmp_path / "landscan-global-2026.tif")
    # The box edges fall mid-cell of the 0.25° grid; output cells snap outward to it.
    ds = fetch(
        mod, src, tmp_path / "out.zarr", "--bbox", "5.6/33.4/4.6/34.4", "--resolution", "0.25"
    )
    np.testing.assert_allclose(
        ds["longitude"].values, [33.375, 33.625, 33.875, 34.125, 34.375], atol=1e-5
    )
    np.testing.assert_allclose(ds["latitude"].values[[0, -1]], [4.625, 5.625], atol=1e-5)
    np.testing.assert_allclose(ds["population"].values, 5 * 30 * 30)


def test_sea_is_nan_and_density_uses_land_area(tmp_path, mod):
    # First 15 native columns (half of the first 0.25° cell) are sea.
    src = write_tif(tmp_path / "landscan-global-2026.tif", sea_cols=15)
    ds = fetch(mod, src, tmp_path / "out.zarr", "--bbox", "6/33/5.75/33.5", "--resolution", "0.25")
    pop = ds["population"].isel(time=0, latitude=0).values
    np.testing.assert_allclose(pop, [5 * 30 * 15, 5 * 30 * 30])
    dens = ds["population_density"].isel(time=0, latitude=0).values
    assert dens[0] == pytest.approx(dens[1], rel=1e-6)  # not diluted by the sea half

    ds2 = fetch(mod, src, tmp_path / "sea.zarr", "--bbox", "6/33/5/33.2")
    assert np.isnan(ds2["population"].isel(longitude=slice(0, 15)).values).all()
    assert (ds2["population"].isel(longitude=slice(15, None)).values == 5).all()


def test_all_sea_box_is_a_data_error(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "landscan-global-2026.tif", sea_cols=15)
    with pytest.raises(SystemExit) as exc:
        fetch(mod, src, tmp_path / "out.zarr", "--bbox", "6/33/5/33.1")
    assert exc.value.code == 1
    assert "no land cells" in capsys.readouterr().err


def test_mask_geojson_blanks_outside(tmp_path, mod):
    src = write_tif(tmp_path / "landscan-global-2026.tif")
    poly = {
        "type": "Polygon",
        "coordinates": [[[33.0, 4.0], [34.0, 4.0], [34.0, 6.0], [33.0, 6.0], [33.0, 4.0]]],
    }
    gj = tmp_path / "half.geojson"
    gj.write_text(json.dumps(poly))
    ds = fetch(mod, src, tmp_path / "out.zarr", "--bbox", "6/33/4/35", "--mask-geojson", str(gj))
    pop = ds["population"].isel(time=0)
    assert float(pop.sel(longitude=slice(33, 34)).sum()) == pytest.approx(5 * 240 * 120)
    assert np.isnan(pop.sel(longitude=slice(34.01, 35)).values).all()


def test_reads_release_zip_and_skips_colorized(tmp_path, mod):
    tif = write_tif(tmp_path / "landscan-global-2026.tif")
    zpath = tmp_path / "landscan-global-2026-assets.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("landscan-global-2026-colorized.tif", b"not counts")
        zf.write(tif, "landscan-global-2026.tif")
    ds = fetch(mod, zpath, tmp_path / "out.zarr", "--bbox", "5/33/4/34")
    assert float(ds["population"].sum()) == pytest.approx(5 * 120 * 120)
    assert "2026" in ds.attrs["citation"]


def test_env_var_source(tmp_path, mod, monkeypatch):
    src = write_tif(tmp_path / "landscan-global-2025.tif")
    monkeypatch.setenv("LANDSCAN_FILE", str(src))
    run_skill(mod.fetch, "--bbox", "5/33/4/34", "-o", str(tmp_path / "out.zarr"))
    with xr.open_zarr(tmp_path / "out.zarr") as ds:
        assert ds["time"].values[0] == np.datetime64("2025-01-01", "ns")


def test_default_reads_bundled_kenya(tmp_path, mod, monkeypatch, capsys):
    monkeypatch.delenv("LANDSCAN_FILE", raising=False)
    monkeypatch.setattr(mod, "_REMOTE", "https://example.invalid/landscan-global-2026.tif")
    ds = fetch_default(mod, tmp_path, "--bbox", "-1/36.5/-1.5/37", "--resolution", "0.25")
    assert "bundled Kenya cut-out" in capsys.readouterr().err
    assert ds.sizes == {"time": 1, "latitude": 2, "longitude": 2}
    assert float(ds["population"].sum()) > 1e6  # Nairobi and surroundings
    assert ds.attrs["license"].startswith("CC BY 4.0")


def test_default_outside_kenya_reads_hosted_global(tmp_path, mod, monkeypatch, capsys):
    monkeypatch.delenv("LANDSCAN_FILE", raising=False)
    # Stand-in for the hosted global COG: a local tile outside the bundled box (west of 33°E).
    tile = write_tif(tmp_path / "landscan-global-2026.tif", west=31.0)
    monkeypatch.setattr(mod, "_REMOTE", str(tile))
    ds = fetch_default(mod, tmp_path, "--bbox", "5/32/4/32.5")
    assert "hosted" in capsys.readouterr().err
    assert float(ds["population"].sum()) == pytest.approx(5 * 120 * 60)


def fetch_default(mod, tmp_path, *argv):
    out = tmp_path / "default.zarr"
    run_skill(mod.fetch, "-o", str(out), *argv)
    return xr.open_zarr(out, consolidated=True)


def test_missing_source_file(tmp_path, mod, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch,
            "--source-file",
            str(tmp_path / "nope" / "landscan-global-2026.tif"),
            "--bbox",
            "5/33/4/34",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert exc.value.code == 2
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--bbox", "5/33/4/34", "--resolution", "0.03"], "whole multiple"),
        (["--bbox", "-10/50/-20/60"], "reaches outside"),
    ],
)
def test_usage_errors(tmp_path, mod, monkeypatch, capsys, argv, needle):
    monkeypatch.delenv("LANDSCAN_FILE", raising=False)
    argv = ["--source-file", str(write_tif(tmp_path / "landscan-global-2026.tif")), *argv]
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, *argv, "-o", str(tmp_path / "out.zarr"))
    assert exc.value.code == 2
    assert needle in capsys.readouterr().err


def test_unnamed_tif_is_refused(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "population.tif")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch,
            "--source-file",
            str(src),
            "--bbox",
            "5/33/4/34",
            "-o",
            str(tmp_path / "o.zarr"),
        )
    assert exc.value.code == 2
    assert "landscan-global-YYYY.tif" in capsys.readouterr().err
