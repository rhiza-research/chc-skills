# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "rasterio>=1.3",
#   "shapely>=2",
#   "xarray",
# ]
# ///
"""Read the Global Synergy Cropland Map (2010) for a box as a weather-skills gridded Zarr."""

import math
import os
import sys
from pathlib import Path

from weather_skills_core import DataError, Dataset, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_coords, udunits_error
from weather_skills_core.standard_utils import polygon_from_geojson

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# Source-to-output transform:
# - band 1 float32 cropland fraction of each 15" cell (0-1; the source names it a "percentage
#   map" but stores fractions) -> `cropland_fraction` (units "1"), passed through at native
#   resolution; the nodata value (-3.4e38, the sea) -> NaN. --resolution averages it over the
#   land area of the native cells that hold data (spherical areas, R = 6371.0088 km), so a
#   coastal or masked cell is not diluted by sea.
# - `cropland_area` (km2) is new: fraction x cell area, summed into each output cell.
# - the band's row/column centres -> `latitude` (ascending, south to north) / `longitude`, on
#   the exact 1/240 deg grid (the file stores the cell size as 0.0041666667);
#   one `time` = 2010-01-01, the map's reference year.
_ENV = "CROPLAND_FILE"
_YEAR = 2010
# Kenya cut from the 2010 map (CC0), 6°N-6°S, 33-43.5°E: see data/README.md.
_BUNDLED = Path(__file__).resolve().parents[1] / "data" / "global-synergy-cropland-2010-kenya.tif"
_BUNDLED_BOX = (6.0, 33.0, -6.0, 43.5)  # N, W, S, E
# The whole map as a Cloud-Optimized GeoTIFF in the public africa-forecasting-data bucket:
# only the 512 x 512 tiles a --bbox touches are fetched (HTTP range reads).
_REMOTE = (
    "https://storage.googleapis.com/africa-forecasting-data/cropland/"
    "global-synergy-cropland-2010.tif"
)
_EARTH_RADIUS_KM = 6371.0088
_DOI = "https://doi.org/10.7910/DVN/ZWSFAA"
_PAPER_DOI = "https://doi.org/10.5194/essd-12-1913-2020"


def _edges(centres):
    """Cell edges for ascending cell centres: midpoints, the outer edges half a spacing out."""
    import numpy as np

    c = np.asarray(centres, dtype="float64")
    mids = (c[:-1] + c[1:]) / 2
    return np.concatenate([[c[0] - (c[1] - c[0]) / 2], mids, [c[-1] + (c[-1] - c[-2]) / 2]])


def _write_area_geojson(path, keep, lat_edges, lon_edges, min_fraction):
    """Write the kept output cells (rows south to north) as one polygon, for clip-region.

    The outline runs along cell edges, shrunk by 1% of a cell, so clip-region --geojson (which
    keeps every cell that overlaps a polygon by positive area) keeps exactly these cells of the
    same grid, not their neighbours through a float-rounding sliver.
    """
    import json

    import numpy as np
    import shapely
    from rasterio.features import shapes
    from rasterio.transform import Affine

    dlat, dlon = np.diff(lat_edges), np.diff(lon_edges)
    if np.allclose(dlat, dlat[0], rtol=1e-3) and np.allclose(dlon, dlon[0], rtol=1e-3):
        # Row 0 is the southern row: a transform with a positive y step.
        transform = Affine(dlon[0], 0, lon_edges[0], 0, dlat[0], lat_edges[0])
        parts = [
            shapely.geometry.shape(geom)
            for geom, _ in shapes(keep.astype("uint8"), mask=keep, transform=transform)
        ]
    else:
        rows, cols = np.nonzero(keep)
        parts = list(
            shapely.box(lon_edges[cols], lat_edges[rows], lon_edges[cols + 1], lat_edges[rows + 1])
        )
    shrink = 0.01 * float(min(dlat.min(), dlon.min()))
    area = shapely.union_all(parts).buffer(-shrink, join_style="mitre")
    feature = {
        "type": "Feature",
        "properties": {
            "name": f"cropland >= {min_fraction:g}",
            "min_fraction": min_fraction,
            "cells": int(keep.sum()),
            "source": f"Global Synergy Cropland Map {_YEAR} ({_PAPER_DOI})",
        },
        "geometry": shapely.geometry.mapping(area),
    }
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    print(
        f"Wrote area GeoJSON: {path} ({int(keep.sum())} cells with cropland >= {min_fraction:g})",
        file=sys.stderr,
    )


@weather_skill(name="cropland-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--source-file",
    help=(
        "A copy of Global-cropland-percentage-map.tif (Harvard Dataverse "
        "doi:10.7910/DVN/ZWSFAA), a path or an https:// URL. Default: "
        f"${_ENV}; else the bundled Kenya cut-out when the box lies in 6N-6S, 33-43.5E; "
        "else the hosted global file, read over HTTP."
    ),
)
@weather_skill.argument(
    "--bbox",
    help="N/W/S/E. Required unless --reference-grid is given; with it, trims that grid.",
)
@weather_skill.argument(
    "--resolution",
    type=float,
    help=(
        "Output cell size in degrees, a whole multiple of 15 arc-seconds (1/240 deg), e.g. "
        "0.05 or 0.25; the fraction is averaged over land and the area summed into each "
        "cell. Default: native 1/240 deg."
    ),
)
@weather_skill.argument(
    "--reference-grid",
    type=Dataset("spatial"),
    help=(
        "Zarr whose lat/lon cells become the output grid (e.g. the precipitation to clip), "
        "so the cropland lines up cell for cell with it. Not with --resolution."
    ),
)
@weather_skill.argument(
    "--mask-geojson",
    help=(
        "Boundary polygon (e.g. from resolve-chc-region Kenya --geojson): cells outside are "
        "NaN, applied at native resolution before the output grid aggregates."
    ),
)
@weather_skill.argument(
    "--area-geojson",
    help=(
        "Also write the output cells with cropland_fraction >= --min-fraction as a polygon "
        "GeoJSON, for clip-region --geojson."
    ),
)
@weather_skill.argument(
    "--min-fraction",
    type=float,
    default=0.3,
    help="Cropland fraction (0-1) a cell needs to be in --area-geojson. Default 0.3.",
)
def fetch(
    source_file,
    bbox,
    resolution,
    reference_grid,
    mask_geojson,
    area_geojson,
    min_fraction,
    **kwargs,
):
    """Read the Global Synergy Cropland Map (2010) for a box as a weather-skills gridded Zarr.

    Writes `cropland_fraction` (share of each cell's land under cropland, 0-1) and
    `cropland_area` (km2 of cropland per cell), on one `time` (2010), for overlaying on maps.
    With --area-geojson, also writes the cells above --min-fraction as a polygon for
    clip-region.
    """
    import numpy as np
    import rasterio
    import xarray as xr
    from rasterio.errors import RasterioIOError
    from rasterio.windows import Window
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    if reference_grid is not None and resolution is not None:
        raise UsageError("pass --resolution or --reference-grid, not both")
    if reference_grid is None and bbox is None:
        raise UsageError("pass --bbox, or --reference-grid (its grid then sets the box)")
    if not 0 < min_fraction <= 1:
        raise UsageError(f"--min-fraction {min_fraction} must be in (0, 1]")

    # Output grid from --reference-grid: its cells, ascending, longitudes in [-180, 180).
    ref_lats = ref_lons = None
    if reference_grid is not None:
        lat_dim, lon_dim = detect_spatial_dims(reference_grid)
        grid = xr.Dataset(
            coords={
                lat_dim: np.asarray(reference_grid[lat_dim].values),
                lon_dim: np.asarray(reference_grid[lon_dim].values),
            }
        )
        grid = ensure_normalized_longitude(grid, lon_dim=lon_dim)
        ref_lats = np.sort(np.asarray(grid[lat_dim].values, dtype="float64"))
        ref_lons = np.sort(np.asarray(grid[lon_dim].values, dtype="float64"))
        if bbox is not None:
            bn, bw, bs, be = bbox
            ref_lats = ref_lats[(ref_lats >= bs) & (ref_lats <= bn)]
            ref_lons = ref_lons[(ref_lons >= bw) & (ref_lons <= be)]
        if ref_lats.size < 2 or ref_lons.size < 2:
            raise UsageError("--reference-grid has fewer than 2 x 2 cells inside --bbox")
        ref_lat_edges, ref_lon_edges = _edges(ref_lats), _edges(ref_lons)
        bbox = (
            float(ref_lat_edges[-1]),
            float(ref_lon_edges[0]),
            float(ref_lat_edges[0]),
            float(ref_lon_edges[-1]),
        )

    north, west, south, east = bbox
    source_label = ""
    if source_file is None:
        source_file = os.environ.get(_ENV)
    if not source_file:
        bn, bw, bs, be = _BUNDLED_BOX
        if _BUNDLED.is_file() and south >= bs and north <= bn and west >= bw and east <= be:
            source_file, source_label = str(_BUNDLED), "bundled Kenya cut-out "
        else:
            source_file, source_label = _REMOTE, "hosted "
    remote = source_file.startswith(("http://", "https://"))
    if remote:
        gdal_path, member = f"/vsicurl/{source_file}", source_file.rsplit("/", 1)[-1]
    else:
        path = Path(source_file).expanduser()
        if not path.is_file():
            raise UsageError(f"--source-file {path} does not exist")
        gdal_path, member = str(path), path.name

    def _read_failed(exc):
        if not remote:
            return DataError(f"cannot read {member}: {exc}")
        text = str(exc)
        if "404" in text or "does not exist" in text or "not recognized" in text:
            return DataError(
                f"{source_file} is not available (not published yet, or moved); pass "
                f"--source-file or set {_ENV} to a local copy of the map"
            )
        return DataError(
            f"network error reading {source_file} ({text}); retry, or pass --source-file "
            "with a local copy of the map"
        )

    # Read a remote COG without listing its folder, and only .tif sidecar probes.
    gdal_env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MAX_RETRY="3",
        GDAL_HTTP_RETRY_DELAY="2",
    )
    with gdal_env:
        try:
            src = rasterio.open(gdal_path)
        except RasterioIOError as exc:
            raise _read_failed(exc) from None
    with gdal_env, src:
        t = src.transform
        if t.b != 0 or t.d != 0 or t.e >= 0 or abs(t.a + t.e) > 1e-9:
            raise DataError(f"{member} is not a north-up lat/lon grid with square cells")
        # The file stores the cell size rounded (0.0041666667): snap the grid to exactly
        # 1/n deg, with its corner on a whole cell from 180°W / 90°N.
        n = round(1 / t.a)
        res = 1 / n
        c_off = round((t.c + 180.0) / res)  # global pixel index of the first column / row
        r_off = round((90.0 - t.f) / res)
        if abs(t.a * n - 1) > 1e-6 or abs((t.c + 180.0) / res - c_off) > 1e-3:
            raise DataError(f"{member} is not on a regular 1/n degree grid from 180°W / 90°N")
        left, top = -180.0 + c_off * res, 90.0 - r_off * res
        factor = 1
        if resolution is not None:
            factor = round(resolution / res)
            if factor < 1 or abs(factor * res - resolution) > 1e-6:
                raise UsageError(
                    f"--resolution {resolution} is not a whole multiple of the native "
                    f"{res:.7f} deg (1/{n}); use e.g. 0.05, 0.1, 0.25 or 0.5"
                )
        right, bottom = left + src.width * res, top - src.height * res
        tol = res / 2
        if north > top + tol or south < bottom - tol or west < left - tol or east > right + tol:
            extent = f"{top:.4g}/{left:.4g}/{bottom:.4g}/{right:.4g}"
            raise UsageError(
                f"--bbox {north}/{west}/{south}/{east} reaches outside {member} ({extent})"
            )
        # Native pixel window for the box (global indices), widened to whole output cells
        # counted from 180°W / 90°N, so the global file and a cut-out of it agree.
        eps = 1e-6
        col0 = math.floor((west + 180.0) / res + eps)
        col1 = math.ceil((east + 180.0) / res - eps)
        row0 = math.floor((90.0 - north) / res + eps)
        row1 = math.ceil((90.0 - south) / res - eps)
        col0, row0 = (col0 // factor) * factor, (row0 // factor) * factor
        col1, row1 = -(-col1 // factor) * factor, -(-row1 // factor) * factor
        print(
            f"Reading {source_label}{member} rows {row0 - r_off}:{row1 - r_off}, "
            f"cols {col0 - c_off}:{col1 - c_off}",
            file=sys.stderr,
        )
        # A coarse cell on the raster's edge reads its outside part as nodata: read the part
        # inside the raster and pad, so a remote read fetches only the tiles it needs.
        frac = np.full((row1 - row0, col1 - col0), np.nan, dtype="float64")
        r0, r1 = max(row0 - r_off, 0), min(row1 - r_off, src.height)
        c0, c1 = max(col0 - c_off, 0), min(col1 - c_off, src.width)
        if r1 > r0 and c1 > c0:
            try:
                block = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0)).astype("float64")
            except RasterioIOError as exc:
                raise _read_failed(exc) from None
            if src.nodata is not None:
                block[block == src.nodata] = np.nan
            block[(block < 0) | (block > 1)] = np.nan  # sea (-3.4e38) and any stray fill
            ro, co = r0 + r_off - row0, c0 + c_off - col0
            frac[ro : ro + block.shape[0], co : co + block.shape[1]] = block

    lats = 90.0 - res * (np.arange(row0, row1) + 0.5)
    lons = -180.0 + res * (np.arange(col0, col1) + 0.5)

    if mask_geojson:
        import shapely

        geom = polygon_from_geojson(mask_geojson, flag="--mask-geojson")
        # Cell-centre test at native 15": the ~0.5 km cells are far finer than any overlay
        # grid, and cell-overlap clipping of ~10^6 cells against a detailed outline is slow.
        shapely.prepare(geom)
        lon2d, lat2d = np.meshgrid(lons, lats)
        frac[~shapely.contains_xy(geom, lon2d, lat2d)] = np.nan
        if not np.isfinite(frac).any():
            raise UsageError("--mask-geojson leaves no cropland-map cells inside the box")

    if not np.isfinite(frac).any():
        raise DataError("no land cells in the box (the cropland map has no data over the sea)")

    # Spherical area of each native row's cells, in km2, counted only where there is data.
    edges = np.deg2rad(90.0 - res * np.arange(row0, row1 + 1))
    row_area = (_EARTH_RADIUS_KM**2) * np.deg2rad(res) * np.abs(np.diff(np.sin(edges)))

    # Each native cell goes to the output cell holding its centre: a block of `factor` x
    # `factor` cells on the 1/240 grid, or a --reference-grid cell. -1 means no output cell.
    # Output rows run south to north.
    if ref_lats is not None:
        out_lats, out_lons = ref_lats, ref_lons
        lat_edges, lon_edges = ref_lat_edges, ref_lon_edges
        iy = np.searchsorted(lat_edges, lats, side="right") - 1
        ix = np.searchsorted(lon_edges, lons, side="right") - 1
        iy[(iy < 0) | (iy >= out_lats.size)] = -1
        ix[(ix < 0) | (ix >= out_lons.size)] = -1
    else:
        ny, nx = (row1 - row0) // factor, (col1 - col0) // factor
        lat_edges = 90.0 - res * (row0 + factor * np.arange(ny + 1))[::-1]
        lon_edges = -180.0 + res * (col0 + factor * np.arange(nx + 1))
        out_lats, out_lons = (
            (lat_edges[:-1] + lat_edges[1:]) / 2,
            (lon_edges[:-1] + lon_edges[1:]) / 2,
        )
        iy = ny - 1 - np.arange(row1 - row0) // factor
        ix = np.arange(col1 - col0) // factor
    ok = np.isfinite(frac) & (iy[:, None] >= 0) & (ix[None, :] >= 0)
    rr, cc = np.nonzero(ok)
    flat = iy[rr] * out_lons.size + ix[cc]
    size, shape = out_lats.size * out_lons.size, (out_lats.size, out_lons.size)
    weights = row_area[rr]
    area = np.bincount(flat, weights=weights, minlength=size).reshape(shape)
    crop = np.bincount(flat, weights=frac[rr, cc] * weights, minlength=size).reshape(shape)
    empty = np.bincount(flat, minlength=size).reshape(shape) == 0
    area[empty], crop[empty] = np.nan, np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = crop / area

    time = np.array([np.datetime64(f"{_YEAR}-01-01", "ns")])

    def var(a):
        return (("time", "latitude", "longitude"), a[None].astype("float32"))

    ds = xr.Dataset(
        {"cropland_fraction": var(frac), "cropland_area": var(crop)},
        coords={"time": time, "latitude": out_lats, "longitude": out_lons},
    )
    cell = "--reference-grid" if ref_lats is not None else f"{factor * res:.6g} deg"
    ds["cropland_fraction"].attrs = {
        "units": "1",
        "long_name": f"Fraction of land under cropland, {_YEAR} (0-1), per {cell} cell",
        "cell_methods": "area: mean where land",
    }
    ds["cropland_area"].attrs = {
        "units": "km2",
        "long_name": f"Cropland area per {cell} cell, {_YEAR}",
        "cell_methods": "area: sum",
    }
    for name, v in ds.data_vars.items():
        err = udunits_error(v.attrs["units"])
        if err is not None:
            raise DataError(f"{name} units {v.attrs['units']!r} do not parse: {err}")
    ds["time"].attrs["long_name"] = "cropland map reference year"
    stamp_cf_coords(ds)
    ds.attrs.update(
        Conventions="CF-1.13",
        title=f"Global Synergy Cropland Map {_YEAR}",
        institution="Chinese Academy of Agricultural Sciences; IFPRI; IIASA",
        source=(
            f"Global Synergy Cropland Map ({_YEAR}), 15 arc-second cropland fraction from the "
            f"SASAM fusion of existing maps and statistics ({member})"
        ),
        references=f"{_DOI} {_PAPER_DOI}",
        license="CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)",
        citation=(
            "Lu, M., Wu, W., You, L., See, L., Fritz, S., Yu, Q., Wei, Y., Chen, D., Yang, P. "
            "and Xue, B. (2020). A cultivated planet in 2010 - Part 1: The global synergy "
            f"cropland map. Earth System Science Data, 12, 1913-1928. {_PAPER_DOI}. "
            f"Data: Harvard Dataverse, {_DOI}"
        ),
        weather_skills_source=f"global-synergy-cropland:{_YEAR}",
    )
    total = float(np.nansum(crop))
    print(
        f"Cropland {_YEAR}: {ds.sizes['latitude']}x{ds.sizes['longitude']} cells of {cell}, "
        f"{total:,.0f} km2 of cropland",
        file=sys.stderr,
    )
    if area_geojson:
        keep = np.nan_to_num(frac, nan=0.0) >= min_fraction
        if not keep.any():
            raise DataError(
                f"no cell has cropland_fraction >= {min_fraction:g} (the highest is "
                f"{np.nanmax(frac):.2f}); lower --min-fraction"
            )
        _write_area_geojson(area_geojson, keep, lat_edges, lon_edges, min_fraction)
    return ds


if __name__ == "__main__":
    fetch()
