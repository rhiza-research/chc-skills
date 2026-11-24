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
"""Read LandScan Global population counts for a box as a weather-skills gridded Zarr."""

import math
import os
import re
import sys
import zipfile
from pathlib import Path

from weather_skills_core import DataError, Dataset, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_coords, udunits_error
from weather_skills_core.standard_utils import polygon_from_geojson

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# Source-to-output transform:
# - band 1 int32 people per 30" cell -> `population` (float32, units "1"); the nodata value
#   (-2147483647, the sea) -> NaN. Counts are summed, never averaged, by --resolution.
# - `population_density` (km-2) is new: people / land area of the cells that hold data
#   (spherical cell areas, R = 6371.0088 km), so a coastal or masked cell is not diluted by sea.
# - `population_density_log10` (units "1") is log10 of the density, NaN where it is 0: plot
#   contour levels are linear, so 1/2/3 here draw the 10/100/1000 km-2 lines.
# - the band's row/column centres -> `latitude` (ascending, south to north) / `longitude`;
#   one `time` = 1 January of the release year read from the file name.
_ENV = "LANDSCAN_FILE"
# landscan-global-2026.tif, or a cut-out such as landscan-global-2026-kenya.tif.
_NAME_RE = re.compile(r"landscan-global-(\d{4})(?:-[a-z0-9_]+)?\.tif$", re.IGNORECASE)
# Kenya cut from LandScan Global 2026 (CC BY 4.0), 6°N-6°S, 33-43.5°E: see data/README.md.
_BUNDLED = Path(__file__).resolve().parents[1] / "data" / "landscan-global-2026-kenya.tif"
_BUNDLED_BOX = (6.0, 33.0, -6.0, 43.5)  # N, W, S, E
# The whole release as a Cloud-Optimized GeoTIFF in the public africa-forecasting-data bucket:
# only the 512 x 512 tiles a --bbox touches are fetched (HTTP range reads).
_REMOTE = "https://storage.googleapis.com/africa-forecasting-data/landscan/landscan-global-2026.tif"
_EARTH_RADIUS_KM = 6371.0088
_DOI = "https://doi.org/10.48690/LSG/0EXD-MK45"


def _find_tif(source: str) -> tuple[str, int, str]:
    """GDAL path of the LandScan count GeoTIFF in `source` (a URL, a .tif or the release .zip)."""
    if source.startswith(("http://", "https://")):
        name = source.rsplit("/", 1)[-1]
        match = _NAME_RE.search(name)
        if not match:
            raise UsageError(f"{source} does not end in landscan-global-YYYY.tif")
        return f"/vsicurl/{source}", int(match.group(1)), name
    source = Path(source).expanduser()
    if not source.exists():
        raise UsageError(f"--source-file {source} does not exist")
    if source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            raise DataError(f"{source} is not a readable zip") from None
        # The colorized RGB file is a picture, not counts: skip it.
        hits = [
            n
            for n in names
            if _NAME_RE.search(n.rsplit("/", 1)[-1]) and "colorized" not in n.lower()
        ]
        if not hits:
            raise DataError(
                f"{source} holds no landscan-global-YYYY.tif count raster "
                f"(it has {', '.join(names)})"
            )
        member = hits[0]
        year = int(_NAME_RE.search(member).group(1))
        return f"/vsizip/{source.resolve().as_posix()}/{member}", year, member
    match = _NAME_RE.search(source.name)
    if not match or "colorized" in source.name.lower():
        raise UsageError(
            f"{source.name} is not named landscan-global-YYYY.tif; keep the release file name "
            "so the year can be read from it (or pass the release .zip)"
        )
    return str(source), int(match.group(1)), source.name


def _edges(centres):
    """Cell edges for ascending cell centres: midpoints, the outer edges half a spacing out."""
    import numpy as np

    c = np.asarray(centres, dtype="float64")
    mids = (c[:-1] + c[1:]) / 2
    return np.concatenate([[c[0] - (c[1] - c[0]) / 2], mids, [c[-1] + (c[-1] - c[-2]) / 2]])


def _write_area_geojson(path, keep, lat_edges, lon_edges, min_density, year):
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
            "name": f"population density >= {min_density:g} km-2",
            "min_density": min_density,
            "cells": int(keep.sum()),
            "source": f"LandScan Global {year} ({_DOI})",
        },
        "geometry": shapely.geometry.mapping(area),
    }
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    print(
        f"Wrote area GeoJSON: {path} ({int(keep.sum())} cells with >= {min_density:g} "
        "people per km2)",
        file=sys.stderr,
    )


@weather_skill(name="landscan-population-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--source-file",
    help=(
        "A LandScan Global release: the downloaded landscan-global-YYYY-assets.zip, the "
        "landscan-global-YYYY.tif inside it, or an https:// URL of that .tif. Default: "
        f"${_ENV}; else the bundled 2026 Kenya cut-out when the box lies in 6N-6S, 33-43.5E; "
        "else the hosted 2026 global file, read over HTTP."
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
        "Output cell size in degrees, a whole multiple of 30 arc-seconds (1/120 deg), e.g. "
        "0.05 or 0.25; counts are summed into each cell. Default: native 1/120 deg."
    ),
)
@weather_skill.argument(
    "--reference-grid",
    type=Dataset("spatial"),
    help=(
        "Zarr whose lat/lon cells become the output grid (e.g. the precipitation to clip), "
        "so the population lines up cell for cell with it. Not with --resolution."
    ),
)
@weather_skill.argument(
    "--mask-geojson",
    help=(
        "Boundary polygon (e.g. from resolve-chc-region Kenya --geojson): cells outside are "
        "NaN, applied at native resolution before the output grid sums."
    ),
)
@weather_skill.argument(
    "--area-geojson",
    help=(
        "Also write the output cells with population_density >= --min-density as a polygon "
        "GeoJSON, for clip-region --geojson."
    ),
)
@weather_skill.argument(
    "--min-density",
    type=float,
    default=100.0,
    help="People per km2 a cell needs to be in --area-geojson. Default 100.",
)
def fetch(
    source_file,
    bbox,
    resolution,
    reference_grid,
    mask_geojson,
    area_geojson,
    min_density,
    **kwargs,
):
    """Read LandScan Global population counts for a box as a weather-skills gridded Zarr.

    Writes `population` (people per cell), `population_density` (people per km2 of land)
    and `population_density_log10`, on one `time` (the release year), for overlaying on maps.
    With --area-geojson, also writes the cells above --min-density as a polygon for
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
    if min_density <= 0:
        raise UsageError(f"--min-density {min_density} must be above 0")

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
    gdal_path, year, member = _find_tif(source_file)
    remote = gdal_path.startswith("/vsicurl/")

    def _read_failed(exc):
        if not remote:
            return DataError(f"cannot read {member}: {exc}")
        text = str(exc)
        if "404" in text or "does not exist" in text or "not recognized" in text:
            return DataError(
                f"{source_file} is not available (not published yet, or moved); pass "
                f"--source-file or set {_ENV} to a local LandScan release"
            )
        return DataError(
            f"network error reading {source_file} ({text}); retry, or pass --source-file "
            "with a local LandScan release"
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
        if t.b != 0 or t.d != 0 or t.e >= 0:
            raise DataError(f"{member} is not a north-up lat/lon grid")
        res = t.a
        factor = 1
        if resolution is not None:
            factor = round(resolution / res)
            if factor < 1 or abs(factor * res - resolution) > 1e-6:
                raise UsageError(
                    f"--resolution {resolution} is not a whole multiple of the native "
                    f"{res:.6f} deg (1/120); use e.g. 0.05, 0.1, 0.25 or 0.5"
                )
        b = src.bounds
        tol = res / 2
        if (
            north > b.top + tol
            or south < b.bottom - tol
            or west < b.left - tol
            or east > b.right + tol
        ):
            extent = f"{b.top:.4g}/{b.left:.4g}/{b.bottom:.4g}/{b.right:.4g}"
            raise UsageError(
                f"--bbox {north}/{west}/{south}/{east} reaches outside {member} ({extent})"
            )
        # Native pixel window for the box, widened to whole output cells counted from 180°W /
        # 90°N, so the global file and a cut-out of it put cell edges in the same place.
        # Global pixel indices of this raster's first column and row:
        c_off = round((t.c + 180.0) / res)
        r_off = round((90.0 - t.f) / -t.e)
        eps = 1e-3  # pixels: the stored cell size (0.008333333333) is not exactly 1/120
        col0 = math.floor((west - t.c) / res + eps) + c_off
        col1 = math.ceil((east - t.c) / res - eps) + c_off
        row0 = math.floor((t.f - north) / -t.e + eps) + r_off
        row1 = math.ceil((t.f - south) / -t.e - eps) + r_off
        col0, row0 = (col0 // factor) * factor, (row0 // factor) * factor
        col1, row1 = -(-col1 // factor) * factor, -(-row1 // factor) * factor
        col0, col1, row0, row1 = col0 - c_off, col1 - c_off, row0 - r_off, row1 - r_off
        print(
            f"Reading {source_label}{member} rows {row0}:{row1}, cols {col0}:{col1}",
            file=sys.stderr,
        )
        # A coarse cell on the raster's edge reads its outside part as nodata: read the part
        # inside the raster and pad, so a remote read fetches only the tiles it needs.
        nodata = src.nodata if src.nodata is not None else -1
        raw = np.full((row1 - row0, col1 - col0), nodata, dtype=src.dtypes[0])
        r0, r1 = max(row0, 0), min(row1, src.height)
        c0, c1 = max(col0, 0), min(col1, src.width)
        try:
            raw[r0 - row0 : r1 - row0, c0 - col0 : c1 - col0] = src.read(
                1, window=Window(c0, r0, c1 - c0, r1 - r0)
            )
        except RasterioIOError as exc:
            raise _read_failed(exc) from None

    count = raw.astype("float64")
    if nodata is not None:
        count[raw == nodata] = np.nan
    count[count < 0] = np.nan
    lats = t.f + t.e * (np.arange(row0, row1) + 0.5)
    lons = t.c + res * (np.arange(col0, col1) + 0.5)

    if mask_geojson:
        import shapely

        geom = polygon_from_geojson(mask_geojson, flag="--mask-geojson")
        # Cell-centre test at native 30": the ~1 km cells are far finer than any overlay
        # grid, and cell-overlap clipping of ~10^6 cells against a detailed outline is slow.
        shapely.prepare(geom)
        lon2d, lat2d = np.meshgrid(lons, lats)
        count[~shapely.contains_xy(geom, lon2d, lat2d)] = np.nan
        if not np.isfinite(count).any():
            raise UsageError("--mask-geojson leaves no LandScan cells inside the box")
    if not np.isfinite(count).any():
        raise DataError("no land cells in the box (LandScan has no data over the sea)")

    # Spherical area of each native row's cells, in km2.
    edges = np.deg2rad(t.f + t.e * np.arange(row0, row1 + 1))
    row_area = (_EARTH_RADIUS_KM**2) * np.deg2rad(res) * np.abs(np.diff(np.sin(edges)))

    # Each native cell goes to the output cell holding its centre: a block of `factor` x
    # `factor` cells, or a --reference-grid cell. -1 means no output cell. Output rows run
    # south to north.
    if ref_lats is not None:
        out_lats, out_lons = ref_lats, ref_lons
        lat_edges, lon_edges = ref_lat_edges, ref_lon_edges
        iy = np.searchsorted(lat_edges, lats, side="right") - 1
        ix = np.searchsorted(lon_edges, lons, side="right") - 1
        iy[(iy < 0) | (iy >= out_lats.size)] = -1
        ix[(ix < 0) | (ix >= out_lons.size)] = -1
    else:
        ny, nx = (row1 - row0) // factor, (col1 - col0) // factor
        lat_edges = (t.f + t.e * (row0 + factor * np.arange(ny + 1)))[::-1]
        lon_edges = t.c + res * (col0 + factor * np.arange(nx + 1))
        out_lats = (lat_edges[:-1] + lat_edges[1:]) / 2
        out_lons = (lon_edges[:-1] + lon_edges[1:]) / 2
        iy = ny - 1 - np.arange(row1 - row0) // factor
        ix = np.arange(col1 - col0) // factor
    ok = np.isfinite(count) & (iy[:, None] >= 0) & (ix[None, :] >= 0)
    rr, cc = np.nonzero(ok)
    flat = iy[rr] * out_lons.size + ix[cc]
    size, shape = out_lats.size * out_lons.size, (out_lats.size, out_lons.size)
    area = np.bincount(flat, weights=row_area[rr], minlength=size).reshape(shape)
    count = np.bincount(flat, weights=count[rr, cc], minlength=size).reshape(shape)
    empty = np.bincount(flat, minlength=size).reshape(shape) == 0
    area[empty], count[empty] = np.nan, np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        density = count / area
        log_density = np.where(density > 0, np.log10(density), np.nan)

    time = np.array([np.datetime64(f"{year}-01-01", "ns")])

    def var(a):
        return (("time", "latitude", "longitude"), a[None].astype("float32"))

    ds = xr.Dataset(
        {
            "population": var(count),
            "population_density": var(density),
            "population_density_log10": var(log_density),
        },
        coords={"time": time, "latitude": out_lats, "longitude": out_lons},
    )
    cell = "--reference-grid" if ref_lats is not None else f"{factor * res:.6g} deg"
    ds["population"].attrs = {
        "units": "1",
        "long_name": f"LandScan {year} ambient population per {cell} cell",
        "cell_methods": "area: sum",
    }
    ds["population_density"].attrs = {
        "units": "km-2",
        "long_name": f"LandScan {year} population per km2 of land",
    }
    ds["population_density_log10"].attrs = {
        "units": "1",
        "long_name": f"log10 of LandScan {year} population per km2 (1 = 10, 3 = 1000 km-2)",
    }
    for name, v in ds.data_vars.items():
        err = udunits_error(v.attrs["units"])
        if err is not None:
            raise DataError(f"{name} units {v.attrs['units']!r} do not parse: {err}")
    ds["time"].attrs["long_name"] = "LandScan release year"
    stamp_cf_coords(ds)
    ds.attrs.update(
        Conventions="CF-1.13",
        title=f"LandScan Global {year} population",
        institution="Oak Ridge National Laboratory",
        source=f"LandScan Global {year}, 30 arc-second ambient population ({member})",
        references=_DOI,
        license="CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); attribute LandScan",
        citation=(
            f"Lebakula, V. et al. ({year}). LandScan Global {year}. Oak Ridge National "
            f"Laboratory. {_DOI}"
        ),
        weather_skills_source=f"landscan-global:{year}",
    )
    total = float(np.nansum(count))
    print(
        f"LandScan {year}: {ds.sizes['latitude']}x{ds.sizes['longitude']} cells of {cell}, "
        f"{total:,.0f} people",
        file=sys.stderr,
    )
    if area_geojson:
        keep = np.nan_to_num(density, nan=0.0) >= min_density
        if not keep.any():
            raise DataError(
                f"no cell has population_density >= {min_density:g} km-2 (the highest is "
                f"{np.nanmax(density):,.0f}); lower --min-density"
            )
        _write_area_geojson(area_geojson, keep, lat_edges, lon_edges, min_density, year)
    return ds


if __name__ == "__main__":
    fetch()
