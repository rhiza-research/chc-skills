# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "pyproj",
#   "xarray",
# ]
# ///
"""Interpolate station observations to a regular lat/lon grid with MetPy's objective analysis."""

from __future__ import annotations

import sys
import warnings

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims, names_for
from weather_skills_core.units import dequantify_dataset

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside the function, after weather_skills_core: importing
# core (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

METHODS = ("barnes", "cressman", "natural_neighbor", "linear", "nearest", "cubic", "rbf")


@weather_skill(name="grid-stations-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("point_obs"),
    required=True,
    dest="ds",
    help="Station Zarr (point_id or station_id, time) with latitude/longitude coordinates.",
)
@weather_skill.argument(
    "--like",
    type=Dataset("spatial"),
    help="Use this gridded Zarr's latitude/longitude grid (instead of --bbox/--resolution).",
)
@weather_skill.argument("--bbox", help="Grid extent N/W/S/E. Default: the stations' extent.")
@weather_skill.argument(
    "--resolution", type=float, help="Grid spacing in degrees (with --bbox or the default extent)."
)
@weather_skill.argument(
    "--method",
    choices=METHODS,
    default="barnes",
    help="Interpolation method. Default: barnes.",
)
@weather_skill.argument(
    "--search-radius",
    type=float,
    help="Influence radius in km for barnes/cressman. Default: MetPy's (5x mean station spacing).",
)
@weather_skill.argument(
    "--min-neighbors",
    type=int,
    default=3,
    help="Stations needed within the radius for barnes/cressman. Default: 3.",
)
@weather_skill.argument(
    "--gamma",
    type=float,
    default=1.0,
    help=(
        "Barnes smoothing: lower fits the stations more closely but turns blocky "
        "(MetPy's Barnes is one pass). Default: 1.0."
    ),
)
@weather_skill.argument(
    "--max-distance",
    type=float,
    help="Blank grid cells farther than this (km) from every reporting station.",
)
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    default=[],
    help="Grid only these variables (repeatable). Default: every station variable.",
)
def grid_stations(
    ds,
    like,
    bbox,
    resolution,
    method,
    search_radius,
    min_neighbors,
    gamma,
    max_distance,
    variable,
    **kwargs,
):
    """Interpolate station observations to a regular lat/lon grid with MetPy's objective analysis.

    Barnes, Cressman, natural-neighbour, linear, nearest, cubic or radial
    basis function interpolation (metpy.interpolate.interpolate_to_points)
    of every station variable, one time step (and member) at a time, onto a
    grid given by --bbox/--resolution or copied from --like. Distances are
    computed on a local equidistant projection, in km.
    """
    import metpy
    from metpy.interpolate import interpolate_to_points
    from pyproj import Transformer

    ds = dequantify_dataset(ds)
    sdim = next((d for d in names_for("point_id") if d in ds.dims), None)
    if sdim is None:
        raise UsageError("input must be station data with a point_id or station_id dim")
    lat_name = next((c for c in ("latitude", "lat") if c in ds.coords), None)
    lon_name = next((c for c in ("longitude", "lon") if c in ds.coords), None)
    if lat_name is None or lon_name is None:
        raise UsageError(f"stations need latitude and longitude coordinates along {sdim}")
    st_lat = np.asarray(ds[lat_name].values, dtype="float64")
    st_lon = np.asarray(ds[lon_name].values, dtype="float64")

    names = [n for n, da in ds.data_vars.items() if sdim in da.dims]
    missing = [v for v in variable if v not in names]
    if missing:
        raise UsageError(f"not station variables: {', '.join(missing)}; have: {', '.join(names)}")
    chosen = variable or names
    if not chosen:
        raise UsageError(f"no variables along {sdim} to grid")

    # Target grid.
    if like is not None:
        if bbox or resolution:
            raise UsageError("--like replaces --bbox and --resolution; pass one or the other")
        like = dequantify_dataset(like)
        g_lat_dim, g_lon_dim = detect_spatial_dims(like)
        grid_lat = like[g_lat_dim].values.astype("float64")
        grid_lon = like[g_lon_dim].values.astype("float64")
        lat_attrs, lon_attrs = dict(like[g_lat_dim].attrs), dict(like[g_lon_dim].attrs)
    else:
        if resolution is None or resolution <= 0:
            raise UsageError("pass --resolution (degrees), or --like a gridded Zarr")
        if bbox is None:
            pad = resolution
            n, w = np.nanmax(st_lat) + pad, np.nanmin(st_lon) - pad
            s, e = np.nanmin(st_lat) - pad, np.nanmax(st_lon) + pad
        else:
            n, w, s, e = bbox
        # Cells on multiples of the resolution, so grids from different runs line up.
        grid_lat = np.arange(np.ceil(s / resolution), np.floor(n / resolution) + 1) * resolution
        grid_lon = np.arange(np.ceil(w / resolution), np.floor(e / resolution) + 1) * resolution
        if grid_lat.size == 0 or grid_lon.size == 0:
            raise UsageError("the grid is empty: --bbox is smaller than one --resolution step")
        lat_attrs = {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"}
        lon_attrs = {"standard_name": "longitude", "units": "degrees_east", "axis": "X"}

    # Local azimuthal-equidistant projection: distances in metres, centred on the grid.
    lat0, lon0 = float(np.mean(grid_lat)), float(np.mean(grid_lon))
    to_xy = Transformer.from_crs(
        "EPSG:4326", f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m", always_xy=True
    )
    sx, sy = to_xy.transform(st_lon, st_lat)
    glon2, glat2 = np.meshgrid(grid_lon, grid_lat)
    gx, gy = to_xy.transform(glon2.ravel(), glat2.ravel())
    targets = np.column_stack([gx, gy])
    radius = search_radius * 1000.0 if search_radius else None

    def analyse(values: np.ndarray) -> np.ndarray:
        ok = np.isfinite(values) & np.isfinite(sx) & np.isfinite(sy)
        if ok.sum() < max(3, min_neighbors if method in ("barnes", "cressman") else 3):
            return np.full(targets.shape[0], np.nan)
        pts = np.column_stack([np.asarray(sx)[ok], np.asarray(sy)[ok]])
        kwargs = {"interp_type": method}
        if method in ("barnes", "cressman"):
            kwargs.update(minimum_neighbors=min_neighbors, search_radius=radius)
        if method == "barnes":
            kwargs["gamma"] = gamma
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            field = np.asarray(interpolate_to_points(pts, values[ok], targets, **kwargs), float)
        if max_distance is not None:
            d2 = (targets[:, None, 0] - pts[None, :, 0]) ** 2 + (
                targets[:, None, 1] - pts[None, :, 1]
            ) ** 2
            field[np.sqrt(d2.min(axis=1)) > max_distance * 1000.0] = np.nan
        return field

    out = xr.Dataset(
        coords={
            "latitude": ("latitude", grid_lat, lat_attrs),
            "longitude": ("longitude", grid_lon, lon_attrs),
        }
    )
    for name in chosen:
        da = ds[name]
        other = [d for d in da.dims if d != sdim]
        stacked = da.transpose(*other, sdim).values.astype("float64")
        flat = stacked.reshape(-1, stacked.shape[-1])
        try:
            fields = np.stack([analyse(row) for row in flat])
        except Exception as exc:
            raise UsageError(f"metpy.interpolate {method} failed on {name}: {exc}") from exc
        shape = [da.sizes[d] for d in other] + [grid_lat.size, grid_lon.size]
        coords = {d: da[d] for d in other if d in da.coords}
        out[name] = xr.DataArray(
            fields.reshape(shape), dims=[*other, "latitude", "longitude"], coords=coords
        ).assign_attrs(
            {k: v for k, v in da.attrs.items() if k != "coordinates"},
            comment=(
                f"MetPy {metpy.__version__} {method} analysis of {int(np.isfinite(flat).any(0).sum())} "
                "stations"
                + (f", search radius {search_radius:g} km" if radius else "")
                + (f", blank beyond {max_distance:g} km" if max_distance else "")
            ),
        )
    out.attrs = {k: v for k, v in ds.attrs.items() if k != "featureType"}
    out.attrs.setdefault("Conventions", "CF-1.13")
    out.attrs["metpy_version"] = metpy.__version__
    print(
        f"grid-stations-metpy: {method} of {', '.join(chosen)} from {ds.sizes[sdim]} stations "
        f"to {grid_lat.size} x {grid_lon.size} cells",
        file=sys.stderr,
    )
    return out


if __name__ == "__main__":
    grid_stations()
