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


def write_tif(path, fill=0.5, sea_cols=0, west=33.0, east_fill=None):
    """A cropland-map-like float32 tile: `fill` fraction, the first `sea_cols` columns nodata.

    With `east_fill`, the eastern half of the tile (from west + 1°) holds that fraction instead.
    """
    data = np.full((SIZE, SIZE), fill, dtype="float32")
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


def write_grid(path, lats, lons):
    """A precipitation-like gridded Zarr on the given cell centres."""
    from conftest import make_gridded, write_zarr

    return write_zarr(make_gridded(n_time=1, lats=lats, lons=lons), path)


# A 0.25° grid with centres on multiples of 0.25 (edges at x.125, like ERA5), latitude
# descending, over the tile's 33.25-34.75°E, 4.25-5.75°N.
OFFSET_LATS = tuple(np.arange(5.75, 4.2, -0.25))
OFFSET_LONS = tuple(np.arange(33.25, 34.8, 0.25))


def test_reference_grid_cells(tmp_path, mod):
    # 0.1 cropland west of 34°E, 0.8 east of it.
    src = write_tif(tmp_path / "crop.tif", fill=0.1, east_fill=0.8)
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    ds = fetch(mod, tmp_path / "o.zarr", "--source-file", str(src), "--reference-grid", str(ref))
    np.testing.assert_allclose(ds["latitude"].values, sorted(OFFSET_LATS), atol=1e-5)
    np.testing.assert_allclose(ds["longitude"].values, OFFSET_LONS, atol=1e-5)
    row = ds["cropland_fraction"].isel(time=0, latitude=0).values
    # 33.875-34.125 straddles 34°E: half 0.1, half 0.8.
    np.testing.assert_allclose(row, [0.1, 0.1, 0.1, 0.45, 0.8, 0.8, 0.8], atol=1e-4)


def test_area_geojson_clips_exactly_the_cells_above_threshold(tmp_path, mod):
    from shapely.geometry import shape
    from weather_skills_core.standard_utils import clip_by_geometry

    src = write_tif(tmp_path / "crop.tif", fill=0.1, east_fill=0.8)
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    gj = tmp_path / "ag.geojson"
    fetch(
        mod,
        tmp_path / "o.zarr",
        "--source-file",
        str(src),
        "--reference-grid",
        str(ref),
        "--area-geojson",
        str(gj),
        "--min-fraction",
        "0.3",
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
    src = write_tif(tmp_path / "crop.tif", fill=0.1, east_fill=0.8)
    gj = tmp_path / "ag.geojson"
    fetch(
        mod,
        tmp_path / "o.zarr",
        "--source-file",
        str(src),
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


def test_no_cell_reaches_min_fraction(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "crop.tif", fill=0.1)
    with pytest.raises(SystemExit) as exc:
        fetch(
            mod,
            tmp_path / "o.zarr",
            "--source-file",
            str(src),
            "--bbox",
            "5/33/4/34",
            "--area-geojson",
            str(tmp_path / "ag.geojson"),
        )
    assert exc.value.code == 1
    assert "lower --min-fraction" in capsys.readouterr().err


def test_grid_argument_errors(tmp_path, mod, capsys):
    src = write_tif(tmp_path / "crop.tif")
    ref = write_grid(tmp_path / "ref.zarr", OFFSET_LATS, OFFSET_LONS)
    for argv, needle in (
        (["--reference-grid", str(ref), "--resolution", "0.25"], "not both"),
        ([], "pass --bbox"),
    ):
        with pytest.raises(SystemExit) as exc:
            fetch(mod, tmp_path / "o.zarr", "--source-file", str(src), *argv)
        assert exc.value.code == 2
        assert needle in capsys.readouterr().err
