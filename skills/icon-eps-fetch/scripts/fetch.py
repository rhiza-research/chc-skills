# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cfgrib",
#   "cftime>=1.6",
#   # eccodeslib carries the native libeccodes that cfgrib loads on macOS/Linux;
#   # eccodes drops this as a transitive dep via PyPI metadata (ecmwf/eccodes-python#150),
#   # so it is declared directly. Windows bundles the library in eccodes itself, and
#   # eccodeslib has no Windows wheels.
#   "eccodeslib; sys_platform != 'win32'",
#   "numpy",
#   "pandas",
#   "scipy",
#   "xarray",
# ]
# ///
"""Fetch the DWD ICON-EPS global ensemble from DWD open data as a weather-skills Zarr."""

# Source-to-output transform declaration (everything else passes through):
# - Grid: DWD publishes ICON-EPS global only on its triangular (icosahedral) R02B07 grid,
#   1,310,720 cells with no coordinates in the GRIB. Cell centres come from the run's
#   `clat`/`clon` files, and every field is regridded onto a regular lat/lon grid
#   (`--target-resolution`, default 0.25 deg): linear barycentric interpolation over a
#   Delaunay triangulation of the cell centres (`--regrid linear`), or the nearest cell
#   (`--regrid nearest`). Targets outside the triangulation (poles) take the nearest cell.
# - Variable names: DWD folder names map to the cfgrib-style short names below
#   (tot_prec -> tp, t_2m -> t2m, ...); the decoded GRIB name is replaced by that token.
# - Units: GRIB `**` exponents are respelled (`kg m**-2` -> `kg m-2`, same unit).
#   `tp` (kg m-2 accumulated since init) becomes a `mm day-1` rate over [step, next step)
#   by deaccumulation, labelled at the interval start; the last lead is dropped.
#   `t2m`, `d2m` K -> degree_Celsius; `sp` Pa -> hPa (core standard units).
# - long_name `Total precipitation` on `tp`.

import bz2
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import is_transient
from weather_skills_core.units import (
    convert_dataarray,
    normalize_unit_strings,
    precip_amounts_to_rates,
    stamp_data_interval,
    to_standard_units,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

_BASE_URL = "https://opendata.dwd.de/weather/nwp/icon-eps/grib"
_FIELD_FILE = "icon-eps_global_icosahedral_single-level_{init}_{step:03d}_{folder}.grib2.bz2"
_GRID_FILE = "icon-eps_global_icosahedral_time-invariant_{init}_{folder}.grib2.bz2"
_HTTP_TIMEOUT = 120
_RETRIES = 4

# `-v` token -> DWD folder name. Most used first.
VARIABLES: dict[str, str] = {
    "tp": "tot_prec",
    "t2m": "t_2m",
    "d2m": "td_2m",
    "r2": "relhum_2m",
    "u10": "u_10m",
    "v10": "v_10m",
    "fg10": "vmax_10m",
    "tcc": "clct",
    "sp": "ps",
}
DEFAULT_VARIABLES = ["tp"]
_DEWPOINT = frozenset({"d2m"})
_LONG_NAMES = {"tp": "Total precipitation"}


def _canonical_name(token: str) -> str | None:
    if token in VARIABLES:
        return token
    for short, folder in VARIABLES.items():
        if token == folder:
            return short
    return None


def _resolve_variables(raw: list[str] | None) -> list[str]:
    tokens = raw or list(DEFAULT_VARIABLES)
    unknown = [token for token in tokens if _canonical_name(token) is None]
    if unknown:
        raise UsageError(
            f"unknown variable(s): {', '.join(unknown)}.\n"
            f"Available (most used first): {', '.join(VARIABLES)} "
            f"(DWD names {', '.join(VARIABLES.values())} also accepted)"
        )
    resolved: list[str] = []
    for token in tokens:
        name = _canonical_name(token)
        if name not in resolved:
            resolved.append(name)
    return resolved


def _parse_hours(value: str, flag: str) -> int:
    """A pandas timedelta string (168h, 7D) as whole hours."""
    import pandas as pd

    try:
        hours = pd.Timedelta(value) / pd.Timedelta(hours=1)
    except ValueError:
        raise UsageError(f"{flag} {value!r} is not a timedelta (168h, 7D)") from None
    if hours <= 0 or hours != int(hours):
        raise UsageError(f"{flag} must be a positive whole number of hours, got {value!r}")
    return int(hours)


def _http_get(url: str) -> bytes | None:
    """GET with retries on transient errors; None on 404."""
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            err = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            err = exc
        if attempt == _RETRIES - 1 or not is_transient(err):
            raise DataError(
                f"DWD open data request failed for {url} ({err}); this is a transport "
                "problem, retry later or lower --workers."
            ) from None
        time.sleep(5 * (attempt + 1))
    return None


def _list_folder(run: int, folder: str) -> str:
    body = _http_get(f"{_BASE_URL}/{run:02d}/{folder}/")
    if body is None:
        raise DataError(f"no {folder!r} folder for the {run:02d} UTC run on {_BASE_URL}.")
    return body.decode("utf-8", errors="replace")


def _published(run: int, folder: str) -> dict[str, list[int]]:
    """Init (YYYYMMDDHH) -> sorted leads (hours) listed for one variable folder."""
    found: dict[str, set[int]] = {}
    pattern = rf"single-level_(\d{{10}})_(\d{{3}})_{re.escape(folder)}\.grib2\.bz2"
    for init, step in re.findall(pattern, _list_folder(run, folder)):
        found.setdefault(init, set()).add(int(step))
    return {init: sorted(steps) for init, steps in found.items()}


def _init_iso(init: str) -> str:
    """YYYYMMDDHH (UTC) -> YYYY-MM-DD."""
    return f"{init[:4]}-{init[4:6]}-{init[6:8]}"


def _choose_steps(available: list[int], max_lead: int, interval: int) -> list[int]:
    """Published leads that are multiples of ``interval`` up to ``max_lead``.

    ICON-EPS output thins out with lead time (hourly to 48 h, 3-hourly to 72 h,
    6-hourly to 120 h, then 12-hourly), so wanted leads the server lacks are skipped.
    """
    wanted = list(range(0, max_lead + 1, interval))
    steps = [s for s in wanted if s in set(available)]
    skipped = [s for s in wanted if s not in steps and s <= max(available, default=0)]
    if skipped:
        print(f"Not published at these leads, skipped: {skipped} h", file=sys.stderr)
    if steps and steps[-1] < max_lead:
        print(
            f"Last available lead is {steps[-1]} h (requested --max-lead {max_lead} h); "
            "06/18 UTC runs end at 120 h and a run still uploading is incomplete.",
            file=sys.stderr,
        )
    return steps


def _download_grib(url: str, dest: Path) -> Path:
    body = _http_get(url)
    if body is None:
        raise DataError(f"{url} is not on the server (DWD keeps only ~24 h of runs).")
    dest.write_bytes(bz2.decompress(body))
    return dest


def _open_values(path: Path):
    """The single field in a DWD GRIB as (DataArray on (number?, values))."""
    import xarray as xr

    with xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""}) as ds:
        if not ds.data_vars:
            raise DataError(f"DWD GRIB {path.name} contained no field.")
        return ds[next(iter(ds.data_vars))].load()


def _fetch_grid(run: int, init: str, tmp: Path):
    """Cell-centre latitudes and longitudes (degrees) of the ICON-EPS grid."""
    import numpy as np

    coords = []
    for folder in ("clat", "clon"):
        url = f"{_BASE_URL}/{run:02d}/{folder}/" + _GRID_FILE.format(init=init, folder=folder)
        values = _open_values(_download_grib(url, tmp / f"{folder}.grib2")).values
        coords.append(np.asarray(values, dtype="float64"))
    clat, clon = coords
    if np.nanmax(np.abs(clat)) <= np.pi / 2 + 1e-6:  # radians in some ICON grid files
        clat, clon = np.rad2deg(clat), np.rad2deg(clon)
    return clat, clon


def _fetch_field(run: int, folder: str, init: str, step: int, tmp: Path):
    """One lead of one variable: DataArray on (number, values) with GRIB attrs and ``time``."""
    url = f"{_BASE_URL}/{run:02d}/{folder}/" + _FIELD_FILE.format(
        init=init, step=step, folder=folder
    )
    path = _download_grib(url, tmp / f"{folder}_{step:03d}.grib2")
    try:
        da = _open_values(path)
    finally:
        path.unlink(missing_ok=True)
    if "number" not in da.dims:
        da = da.expand_dims("number")
    return da


class _Regridder:
    """Icosahedral cell centres -> regular lat/lon grid; weights built once, reused."""

    def __init__(self, clat, clon, lats, lons, method: str, margin: float = 1.0):
        import numpy as np
        from scipy.spatial import Delaunay, cKDTree

        clon = ((clon + 180.0) % 360.0) - 180.0
        north, south = lats.max() + margin, lats.min() - margin
        west, east = lons.min() - margin, lons.max() + margin
        # Pad with copies shifted by +-360 deg so a box at the dateline (or the whole
        # globe) is covered by the planar triangulation.
        src_idx, src_lon = [], []
        for shift in (-360.0, 0.0, 360.0):
            lon_s = clon + shift
            keep = np.where((clat >= south) & (clat <= north) & (lon_s >= west) & (lon_s <= east))[
                0
            ]
            src_idx.append(keep)
            src_lon.append(lon_s[keep])
        self.index = np.concatenate(src_idx)
        points = np.column_stack([np.concatenate(src_lon), clat[self.index]])
        if len(points) < 3:
            raise DataError("no ICON-EPS cells inside --bbox; widen it.")

        lon2d, lat2d = np.meshgrid(lons, lats)
        targets = np.column_stack([lon2d.ravel(), lat2d.ravel()])
        self.shape = (len(lats), len(lons))

        # Nearest cell on the sphere (3-D unit vectors), used by `nearest` and as the
        # fallback for targets outside the triangulation.
        def xyz(lon, lat):
            lon, lat = np.deg2rad(lon), np.deg2rad(lat)
            return np.column_stack(
                [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]
            )

        _, nearest = cKDTree(xyz(points[:, 0], points[:, 1])).query(
            xyz(targets[:, 0], targets[:, 1])
        )
        vertices = np.repeat(nearest[:, None], 3, axis=1)
        weights = np.zeros((len(targets), 3), dtype="float32")
        weights[:, 0] = 1.0
        if method == "linear":
            tri = Delaunay(points)
            simplex = tri.find_simplex(targets)
            inside = simplex >= 0
            transform = tri.transform[simplex[inside]]
            bary = np.einsum("nij,nj->ni", transform[:, :2], targets[inside] - transform[:, 2])
            weights[inside] = np.column_stack([bary, 1.0 - bary.sum(axis=1)])
            vertices[inside] = tri.simplices[simplex[inside]]
            outside = int((~inside).sum())
            if outside:
                print(
                    f"{outside} target points outside the triangulation take the nearest cell",
                    file=sys.stderr,
                )
        self.vertices, self.weights = vertices, weights

    def __call__(self, values):
        """(..., n_cells) on the full ICON grid -> (..., nlat, nlon)."""
        sub = values[..., self.index]
        out = (sub[..., self.vertices] * self.weights).sum(axis=-1)
        return out.reshape(values.shape[:-1] + self.shape)


def _target_axes(bbox, resolution: float):
    import numpy as np

    north, west, south, east = bbox if bbox is not None else (90.0, -180.0, -90.0, 180.0)
    if bbox is None:
        east = 180.0 - resolution  # [-180, 180)
    lats = np.round(np.arange(north, south - resolution / 2, -resolution), 6)
    lons = np.round(np.arange(west, east + resolution / 2, resolution), 6)
    if lats.size == 0 or lons.size == 0:
        raise UsageError(f"--bbox {bbox} holds no {resolution} deg grid points.")
    return lats, lons


def _to_celsius(da):
    if str(da.attrs.get("units") or "") in {"degree_Celsius", "degC", "celsius"}:
        return da
    converted, _ = convert_dataarray(da, "degree_Celsius")
    converted.attrs["units"] = "degree_Celsius"
    return converted


def _standardize(ds):
    """CF attrs + standard units; deaccumulate `tp` into rates."""
    normalize_unit_strings(ds)
    stamp_cf_attrs(ds)
    for name, long_name in _LONG_NAMES.items():
        if name in ds.data_vars:
            ds[name].attrs["long_name"] = long_name
    ds = to_standard_units(ds)
    for name in _DEWPOINT:
        if name in ds.data_vars:
            ds[name] = _to_celsius(ds[name])
    ds = precip_amounts_to_rates(ds)
    return stamp_data_interval(ds)


@weather_skill(
    name="icon-eps-fetch",
    version=_SKILL_VERSION,
)
@weather_skill.argument(
    "--date",
    help=(
        "Init date YYYY-MM-DD. Default: the newest init of --run on the server. DWD keeps "
        "only ~24 h of runs, so older dates are not available."
    ),
)
@weather_skill.argument(
    "--run",
    type=int,
    default=0,
    choices=(0, 6, 12, 18),
    help="Init hour (UTC). 00/12 runs reach 180 h, 06/18 runs 120 h. Default 0.",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Field to retrieve (repeatable). Most used first: tp, t2m, d2m, r2, u10, v10, fg10, "
        "tcc, sp. DWD folder names (tot_prec, t_2m, ...) are accepted. Default tp."
    ),
)
@weather_skill.argument(
    "--max-lead",
    default="168h",
    help="Last lead to keep (pandas timedelta: 168h, 7D). Default 168h (one week).",
)
@weather_skill.argument(
    "--step-interval",
    default="24h",
    help=(
        "Keep the published leads that are multiples of this (pandas timedelta). Default "
        "24h: daily precip totals from 8 files per week. Use 6h for sub-daily fields."
    ),
)
@weather_skill.argument(
    "--target-resolution",
    type=float,
    default=0.25,
    help="Regular lat/lon grid spacing in degrees (native grid is ~20 km). Default 0.25.",
)
@weather_skill.argument(
    "--regrid",
    default="linear",
    choices=("linear", "nearest"),
    help="Interpolation from the triangular grid: linear (barycentric) or nearest cell.",
)
@weather_skill.argument(
    "--workers",
    type=int,
    default=4,
    help="Parallel downloads (default 4). Lower it if DWD throttles.",
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help=(
        "Print the newest init date of --run (YYYY-MM-DD, usable as --date) on stdout and "
        "exit. IDENT is ignored. Does not download fields."
    ),
)
def fetch(
    date,
    run,
    bbox,
    variable,
    max_lead,
    step_interval,
    target_resolution,
    regrid,
    workers,
    probe_latest,
    **kwargs,
):
    """Fetch the DWD ICON-EPS global ensemble (40 members) from DWD open data, regridded
    from its triangular grid onto a regular lat/lon grid, as a weather-skills Zarr."""
    names = _resolve_variables(variable)
    published = _published(run, VARIABLES[names[0]])
    if not published:
        raise DataError(
            f"no {run:02d} UTC ICON-EPS run is listed on {_BASE_URL} (a run may be "
            "mid-upload); try another --run or retry later."
        )
    newest = max(published)

    if probe_latest is not None:
        print(f"Latest run: {newest[:8]} {newest[8:]} UTC", file=sys.stderr)
        print(_init_iso(newest))
        return

    init = newest if date is None else f"{date:%Y%m%d}{run:02d}"
    if init not in published:
        raise DataError(
            f"ICON-EPS init {init[:8]} {run:02d} UTC is not on the server; DWD keeps only "
            f"~24 h of runs (newest: {newest[:8]} {newest[8:]} UTC). Omit --date for the "
            "newest run."
        )
    if target_resolution <= 0:
        raise UsageError("--target-resolution must be positive.")
    lead = _parse_hours(max_lead, "--max-lead")
    interval = _parse_hours(step_interval, "--step-interval")

    import numpy as np
    import xarray as xr

    lats, lons = _target_axes(bbox, target_resolution)
    init_time = np.datetime64(f"{_init_iso(init)}T{init[8:]}:00", "ns")
    print(
        f"Fetching ICON-EPS {init[:8]} {run:02d} UTC, variables={','.join(names)}, "
        f"grid {target_resolution} deg {len(lats)}x{len(lons)} ({regrid})",
        file=sys.stderr,
    )

    with tempfile.TemporaryDirectory(prefix="icon-eps-fetch-") as tmpdir:
        tmp = Path(tmpdir)
        clat, clon = _fetch_grid(run, init, tmp)
        regridder = _Regridder(clat, clon, lats, lons, regrid)

        fields = {}
        for name in names:
            folder = VARIABLES[name]
            available = (
                published[init]
                if folder == VARIABLES[names[0]]
                else (_published(run, folder).get(init, []))
            )
            steps = _choose_steps(available, lead, interval)
            if len(steps) < (2 if name == "tp" else 1):
                raise DataError(
                    f"{folder}: too few leads for init {init} (have {available[:5]}...); "
                    "lower --step-interval or wait for the run to finish uploading."
                )

            def one(step, folder=folder, name=name):
                da = _fetch_field(run, folder, init, step, tmp)
                print(f"  {name} +{step:03d} h", file=sys.stderr)
                return xr.DataArray(
                    regridder(np.asarray(da.values, dtype="float32")),
                    dims=("number", "latitude", "longitude"),
                    coords={"number": da["number"].values, "latitude": lats, "longitude": lons},
                    attrs={k: v for k, v in da.attrs.items() if not k.startswith("GRIB_")},
                ).expand_dims(step=[np.timedelta64(step, "h").astype("timedelta64[ns]")])

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                parts = list(pool.map(one, steps))
            fields[name] = xr.concat(parts, dim="step")

    ds = xr.Dataset(fields).transpose("number", "step", "latitude", "longitude")
    if "tp" in ds.data_vars:
        # Accumulation is zero at the init by definition (the 0 h file can be empty).
        ds["tp"] = ds["tp"].where(ds["step"] != np.timedelta64(0, "ns"), 0.0)
    ds = ds.assign_coords(time=init_time)
    ds = ds.assign_coords(valid_time=("step", init_time + ds["step"].values))
    ds.attrs.update(
        Conventions="CF-1.13",
        weather_skills_source=f"dwd-icon-eps:{run:02d}/{init}",
        institution="Deutscher Wetterdienst (DWD)",
        license="CC BY 4.0; source: Deutscher Wetterdienst",
        regridding=(f"{regrid} from the ICON R02B07 triangular grid to {target_resolution} deg"),
    )
    return _standardize(ds)


if __name__ == "__main__":
    fetch()
