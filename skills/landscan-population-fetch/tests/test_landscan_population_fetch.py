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


def write_tif(path, fill=5, sea_cols=0, west=WEST, east_fill=None):
    """A LandScan-like int32 tile: `fill` people per cell, the first `sea_cols` columns nodata.

    With `east_fill`, the eastern half of the tile (from west + 1°) holds that count instead.
    """
    data = np.full((SIZE, SIZE), fill, dtype="int32")
    if east_fill is not None:
        data[:, SIZE // 2 :] = east_fill
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


def write_grid(path, lats, lons):
    """A precipitation-like gridded Zarr on the given cell centres."""
    from conftest import make_gridded, write_zarr

    return write_zarr(make_gridded(n_time=1, lats=lats, lons=lons), path)


# A 0.25° grid with centres on multiples of 0.25 (edges at x.125, like ERA5), latitude
# descending, over the tile's 33.25-34.75°E, 4.25-5.75°N.
OFFSET_LATS = tuple(np.arange(5.75, 4.2, -0.25))
OFFSET_LONS = tuple(np.arange(33.25, 34.8, 0.25))


def test_reference_grid_cells(tmp_path, mod):
    # 5 people per 30" cell west of 34°E (~6 km-2), 200 east of it (~233 km-2).
    src = write_tif(tmp_path / "landscan-global-2026.tif", east_fill=200)
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    ds = fetch(mod, src, tmp_path / "o.zarr", "--reference-grid", str(ref))
    np.testing.assert_allclose(ds["latitude"].values, sorted(OFFSET_LATS), atol=1e-5)
    np.testing.assert_allclose(ds["longitude"].values, OFFSET_LONS, atol=1e-5)
    row = ds["population"].isel(time=0, latitude=0).values
    # 30 x 30 native cells per 0.25° cell; 33.875-34.125 straddles 34°E.
    np.testing.assert_allclose(
        row, [4500, 4500, 4500, 15 * 30 * 5 + 15 * 30 * 200, 180000, 180000, 180000]
    )


def test_area_geojson_clips_exactly_the_cells_above_threshold(tmp_path, mod):
    from shapely.geometry import shape
    from weather_skills_core.standard_utils import clip_by_geometry

    src = write_tif(tmp_path / "landscan-global-2026.tif", east_fill=200)
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    gj = tmp_path / "pa.geojson"
    fetch(
        mod,
        src,
        tmp_path / "o.zarr",
        "--reference-grid",
        str(ref),
        "--area-geojson",
        str(gj),
        "--min-density",
        "100",
    )
    feature = json.loads(gj.read_text())["features"][0]
    assert feature["properties"]["cells"] == 7 * 4
    # The same clip clip-region --geojson runs, on the reference grid itself.
    with xr.open_zarr(ref) as grid:
        clipped = clip_by_geometry(grid.load(), shape(feature["geometry"]))
    kept = clipped["precip"].isel(time=0).notnull()
    np.testing.assert_allclose(kept["longitude"].values, OFFSET_LONS[3:], atol=1e-5)
    assert bool(kept.all())
    assert kept.sizes["latitude"] == 7


def test_area_geojson_on_resolution_grid(tmp_path, mod):
    src = write_tif(tmp_path / "landscan-global-2026.tif", east_fill=200)
    gj = tmp_path / "pa.geojson"
    fetch(
        mod,
        src,
        tmp_path / "o.zarr",
        "--bbox",
        "6/33/4/35",
        "--resolution",
        "0.5",
        "--area-geojson",
        str(gj),
    )
    geom = json.loads(gj.read_text())["features"][0]["geometry"]
    xs = [x for ring in geom["coordinates"] for x, _ in ring]
    assert min(xs) == pytest.approx(34.005, abs=1e-6)  # the 34-35°E half, shrunk 1% of a cell
    assert max(xs) == pytest.approx(34.995, abs=1e-6)


def test_no_cell_reaches_min_density(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "landscan-global-2026.tif")
    with pytest.raises(SystemExit) as exc:
        fetch(
            mod,
            src,
            tmp_path / "o.zarr",
            "--bbox",
            "5/33/4/34",
            "--area-geojson",
            str(tmp_path / "pa.geojson"),
        )
    assert exc.value.code == 1
    assert "lower --min-density" in capsys.readouterr().err


def test_grid_argument_errors(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "landscan-global-2026.tif")
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    for argv, needle in (
        (["--reference-grid", str(ref), "--resolution", "0.25"], "not both"),
        ([], "pass --bbox"),
        (["--bbox", "5/33/4/34", "--min-density", "0"], "above 0"),
    ):
        with pytest.raises(SystemExit) as exc:
            fetch(mod, src, tmp_path / "o.zarr", *argv)
        assert exc.value.code == 2
        assert needle in capsys.readouterr().err
