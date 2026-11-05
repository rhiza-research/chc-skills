# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "numpy",
#   "scipy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Detect and track tropical cyclones in gridded forecasts, reanalyses or climate-model output."""
# A pure-Python port of the TempestExtremes DetectNodes + StitchNodes method
# (Ullrich and Zarzycki 2017, GMD 10:1069; Ullrich et al. 2021, GMD 14:5023) with the
# tropical-cyclone criteria of Zarzycki and Ullrich (2017, GRL 44:1141). With --preset auto
# the distances, the stitch range, the durations and the wind threshold follow the grid
# spacing and time step of the input (wind: Walsh et al. 2007, J. Climate 20:2307).

from __future__ import annotations

import math
import re
import sys

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

G = 9.80665  # m s-2, geopotential -> geopotential height
EARTH_RADIUS_KM = 6371.0
KM_PER_DEG = math.pi * EARTH_RADIUS_KM / 180.0

# Names each data source uses for the fields the tracker needs.
FIELD_ALIASES = {
    "psl": (
        "msl",
        "psl",
        "pressure_reduced_to_mean_sea_level",
        "mean_sea_level_pressure",
        "prmsl",
        "slp",
        "mslp",
    ),
    "u10": ("u10", "uas", "wind_u_10m", "10m_u_component_of_wind", "u10m", "eastward_wind_10m"),
    "v10": ("v10", "vas", "wind_v_10m", "10m_v_component_of_wind", "v10m", "northward_wind_10m"),
    "wspd10": ("si10", "sfcWind", "wind_speed_10m", "ws10", "10m_wind_speed"),
    "u": ("u", "ua", "u_component_of_wind", "eastward_wind"),
    "v": ("v", "va", "v_component_of_wind", "northward_wind"),
    "z": ("gh", "zg", "geopotential_height", "z", "geopotential", "hgt"),
    "zs": (
        "zs",
        "orog",
        "orography",
        "geopotential_at_surface",
        "surface_geopotential",
        "z_sfc",
        "hgt_sfc",
    ),
    "vort": ("vo", "vorticity", "relative_vorticity", "atmosphere_relative_vorticity"),
}
# Single-level names such as gh500, z_300, zg500, geopotential_height_500hpa, u850, wind_u_850hpa.
LEVEL_NAME = re.compile(
    r"^(?P<base>gh|z|zg|hgt|geopotential_height|geopotential|u|ua|v|va|wind_u|wind_v)"
    r"_?(?P<level>\d{3,4})(hpa)?$",
    re.IGNORECASE,
)
LEVEL_BASE = {
    "gh": "z",
    "z": "z",
    "zg": "z",
    "hgt": "z",
    "geopotential_height": "z",
    "geopotential": "z",
    "u": "u",
    "ua": "u",
    "wind_u": "u",
    "v": "v",
    "va": "v",
    "wind_v": "v",
}
LEVEL_DIMS = ("level", "pressure_level", "plev", "isobaricInhPa", "vertical", "pressure", "lev")
MEMBER_DIMS = ("number", "member", "realization", "ensemble_member")
STEP_DIMS = ("step", "lead_time", "prediction_timedelta")
INIT_NAMES = ("time", "init_time", "forecast_reference_time")

# Walsh et al. (2007), Fig. 1 grid-box-average curve: tropical-storm 10 m wind threshold
# (m s-1) against grid spacing (km).
WALSH_KM = (0.0, 10.0, 30.0, 50.0, 75.0, 100.0, 150.0, 200.0, 250.0, 300.0)
WALSH_MS = (17.5, 17.5, 17.0, 16.4, 15.7, 14.9, 13.9, 12.9, 11.0, 10.5)
# 850 hPa winds are stronger than 10 m winds; surface winds are about 0.8 of them
# (Franklin et al. 2003 reduction factor), so 850 hPa thresholds are divided by 0.8.
WIND_850_FACTOR = 0.8

# Published Zarzycki and Ullrich (2017) TempestExtremes TC configuration (6-hourly data):
# DetectNodes --mergedist 6.0 --closedcontourcmd "PSL,200.0,5.5,0;_DIFF(Z300,Z500),-58.8,6.5,1.0"
#   --outputcmd "PSL,min,0;_VECMAG(U,V),max,2;ZS,max,0"
# StitchNodes --range 8.0 --mintime 54h --maxgap 24h
#   --threshold "wind,>=,10.0,10;lat,<=,50.0,10;lat,>=,-50.0,10;zs,<=,150.0,10"
ZU2017 = {
    "merge_dist": 6.0,
    "psl_delta": 200.0,
    "psl_dist": 5.5,
    "warm_core": True,
    "warm_core_delta": 58.8 / G,  # 58.8 m2 s-2 of geopotential = 6.0 gpm
    "warm_core_dist": 6.5,
    "warm_core_offset": 1.0,
    "wind_dist": 2.0,
    "range": 8.0,
    "max_gap": 24.0,
    "min_time": 54.0,
    "wind_min": 10.0,
    "wind_hours": 60.0,
    "max_lat": 50.0,
    "lat_hours": 60.0,
    "max_zs": 150.0,
    "zs_hours": 60.0,
}
COARSE_DAILY = {
    **ZU2017,
    "psl_dist": 6.0,
    "warm_core": False,
    "wind_dist": 3.0,
    "range": 12.5,
    "min_time": 48.0,
    "wind_min": 8.0,
    "wind_hours": 48.0,
    "lat_hours": 48.0,
    "zs_hours": 48.0,
}
PRESETS = {"zu2017": ZU2017, "coarse-daily": COARSE_DAILY}
KNOTS_PER_MS = 3600.0 / 1852.0
# Intensity classes on the wind in knots (ECMWF's TC chart legend; Saffir-Simpson edges).
CATEGORY_KT = (34.0, 64.0, 83.0, 96.0)
CATEGORY_NAMES = (
    "tropical_depression",
    "tropical_storm",
    "category_1",
    "category_2",
    "category_3_or_more",
)


def wind_category(wind_ms):
    """Intensity class per point: 0 TD (<34 kt), 1 TS (34-63), 2 Cat 1 (64-82), 3 Cat 2 (83-95),
    4 Cat 3+ (>=96 kt); NaN where the wind is NaN."""
    import numpy as np

    wind_ms = np.asarray(wind_ms, dtype=float)
    cat = np.digitize(wind_ms * KNOTS_PER_MS, CATEGORY_KT).astype(float)
    return np.where(np.isfinite(wind_ms), cat, np.nan)


REFERENCES = (
    "Ullrich and Zarzycki 2017 (GMD 10:1069); Ullrich et al. 2021 (GMD 14:5023); "
    "Zarzycki and Ullrich 2017 (GRL 44:1141); Walsh et al. 2007 (J. Climate 20:2307)"
)


def walsh_threshold(dx_km):
    """Tropical-storm 10 m wind threshold (m s-1) for a grid spacing in km (Walsh et al. 2007)."""
    import numpy as np

    return float(np.interp(dx_km, WALSH_KM, WALSH_MS))


def resolve_criteria(
    preset,
    dx_deg,
    dt_hours,
    *,
    mean_psl=False,
    wind_level="10m",
    have_warm_core=True,
    overrides=None,
):
    """Detection and stitching criteria for the input's grid spacing and time step.

    Args:
        preset: ``auto``, ``zu2017`` or ``coarse-daily``.
        dx_deg: grid spacing in degrees (the larger of the lat and lon spacing).
        dt_hours: time step in hours.
        mean_psl: the pressure field is a period mean rather than instantaneous.
        wind_level: ``10m``, ``850hPa`` or ``None`` (no wind field).
        have_warm_core: geopotential at the warm-core levels is in the input.
        overrides: criteria set on the command line (None values are ignored).

    Returns:
        ``(criteria, notes)``: the dict of criteria and a list of notes on what was chosen.
    """
    if dt_hours > 24.0 + 1e-6:
        raise UsageError(
            f"time step is {dt_hours:g} h; tracking needs data at least every 24 h "
            "(select sub-daily or daily fields)"
        )
    notes = []
    dx_km = dx_deg * KM_PER_DEG
    if preset == "auto":
        crit = dict(ZU2017)
        crit["psl_dist"] = max(crit["psl_dist"], 3.0 * dx_deg)
        crit["merge_dist"] = max(crit["merge_dist"], 2.0 * dx_deg)
        crit["warm_core_dist"] = max(crit["warm_core_dist"], 3.0 * dx_deg)
        crit["warm_core_offset"] = max(crit["warm_core_offset"], dx_deg)
        crit["wind_dist"] = max(crit["wind_dist"], 1.5 * dx_deg)
        if dt_hours <= 6.0:
            crit["range"] = min(8.0, max(2.0, 8.0 * dt_hours / 6.0))
        else:
            crit["range"] = 8.0 + 0.25 * (dt_hours - 6.0)
            crit["min_time"] = max(2.0 * dt_hours, dt_hours * math.floor(54.0 / dt_hours))
        # ZU2017's 10 m s-1 was set for ~0.25 deg grids; scale it with Walsh's curve.
        ref = walsh_threshold(0.25 * KM_PER_DEG)
        crit["wind_min"] = round(10.0 * walsh_threshold(dx_km) / ref, 2)
        if mean_psl:
            crit["psl_delta"] = 100.0
            notes.append(
                "pressure is a period mean (cell_methods); closed-contour delta relaxed to "
                "100 Pa (heuristic, not from the papers)"
            )
    elif preset in PRESETS:
        crit = dict(PRESETS[preset])
    else:
        raise UsageError(f"unknown --preset {preset!r}; use auto, zu2017 or coarse-daily")

    crit["ts_wind"] = round(walsh_threshold(dx_km), 2)
    if wind_level == "850hPa":
        crit["wind_min"] = round(crit["wind_min"] / WIND_850_FACTOR, 2)
        crit["ts_wind"] = round(crit["ts_wind"] / WIND_850_FACTOR, 2)
        notes.append("no 10 m wind; using 850 hPa wind with thresholds divided by 0.8")
    for key, value in (overrides or {}).items():
        if value is not None:
            crit[key] = value
    if crit["warm_core"] and not have_warm_core:
        crit["warm_core"] = False
        notes.append("warm-core test skipped: no geopotential at 300+500 or 250+850 hPa")
    if wind_level is None:
        notes.append("wind filter skipped: no 10 m or 850 hPa wind in the input")
    return crit, notes


# ---------------------------------------------------------------------------- input


def _units(da):
    units = da.attrs.get("units")
    return units.strip() if isinstance(units, str) and units.strip() else None


def _convert(da, target, guess=None):
    """Values of ``da`` as a numpy array in ``target`` units (pint via the core helper)."""
    from weather_skills_core.units import convert_values

    units = _units(da) or guess
    if units is None:
        raise UsageError(f"variable {da.name!r} has no units attribute; set it before tracking")
    try:
        values, _ = convert_values(da.values, units, target)
    except Exception as exc:
        raise UsageError(
            f"variable {da.name!r}: units {units!r} not convertible to {target}"
        ) from exc
    return values


def _height(da):
    """Geopotential or geopotential height as geopotential height in m."""
    import xarray as xr

    units = _units(da)
    if units is None:
        units = "m2 s-2" if da.name in ("z", "geopotential", "geopotential_at_surface") else "m"
    try:
        values = _convert(da.assign_attrs(units=units), "m")
    except UsageError:
        values = _convert(da.assign_attrs(units=units), "m2 s-2") / G
    return xr.DataArray(values, dims=da.dims, coords=da.coords, name=da.name)


def _level_hpa(coord):
    """Pressure levels in hPa (CMIP plev is in Pa)."""
    import numpy as np

    values = np.asarray(coord.values, dtype=float)
    units = (coord.attrs.get("units") or "").strip().lower()
    if units == "pa" or (not units and np.nanmax(values) > 2000.0):
        values = values / 100.0
    return values


def _level_dim(da):
    return next((d for d in LEVEL_DIMS if d in da.dims), None)


def _at_level(da, level_hpa):
    """Slice ``da`` at one pressure level, or None when the level is not there."""
    import numpy as np

    dim = _level_dim(da)
    if dim is None:
        return None
    levels = _level_hpa(da[dim])
    hit = np.flatnonzero(np.isclose(levels, level_hpa))
    if not hit.size:
        return None
    return da.isel({dim: int(hit[0])}, drop=True)


def gather_fields(datasets):
    """Find the tracker's fields in the merged inputs, by name, alias and level.

    Returns:
        ``(fields, wind_level, notes)``: ``fields`` maps ``psl``, ``wind``, ``thick``,
        ``zs`` and ``vort`` to DataArrays (psl in Pa, wind in m s-1, thickness and zs in m,
        vort in s-1); missing optional fields are absent.
    """
    import numpy as np
    from weather_skills_core.units import dequantify_dataset

    variables = {}
    for ds in datasets:
        ds = dequantify_dataset(ds)
        for name in ds.data_vars:
            variables.setdefault(name, ds[name])

    def pick(key):
        return next((variables[n] for n in FIELD_ALIASES[key] if n in variables), None)

    by_level = {"z": {}, "u": {}, "v": {}}
    for name, da in variables.items():
        m = LEVEL_NAME.match(name)
        if m and _level_dim(da) is None:
            by_level[LEVEL_BASE[m["base"].lower()]][float(m["level"])] = da

    def level_field(key, level):
        if level in by_level[key]:
            return by_level[key][level]
        da = pick(key)
        return None if da is None else _at_level(da, level)

    psl = pick("psl")
    if psl is None:
        raise UsageError(
            f"no sea-level pressure in the input; expected one of {', '.join(FIELD_ALIASES['psl'])}"
        )
    if _level_dim(psl) is not None:
        raise UsageError(f"{psl.name!r} has a level dimension; pass mean sea-level pressure")
    fields = {"psl": psl.copy(data=_convert(psl, "Pa", guess="Pa" if psl.max() > 2000 else "hPa"))}
    notes = []

    wind_level = None
    u10, v10, w10 = pick("u10"), pick("v10"), pick("wspd10")
    if u10 is not None and v10 is not None:
        fields["wind"] = np.hypot(
            u10.copy(data=_convert(u10, "m s-1")), v10.copy(data=_convert(v10, "m s-1"))
        )
        wind_level = "10m"
    elif w10 is not None:
        fields["wind"] = w10.copy(data=_convert(w10, "m s-1"))
        wind_level = "10m"
    else:
        u850, v850 = level_field("u", 850.0), level_field("v", 850.0)
        if u850 is not None and v850 is not None:
            fields["wind"] = np.hypot(
                u850.copy(data=_convert(u850, "m s-1")), v850.copy(data=_convert(v850, "m s-1"))
            )
            wind_level = "850hPa"

    # Upper-tropospheric layers first (ZU2017; Zarzycki et al. 2017). 500-850 hPa is a
    # fallback for sources without upper levels (e.g. dynamical's IFS-ENS, which has only
    # 500/850/925 hPa): Hart's (2003) lower layer, where a TC's warm anomaly is weaker
    # and the test removes fewer cold-core lows.
    for upper, lower in ((300.0, 500.0), (250.0, 850.0), (500.0, 850.0)):
        zu, zl = level_field("z", upper), level_field("z", lower)
        if zu is not None and zl is not None:
            fields["thick"] = _height(zu) - _height(zl)
            fields["thick"].attrs["layer"] = f"{upper:g}-{lower:g} hPa"
            if upper == 500.0:
                notes.append(
                    "warm core tested on the 500-850 hPa thickness (no 300 or 250 hPa height "
                    "in the input); weaker than the standard upper-level test"
                )
            break

    zs = pick("zs")
    if zs is not None:
        fields["zs"] = _height(zs)
    vort = pick("vort")
    if vort is not None and _level_dim(vort) is None:
        fields["vort"] = vort.copy(data=_convert(vort, "s-1"))
    return fields, wind_level, notes


def _align(fields, psl):
    """Put every field on the pressure field's lat/lon grid (linear interpolation if needed)."""
    import numpy as np
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    lat_dim, lon_dim = detect_spatial_dims(psl.to_dataset(name="psl"))
    out = {}
    for key, da in fields.items():
        if not {lat_dim, lon_dim} <= set(da.dims):
            raise UsageError(f"field {key} ({da.name!r}) has no {lat_dim}/{lon_dim} dims")
        da = ensure_normalized_longitude(da, lon_dim).sortby([lat_dim, lon_dim])
        out[key] = da
    base = out["psl"]
    for key, da in out.items():
        if key == "psl":
            continue
        same = all(
            da.sizes.get(d) == base.sizes[d] and np.allclose(da[d].values, base[d].values)
            for d in (lat_dim, lon_dim)
        )
        if not same:
            out[key] = da.interp({lat_dim: base[lat_dim], lon_dim: base[lon_dim]})
    return out, lat_dim, lon_dim


def _hours(times):
    """Hours since the first entry, for datetime64 or cftime values."""
    import numpy as np

    times = np.asarray(times)
    if np.issubdtype(times.dtype, np.datetime64):
        return (times - times[0]) / np.timedelta64(1, "h")
    return np.array([(t - times[0]).total_seconds() / 3600.0 for t in times])


def series_layout(psl):
    """Dims that index separate tracking series, and how to get valid times.

    Returns:
        dict with ``member`` (dim or None), ``init`` (dim or None), ``step`` (dim or None)
        and ``time`` (the valid-time dim when there is no step).
    """
    member = next((d for d in MEMBER_DIMS if d in psl.dims), None)
    step = next((d for d in STEP_DIMS if d in psl.dims), None)
    if step is not None:
        init = next((n for n in INIT_NAMES if n in psl.coords), None)
        if init is None:
            raise UsageError(f"forecast input has a {step!r} dim but no init time coordinate")
        init_dim = init if init in psl.dims else None
        return {"member": member, "init": init_dim, "init_name": init, "step": step, "time": None}
    time = next((d for d in ("time", "valid_time") if d in psl.dims), None)
    if time is None:
        raise UsageError("input has no time or step dimension to track along")
    return {"member": member, "init": None, "init_name": None, "step": None, "time": time}


# ------------------------------------------------------------------------ detection


class Grid:
    """Lat/lon geometry of one map, with longitude wrap for global grids."""

    def __init__(self, lat, lon):
        import numpy as np

        self.lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        gaps = np.diff(lon, append=lon[0] + 360.0)
        self.dlon = float(np.median(gaps)) if lon.size > 1 else 1.0
        self.dlat = float(np.median(np.abs(np.diff(self.lat)))) if self.lat.size > 1 else 1.0
        self.wrap = lon.size > 1 and gaps.max() < 1.5 * self.dlon
        # Roll so a bbox across the dateline is one contiguous map.
        self.roll = 0 if self.wrap else int((np.argmax(gaps) + 1) % lon.size)
        self.lon = np.roll(lon, -self.roll)
        self.ny, self.nx = self.lat.size, self.lon.size
        self.rlat = np.radians(self.lat)
        self.rlon = np.radians(self.lon)

    def prep(self, field):
        import numpy as np

        return np.roll(np.asarray(field, dtype=float), -self.roll, axis=-1)

    def distance(self, lat0, lon0, rows, cols):
        """Great-circle distance in degrees from (lat0, lon0) to the cells rows x cols."""
        import numpy as np

        phi = self.rlat[rows][:, None]
        lam = self.rlon[cols][None, :]
        p0, l0 = math.radians(lat0), math.radians(lon0)
        a = np.sin((phi - p0) / 2) ** 2 + np.cos(phi) * math.cos(p0) * np.sin((lam - l0) / 2) ** 2
        return np.degrees(2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))))

    def window(self, y0, x0, radius):
        """Row and column indices of a box covering ``radius`` degrees around a cell.

        Returns:
            ``(rows, cols, open_edges)``: ``open_edges`` lists the box sides that are cut by
            the domain edge (``top``, ``bottom``, ``left``, ``right``).
        """
        import numpy as np

        ky = math.ceil(radius / self.dlat) + 2
        coslat = max(math.cos(math.radians(self.lat[y0])), 0.05)
        kx = math.ceil(radius / (self.dlon * coslat)) + 2
        open_edges = []
        lo, hi = y0 - ky, y0 + ky
        if lo < 0:
            open_edges.append("bottom")
        if hi > self.ny - 1:
            open_edges.append("top")
        rows = np.arange(max(lo, 0), min(hi, self.ny - 1) + 1)
        if self.wrap:
            if 2 * kx + 1 >= self.nx:
                cols = (np.arange(self.nx) + x0 - self.nx // 2) % self.nx
            else:
                cols = np.arange(x0 - kx, x0 + kx + 1) % self.nx
        else:
            lo, hi = x0 - kx, x0 + kx
            if lo < 0:
                open_edges.append("left")
            if hi > self.nx - 1:
                open_edges.append("right")
            cols = np.arange(max(lo, 0), min(hi, self.nx - 1) + 1)
        return rows, cols, open_edges


def closed_contour(grid, field, y0, x0, delta, dist, offset=0.0):
    """TempestExtremes closed-contour test around a minimum of ``field``.

    The reference is the smallest value within ``offset`` degrees of (y0, x0). The test
    passes when every path from it that stays below reference + ``delta`` ends within
    ``dist`` degrees: a flood fill from the reference through below-threshold cells may not
    reach a cell beyond ``dist`` or the edge of the domain. Missing cells are walls.

    Returns:
        True when the contour is closed.
    """
    import numpy as np
    from scipy import ndimage

    rows, cols, open_edges = grid.window(y0, x0, dist + offset)
    sub = field[np.ix_(rows, cols)]
    cy, cx = int(np.flatnonzero(rows == y0)[0]), int(np.flatnonzero(cols == x0)[0])
    if offset > 0:
        d0 = grid.distance(grid.lat[y0], grid.lon[x0], rows, cols)
        near = np.where((d0 <= offset) & np.isfinite(sub), sub, np.inf)
        cy, cx = np.unravel_index(int(np.argmin(near)), sub.shape)
    ref = sub[cy, cx]
    if not np.isfinite(ref):
        return False
    d = grid.distance(grid.lat[rows[cy]], grid.lon[cols[cx]], rows, cols)
    below = np.isfinite(sub) & (sub < ref + delta)
    labels, _ = ndimage.label(below & (d <= dist), structure=np.ones((3, 3), bool))
    comp = labels == labels[cy, cx]
    grown = ndimage.binary_dilation(comp, structure=np.ones((3, 3), bool))
    if (grown & below & (d > dist)).any():
        return False
    edges = {"bottom": comp[0, :], "top": comp[-1, :], "left": comp[:, 0], "right": comp[:, -1]}
    return not any(edges[e].any() for e in open_edges)


def _local_max(grid, field, y0, x0, radius):
    """Largest value of ``field`` within ``radius`` degrees of a cell (NaN when none)."""
    import numpy as np

    rows, cols, _ = grid.window(y0, x0, radius)
    sub = field[np.ix_(rows, cols)]
    d = grid.distance(grid.lat[y0], grid.lon[x0], rows, cols)
    vals = sub[(d <= radius) & np.isfinite(sub)]
    return float(vals.max()) if vals.size else float("nan")


def detect_nodes(grid, maps, crit):
    """Candidate TC centres in one map (DetectNodes).

    Args:
        grid: the map's ``Grid``.
        maps: dict of 2-D arrays already rolled onto the grid: ``psl`` (Pa) and optional
            ``wind`` (m s-1), ``thick`` (m), ``zs`` (m), ``vort`` (s-1).
        crit: criteria from ``resolve_criteria``.

    Returns:
        list of dicts with ``lat``, ``lon``, ``psl``, ``wind``, ``zs``, ``vort``.
    """
    import numpy as np
    from scipy import ndimage

    psl = maps["psl"]
    finite = np.isfinite(psl)
    filled = np.where(finite, psl, np.inf)
    mode = ("nearest", "wrap" if grid.wrap else "nearest")
    low = finite & (filled == ndimage.minimum_filter(filled, size=3, mode=mode))
    iy, ix = np.nonzero(low)
    order = np.argsort(psl[iy, ix], kind="stable")
    iy, ix = iy[order], ix[order]
    kept = _merge_candidates(grid.lat[iy], grid.lon[ix], crit["merge_dist"])
    kept = [(int(iy[k]), int(ix[k])) for k in kept]

    nodes = []
    thick = maps.get("thick")
    for y, x in kept:
        if not closed_contour(grid, psl, y, x, crit["psl_delta"], crit["psl_dist"]):
            continue
        if (
            crit["warm_core"]
            and thick is not None
            and not closed_contour(
                grid,
                -thick,
                y,
                x,
                crit["warm_core_delta"],
                crit["warm_core_dist"],
                crit["warm_core_offset"],
            )
        ):
            continue
        node = {"lat": float(grid.lat[y]), "lon": float(grid.lon[x]), "psl": float(psl[y, x])}
        wind = maps.get("wind")
        node["wind"] = (
            _local_max(grid, wind, y, x, crit["wind_dist"]) if wind is not None else np.nan
        )
        node["zs"] = float(maps["zs"][y, x]) if "zs" in maps else np.nan
        vort = maps.get("vort")
        if vort is not None:
            # Cyclonic vorticity is positive in the north and negative in the south.
            sign = 1.0 if grid.lat[y] >= 0 else -1.0
            node["vort"] = sign * _local_max(grid, sign * vort, y, x, crit["wind_dist"])
        else:
            node["vort"] = np.nan
        nodes.append(node)
    return nodes


def _merge_candidates(lat, lon, merge_dist):
    """Greedy merge: indices of the candidates kept, deepest first.

    ``lat``/``lon`` are sorted deepest first. A candidate is dropped when it lies closer than
    ``merge_dist`` degrees (great circle) to one already kept. A KD tree on unit vectors
    keeps this fast when a quantized field has many flat-plateau minima (a global map can
    have 10^5 of them).
    """
    import numpy as np
    from scipy.spatial import cKDTree

    if lat.size == 0:
        return []
    p, la = np.radians(lat), np.radians(lon)
    xyz = np.column_stack([np.cos(p) * np.cos(la), np.cos(p) * np.sin(la), np.sin(p)])
    tree = cKDTree(xyz)
    # Chord length of the merge angle; shrink a hair so "closer than" stays strict.
    chord = 2 * np.sin(np.radians(merge_dist) / 2) * (1 - 1e-12)
    dropped = np.zeros(lat.size, bool)
    kept = []
    for k in range(lat.size):
        if dropped[k]:
            continue
        kept.append(k)
        dropped[tree.query_ball_point(xyz[k], chord)] = True
    return kept


def _pair_distance(lat0, lon0, lat, lon):
    """Great-circle distance in degrees from one point to arrays of points."""
    import numpy as np

    p0, l0 = np.radians(lat0), np.radians(lon0)
    p, la = np.radians(lat), np.radians(lon)
    a = np.sin((p - p0) / 2) ** 2 + np.cos(p) * np.cos(p0) * np.sin((la - l0) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))))


# ------------------------------------------------------------------------ stitching


def _count_needed(hours, dt):
    return max(1, math.ceil(hours / dt - 1e-9))


def stitch_nodes(nodes_by_time, hours, crit, dt):
    """Link candidates into tracks (StitchNodes) and apply the duration and threshold filters.

    Args:
        nodes_by_time: list (one entry per time) of node lists from ``detect_nodes``.
        hours: hours of each time since the first.
        crit: criteria.
        dt: time step in hours.

    Returns:
        list of tracks; a track is a list of ``(time index, node)``.
    """
    import numpy as np

    used = [np.zeros(len(n), bool) for n in nodes_by_time]
    tracks = []
    for t0, nodes in enumerate(nodes_by_time):
        for i0 in np.argsort([n["psl"] for n in nodes], kind="stable"):
            if used[t0][i0]:
                continue
            used[t0][i0] = True
            path = [(t0, nodes[i0])]
            t, node = t0, nodes[i0]
            while True:
                nxt = None
                for t2 in range(t + 1, len(nodes_by_time)):
                    if hours[t2] - hours[t] - dt > crit["max_gap"] + 1e-6:
                        break
                    free = [j for j in range(len(nodes_by_time[t2])) if not used[t2][j]]
                    if not free:
                        continue
                    cand = nodes_by_time[t2]
                    d = _pair_distance(
                        node["lat"],
                        node["lon"],
                        np.array([cand[j]["lat"] for j in free]),
                        np.array([cand[j]["lon"] for j in free]),
                    )
                    k = int(np.argmin(d))
                    if d[k] <= crit["range"]:
                        nxt = (t2, free[k])
                        break
                if nxt is None:
                    break
                t, j = nxt
                used[t][j] = True
                node = nodes_by_time[t][j]
                path.append((t, node))
            if _keep(path, hours, crit, dt):
                tracks.append(path)
    return tracks


def _keep(path, hours, crit, dt):
    """Duration and threshold filters of StitchNodes."""
    import numpy as np

    if hours[path[-1][0]] - hours[path[0][0]] < crit["min_time"] - 1e-6:
        return False
    lat = np.array([n["lat"] for _, n in path])
    if (np.abs(lat) <= crit["max_lat"]).sum() < _count_needed(crit["lat_hours"], dt):
        return False
    wind = np.array([n["wind"] for _, n in path])
    need = _count_needed(crit["wind_hours"], dt)
    if np.isfinite(wind).any() and (wind >= crit["wind_min"]).sum() < need:
        return False
    zs = np.array([n["zs"] for _, n in path])
    need = _count_needed(crit["zs_hours"], dt)
    return not (np.isfinite(zs).any() and (zs <= crit["max_zs"]).sum() < need)


# --------------------------------------------------------------------------- skill


OVERRIDE_FLAGS = {
    "merge_dist": "Degrees: of candidates closer than this, only the deepest is kept.",
    "psl_delta": "Pa: pressure rise that closes the contour around a low.",
    "psl_dist": "Degrees: the pressure contour must close within this distance.",
    "warm_core_delta": "m: Z300-Z500 thickness fall that closes the warm core.",
    "warm_core_dist": "Degrees: the warm-core contour must close within this distance.",
    "warm_core_offset": "Degrees: the thickness maximum may be this far from the low.",
    "wind_dist": "Degrees: radius for the maximum wind recorded at each centre.",
    "range": "Degrees: farthest a centre may move between two linked times.",
    "max_gap": "Hours of missing detections allowed inside a track.",
    "min_time": "Hours: shortest track kept (first to last point).",
    "wind_min": "m s-1: wind a track must reach for --wind-hours.",
    "wind_hours": "Hours (as points) a track must meet --wind-min.",
    "max_lat": "Degrees: |latitude| a track must stay within for --lat-hours.",
    "lat_hours": "Hours (as points) a track must stay within --max-lat.",
    "max_zs": "m: surface height a track must stay below for --zs-hours.",
    "zs_hours": "Hours (as points) a track must stay below --max-zs.",
}


def _decorate(fn):
    for dest, text in reversed(OVERRIDE_FLAGS.items()):
        fn = weather_skill.argument(
            f"--{dest.replace('_', '-')}", type=float, default=None, help=text
        )(fn)
    return fn


@weather_skill(name="tc-track", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("spatial"),
    action="append",
    required=True,
    dest="ds",
    help="Gridded Zarr(s) with sea-level pressure and optional winds, geopotential, orography.",
)
@weather_skill.argument(
    "--preset",
    choices=["auto", "zu2017", "coarse-daily"],
    default="auto",
    help="auto (criteria follow grid spacing and time step), zu2017 or coarse-daily.",
)
@weather_skill.argument(
    "--no-warm-core", action="store_true", help="Skip the warm-core test even with geopotential."
)
@_decorate
def tc_track(ds, preset, no_warm_core, **kwargs):
    """Detect and track tropical cyclones in gridded forecasts, reanalyses or climate-model output."""
    import numpy as np

    overrides = {k: kwargs.get(k) for k in OVERRIDE_FLAGS}
    if no_warm_core:
        overrides["warm_core"] = False
    datasets = ds if isinstance(ds, list) else [ds]
    fields, wind_level, notes = gather_fields(datasets)
    fields, lat_dim, lon_dim = _align(fields, fields["psl"])
    psl = fields["psl"]
    layout = series_layout(psl)

    lat = psl[lat_dim].values
    grid = Grid(lat, psl[lon_dim].values)
    dx = max(grid.dlat, grid.dlon)
    first = _first_series(psl, layout)
    valid = _valid_times(first, layout)
    if len(valid) < 2:
        raise UsageError("need at least two times to track")
    dt = float(np.median(np.diff(_hours(valid))))
    mean_psl = "mean" in str(psl.attrs.get("cell_methods", ""))
    crit, crit_notes = resolve_criteria(
        preset,
        dx,
        dt,
        mean_psl=mean_psl,
        wind_level=wind_level,
        have_warm_core="thick" in fields,
        overrides=overrides,
    )
    notes += crit_notes
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    shown = ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in crit.items())
    print(f"criteria ({preset}, grid {dx:g} deg, step {dt:g} h): {shown}", file=sys.stderr)

    members = psl[layout["member"]].values if layout["member"] else [None]
    inits = psl[layout["init"]].values if layout["init"] else [None]
    rows = []
    for init in inits:
        for member in members:
            sel = {}
            if layout["init"]:
                sel[layout["init"]] = init
            if layout["member"]:
                sel[layout["member"]] = member
            series = {
                k: (v.sel({d: sel[d] for d in sel if d in v.dims}) if sel else v)
                for k, v in fields.items()
            }
            times = _valid_times(series["psl"], layout)
            tdim = layout["step"] or layout["time"]
            hours = _hours(times)
            nodes_by_time = []
            for t in range(len(times)):
                maps = {}
                for key, da in series.items():
                    if tdim in da.dims:
                        da = da.isel({tdim: t})
                    extra = [d for d in da.dims if d not in (lat_dim, lon_dim)]
                    if extra:
                        raise UsageError(
                            f"field {key} ({da.name!r}) has extra dims {extra}; select them first"
                        )
                    maps[key] = grid.prep(da.transpose(lat_dim, lon_dim).values)
                nodes_by_time.append(detect_nodes(grid, maps, crit))
            for path in stitch_nodes(nodes_by_time, hours, crit, dt):
                rows.append((member, init, [(times[t], n) for t, n in path]))

    out = _to_dataset(rows, layout, crit, wind_level)
    out.attrs.update(
        tc_preset=preset,
        tc_grid_spacing_deg=dx,
        tc_time_step_hours=dt,
        tc_wind_level=wind_level or "none",
        tc_notes="; ".join(notes),
        tc_references=REFERENCES,
        **{f"tc_{k}": (int(v) if isinstance(v, bool) else v) for k, v in crit.items()},
    )
    if "thick" in fields and crit["warm_core"]:
        out.attrs["tc_warm_core_layer"] = fields["thick"].attrs.get("layer", "")
    if layout["member"]:
        out.attrs["ensemble_members"] = [int(m) for m in members]
    if layout["init_name"] and not layout["init"]:
        out.attrs["forecast_reference_time"] = str(
            np.datetime_as_string(
                np.asarray(psl[layout["init_name"]].values).astype("datetime64[s]")
            )
        )
    print(f"found {out.sizes['track']} tracks", file=sys.stderr)
    return out


def _first_series(psl, layout):
    sel = {}
    for key in ("member", "init"):
        if layout[key]:
            sel[layout[key]] = 0
    return psl.isel(sel) if sel else psl


def _valid_times(da, layout):
    import numpy as np

    if layout["step"]:
        init = np.asarray(da[layout["init_name"]].values)
        return init + np.asarray(da[layout["step"]].values)
    return np.asarray(da[layout["time"]].values)


def _to_dataset(rows, layout, crit, wind_level):
    """Tracks as a CF trajectory dataset on (track, obs), padded with NaN."""
    import numpy as np
    import xarray as xr

    n_obs = max([len(p) for _, _, p in rows], default=1)
    n = len(rows)
    times = np.asarray([p[0][0] for _, _, p in rows] if rows else [])
    cftime_times = times.size and not np.issubdtype(times.dtype, np.datetime64)
    tvals = np.full(
        (n, n_obs),
        None if cftime_times else np.datetime64("NaT", "ns"),
        dtype=object if cftime_times else "datetime64[ns]",
    )
    arrays = {k: np.full((n, n_obs), np.nan) for k in ("lat", "lon", "psl", "wind", "zs", "vort")}
    ids = []
    counts = {}
    for k, (member, _, path) in enumerate(rows):
        tag = f"m{int(member):03d}-" if member is not None else ""
        counts[tag] = counts.get(tag, 0) + 1
        ids.append(f"{tag}{counts[tag]:03d}")
        for j, (time, node) in enumerate(path):
            tvals[k, j] = time
            for key, arr in arrays.items():
                arr[k, j] = node[key]
    dims = ("track", "obs")
    out = xr.Dataset(
        {
            "msl": (
                dims,
                arrays["psl"],
                {
                    "units": "Pa",
                    "long_name": "central mean sea level pressure",
                    "standard_name": "air_pressure_at_mean_sea_level",
                },
            ),
            "wind_max": (
                dims,
                arrays["wind"],
                {
                    "units": "m s-1",
                    "long_name": f"maximum {wind_level or ''} wind speed within "
                    f"{crit['wind_dist']:g} degrees".replace("  ", " "),
                },
            ),
            "ts_strength": (
                dims,
                np.where(
                    np.isnan(arrays["wind"]),
                    np.nan,
                    (arrays["wind"] >= crit["ts_wind"]).astype(float),
                ),
                {
                    "units": "1",
                    "long_name": "1 where wind_max reaches the tropical-storm threshold for this "
                    f"grid ({crit['ts_wind']:g} m s-1, Walsh et al. 2007)",
                },
            ),
            "category": (
                dims,
                wind_category(
                    arrays["wind"] * (WIND_850_FACTOR if wind_level == "850hPa" else 1.0)
                ),
                {
                    "units": "1",
                    "long_name": "intensity class from wind_max in knots"
                    + (" (850 hPa wind x 0.8)" if wind_level == "850hPa" else ""),
                    "flag_values": list(range(len(CATEGORY_NAMES))),
                    "flag_meanings": " ".join(CATEGORY_NAMES),
                    "category_edges_kt": list(CATEGORY_KT),
                    "comment": "uses the model wind as given (instantaneous, grid-scale): "
                    "coarse grids under-resolve the peak wind and so the class",
                },
            ),
        },
        coords={
            "track": (
                "track",
                np.array(ids, dtype=str),
                {"cf_role": "trajectory_id", "long_name": "track id"},
            ),
            "obs": ("obs", np.arange(n_obs)),
            "time": (dims, tvals, {"standard_name": "time", "long_name": "valid time"}),
            "latitude": (
                dims,
                arrays["lat"],
                {"standard_name": "latitude", "units": "degrees_north"},
            ),
            "longitude": (
                dims,
                arrays["lon"],
                {"standard_name": "longitude", "units": "degrees_east"},
            ),
        },
    )
    if np.isfinite(arrays["zs"]).any():
        out["zs"] = (dims, arrays["zs"], {"units": "m", "long_name": "surface height at centre"})
    if np.isfinite(arrays["vort"]).any():
        out["vort_max"] = (
            dims,
            arrays["vort"],
            {
                "units": "s-1",
                "long_name": f"maximum cyclonic relative vorticity within "
                f"{crit['wind_dist']:g} degrees",
            },
        )
    if layout["member"]:
        out = out.assign_coords(
            number=(
                "track",
                np.array([int(m) for m, _, _ in rows], dtype=int),
                {"long_name": "ensemble member"},
            )
        )
    if layout["init"]:
        out = out.assign_coords(
            forecast_reference_time=(
                "track",
                np.array([i for _, i, _ in rows]),
                {"standard_name": "forecast_reference_time"},
            )
        )
    for name in out.data_vars:
        out[name].attrs["coordinates"] = "time latitude longitude"
    out.attrs.update(featureType="trajectory", Conventions="CF-1.13")
    return out


if __name__ == "__main__":
    tc_track()
