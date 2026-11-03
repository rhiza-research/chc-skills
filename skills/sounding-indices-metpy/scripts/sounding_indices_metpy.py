# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "metpy>=1.6",
#   "numpy",
#   "xarray",
# ]
# ///
"""Compute convective and severe-weather indices from vertical profiles with MetPy."""

from __future__ import annotations

import itertools
import os
import sys
import warnings

import numpy as np
import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import STANDARD, dequantify_dataset, ureg

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

# MetPy is imported inside functions, after weather_skills_core: importing core
# (cf_xarray's units) after MetPy puts MetPy's constants and the inputs in
# different pint registries.

# index -> (outputs, units, long names, per column?). Per-column indices run
# MetPy on one profile at a time; the others run on the whole array at once.
INDICES: dict[str, tuple[tuple[str, ...], str, tuple[str, ...], bool]] = {
    "sbcape": (
        ("sbcape", "sbcin"),
        "J kg-1",
        ("surface-based CAPE", "surface-based CIN"),
        True,
    ),
    "mlcape": (
        ("mlcape", "mlcin"),
        "J kg-1",
        ("mixed-layer CAPE (lowest 100 hPa)", "mixed-layer CIN (lowest 100 hPa)"),
        True,
    ),
    "mucape": (
        ("mucape", "mucin"),
        "J kg-1",
        ("most-unstable CAPE (lowest 300 hPa)", "most-unstable CIN (lowest 300 hPa)"),
        True,
    ),
    "lfc": (("lfc_pressure",), "hPa", ("level of free convection (surface parcel)",), True),
    "el": (("el_pressure",), "hPa", ("equilibrium level (surface parcel)",), True),
    "lifted_index": (("lifted_index",), "K", ("lifted index (surface parcel at 500 hPa)",), True),
    "showalter_index": (("showalter_index",), "K", ("Showalter index (850 hPa parcel)",), True),
    "bulk_shear": (("bulk_shear_0_6km",), "m s-1", ("0-6 km bulk wind shear",), True),
    "srh": (
        ("srh_0_1km", "srh_0_3km"),
        "m2 s-2",
        (
            "0-1 km storm-relative helicity (Bunkers right mover)",
            "0-3 km storm-relative helicity (Bunkers right mover)",
        ),
        True,
    ),
    "lcl": (
        ("lcl_pressure", "lcl_temperature"),
        "hPa,degree_Celsius",
        ("lifting condensation level (surface parcel)", "temperature at the LCL"),
        False,
    ),
    "k_index": (("k_index",), "degree_Celsius", ("K-index",), False),
    "total_totals_index": (("total_totals_index",), "K", ("total totals index",), False),
    "cross_totals": (("cross_totals",), "K", ("cross totals",), False),
    "vertical_totals": (("vertical_totals",), "K", ("vertical totals",), False),
    "galvez_davison_index": (("galvez_davison_index",), "1", ("Galvez-Davison index",), False),
    "precipitable_water": (("precipitable_water",), "mm", ("precipitable water",), False),
}
DEFAULT_INDICES = (
    "sbcape",
    "lifted_index",
    "lcl",
    "k_index",
    "total_totals_index",
    "precipitable_water",
)
_KINEMATIC = ("bulk_shear", "srh")

# Roles filled from the input when --var leaves them out: CF standard names
# first, then variable names. Level roles must have a pressure dim; surface
# roles must not.
ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    "temperature": (("air_temperature",), ("t", "ta", "temperature", "air_temperature"), True),
    "dewpoint": (("dew_point_temperature",), ("dewpoint", "td", "dpt"), True),
    "specific_humidity": (("specific_humidity",), ("q", "hus", "specific_humidity"), True),
    "relative_humidity": (("relative_humidity",), ("r", "hur", "rh", "relative_humidity"), True),
    "u": (("eastward_wind",), ("u", "ua", "u_component_of_wind"), True),
    "v": (("northward_wind",), ("v", "va", "v_component_of_wind"), True),
    "height": (("geopotential_height",), ("gh", "zg", "geopotential_height"), True),
    "geopotential": (("geopotential",), ("z", "geopotential"), True),
    "surface_pressure": (
        ("surface_air_pressure",),
        ("sp", "surface_pressure", "pressure_surface", "ps"),
        False,
    ),
    "surface_temperature": (
        ("air_temperature",),
        ("t2m", "2m_temperature", "temperature_2m", "tas"),
        False,
    ),
    "surface_dewpoint": (
        ("dew_point_temperature",),
        ("d2m", "2m_dewpoint_temperature", "dewpoint_temperature_2m", "tdps"),
        False,
    ),
}


def _pressure_dim(ds: xr.Dataset) -> str | None:
    """The dim whose coordinate is a pressure (hPa, Pa, ...)."""
    for dim in ds.dims:
        coord = ds.coords.get(dim)
        units = coord.attrs.get("units") if coord is not None else None
        if not units:
            continue
        try:
            if ureg.Unit(units).is_compatible_with(ureg.Unit("Pa")):
                return dim
        except Exception:  # noqa: BLE001, S112 — not a unit pint knows
            continue
    return None


def _match(ds: xr.Dataset, role: str, vdim: str, given: dict[str, str]) -> str | None:
    """The input variable for ``role``; ambiguity is an error."""
    if role in given:
        return given[role]
    standard_names, names, on_levels = ROLES[role]

    def fits(name):
        return (vdim in ds[name].dims) == on_levels

    by_std = [
        n
        for n, da in ds.data_vars.items()
        if da.attrs.get("standard_name") in standard_names and fits(n)
    ]
    by_name = [n for n in names if n in ds.data_vars and fits(n)]
    found = list(dict.fromkeys(by_std or by_name))
    if len(found) > 1:
        raise UsageError(
            f"several variables could be {role}: {', '.join(found)}; choose with --var {role}=NAME"
        )
    return found[0] if found else None


def _as(da: xr.DataArray, units: str, mu) -> np.ndarray:
    """``da`` as a float array in ``units`` (MetPy's registry handles offsets)."""
    src = da.attrs.get("units")
    if not src:
        raise UsageError(f"{da.name} has no units attr; MetPy needs units")
    try:
        return mu.Quantity(da.values.astype("float64"), src).to(units).magnitude
    except Exception as exc:
        raise UsageError(f"{da.name} units {src!r} do not convert to {units}: {exc}") from exc


def _compact(valid: np.ndarray, *arrays: np.ndarray) -> list[np.ndarray]:
    """Move each column's valid levels to the top (index 0 = lowest), NaN after."""
    order = np.argsort(~valid, axis=0, kind="stable")
    out = []
    for a in arrays:
        a = np.take_along_axis(a, order, axis=0)
        out.append(np.where(np.take_along_axis(valid, order, axis=0), a, np.nan))
    return out


def _profile(p, t, td, kin, wanted, mpcalc, mu):
    """Per-profile indices for one column: ({index: values}, [failed indices])."""
    cache = {}

    def prof():
        if "prof" not in cache:
            cache["prof"] = mpcalc.parcel_profile(p, t[0], td[0])
        return cache["prof"]

    def cape_cin(pair):
        return pair[0].m_as("J/kg"), pair[1].m_as("J/kg")

    calcs = {
        "sbcape": lambda: cape_cin(mpcalc.cape_cin(p, t, td, prof())),
        "mlcape": lambda: cape_cin(mpcalc.mixed_layer_cape_cin(p, t, td)),
        "mucape": lambda: cape_cin(mpcalc.most_unstable_cape_cin(p, t, td)),
        "lfc": lambda: (mpcalc.lfc(p, t, td, prof())[0].m_as("hPa"),),
        "el": lambda: (mpcalc.el(p, t, td, prof())[0].m_as("hPa"),),
        "lifted_index": lambda: (
            np.ravel(mpcalc.lifted_index(p, t, prof()).m_as("delta_degC"))[0],
        ),
        "showalter_index": lambda: (
            np.ravel(mpcalc.showalter_index(p, t, td).m_as("delta_degC"))[0],
        ),
    }
    if kin is not None:
        pk, uk, vk, zk = kin

        def shear():
            su, sv = mpcalc.bulk_shear(pk, uk, vk, height=zk, depth=6 * mu.km)
            return (np.hypot(su, sv).m_as("m/s"),)

        def helicity():
            right, _, _ = mpcalc.bunkers_storm_motion(pk, uk, vk, zk)
            return tuple(
                mpcalc.storm_relative_helicity(
                    zk, uk, vk, depth * mu.km, storm_u=right[0], storm_v=right[1]
                )[2].m_as("m^2/s^2")
                for depth in (1, 3)
            )

        calcs.update(bulk_shear=shear, srh=helicity)

    values, failed = {}, []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for idx in wanted:
            if idx not in calcs:
                continue
            # Each index on its own: Showalter has no 850 hPa parcel over high
            # ground, which must not blank CAPE or shear in that column.
            try:
                values[idx] = calcs[idx]()
            except Exception:  # noqa: BLE001 — one odd profile must not stop the field
                failed.append(idx)
    return values, failed


def _column_batch(job: dict) -> dict:
    """Per-profile MetPy indices for a batch of columns (runs in worker processes)."""
    import metpy.calc as mpcalc
    from metpy.units import units as mu

    p_all, t_all, td_all, n_all = job["p"], job["t"], job["td"], job["n"]
    wind = job.get("wind")
    wanted = job["indices"]
    ncol = p_all.shape[1]
    out = {name: np.full(ncol, np.nan) for idx in wanted for name in INDICES[idx][0]}
    failed = dict.fromkeys(wanted, 0)
    for i in range(ncol):
        n = int(n_all[i])
        if n < 3:
            continue
        kin = None
        if wind is not None and int(wind["n"][i]) >= 3:
            nk = int(wind["n"][i])
            kin = (
                wind["p"][:nk, i] * mu.hPa,
                wind["u"][:nk, i] * mu("m/s"),
                wind["v"][:nk, i] * mu("m/s"),
                wind["z"][:nk, i] * mu.m,
            )
        values, bad = _profile(
            p_all[:n, i] * mu.hPa,
            t_all[:n, i] * mu.degC,
            td_all[:n, i] * mu.degC,
            kin,
            wanted,
            mpcalc,
            mu,
        )
        for idx in bad:
            failed[idx] += 1
        for idx, vals in values.items():
            for name, value in zip(INDICES[idx][0], vals, strict=True):
                out[name][i] = value
    out["__failed__"] = failed
    return out


def _fast_indices(wanted, p_lev, t_lev, td_lev, sp, p_c, t_c, td_c, mu, mpcalc, consts):
    """Indices MetPy computes for every column at once."""
    out: dict[str, np.ndarray] = {}
    P = p_lev * mu.hPa
    T = t_lev * mu.degC
    Td = td_lev * mu.degC
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "k_index" in wanted:
            out["k_index"] = np.asarray(mpcalc.k_index(P, T, Td, vertical_dim=0).m_as("degC"))
        if "total_totals_index" in wanted:
            tt = mpcalc.total_totals_index(P, T, Td, vertical_dim=0)
            out["total_totals_index"] = np.asarray(tt.m_as("delta_degC"))
        if "cross_totals" in wanted:
            ct = mpcalc.cross_totals(P, T, Td, vertical_dim=0)
            out["cross_totals"] = np.asarray(ct.m_as("delta_degC"))
        if "vertical_totals" in wanted:
            vt = mpcalc.vertical_totals(P, T, vertical_dim=0)
            out["vertical_totals"] = np.asarray(vt.m_as("delta_degC"))
        if "galvez_davison_index" in wanted:
            w = mpcalc.mixing_ratio(mpcalc.saturation_vapor_pressure(Td), P)
            psfc = (sp if sp is not None else p_lev[0]) * mu.hPa
            gdi = mpcalc.galvez_davison_index(P, T, w, psfc, vertical_dim=0)
            out["galvez_davison_index"] = np.asarray(gdi.m_as("dimensionless"))
        if "lcl" in wanted:
            lcl_p, lcl_t = mpcalc.lcl(p_c[0] * mu.hPa, t_c[0] * mu.degC, td_c[0] * mu.degC)
            out["lcl_pressure"] = np.asarray(lcl_p.m_as("hPa"))
            out["lcl_temperature"] = np.asarray(lcl_t.m_as("degC"))
        if "precipitable_water" in wanted:
            # MetPy's precipitable_water (-trapz(w, p) / (g rho_l)) on every column at
            # once, over each column's valid levels only.
            w = mpcalc.mixing_ratio(
                mpcalc.saturation_vapor_pressure(td_c * mu.degC), p_c * mu.hPa
            ).m_as("dimensionless")
            p_pa = p_c * 100.0
            seg = 0.5 * (w[1:] + w[:-1]) * (p_pa[:-1] - p_pa[1:])
            total = np.nansum(np.where(np.isfinite(seg), seg, 0.0), axis=0)
            g = consts.g.m_as("m/s^2")
            rho = consts.rho_l.m_as("kg/m^3")
            pw = total / (g * rho) * 1000.0
            out["precipitable_water"] = np.where(np.isfinite(p_c[1]), pw, np.nan)
    return out


def _unit_spelling(units: str) -> str:
    """Core's spelling for standard units, CF form otherwise."""
    unit = ureg.Unit(units)
    standard = {ureg.Unit(spec["units"]): spec["units"] for spec in STANDARD.values()}
    return standard.get(unit) or format(unit, "cf") or "1"


@weather_skill(name="sounding-indices-metpy", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    action="append",
    required=True,
    help=(
        "Zarr with temperature and moisture on pressure levels; repeat to add a store "
        "with surface fields (sp, t2m, d2m) or winds and heights."
    ),
)
@weather_skill.argument(
    "--index",
    action="append",
    default=[],
    metavar="INDEX",
    help=(
        f"Index to compute, repeatable: {', '.join(INDICES)}, or 'all'. "
        f"Default: {', '.join(DEFAULT_INDICES)}."
    ),
)
@weather_skill.argument(
    "--var",
    action="append",
    default=[],
    metavar="ROLE=NAME",
    help=(
        f"Use variable NAME for ROLE ({', '.join(ROLES)}). Roles left out are matched "
        "from CF standard names and common names."
    ),
)
@weather_skill.argument(
    "--workers",
    type=int,
    default=1,
    help="Processes for the per-column indices (CAPE, shear, ...). Default: 1.",
)
def sounding_indices(ds, index, var, workers, **kwargs):
    """Compute convective and severe-weather indices from vertical profiles with MetPy.

    CAPE/CIN (surface-based, mixed-layer, most-unstable), LCL, LFC, EL, lifted,
    Showalter, K, total totals, cross/vertical totals and Galvez-Davison indices,
    precipitable water, 0-6 km bulk shear and storm-relative helicity, for every
    column of a pressure-level forecast, reanalysis or sounding Zarr. Every dim
    other than pressure is kept.
    """
    import metpy
    import metpy.calc as mpcalc
    import metpy.constants as consts
    from metpy.units import units as mu

    wanted = list(dict.fromkeys(index)) or list(DEFAULT_INDICES)
    if "all" in wanted:
        wanted = list(INDICES)
    unknown = [i for i in wanted if i not in INDICES]
    if unknown:
        raise UsageError(f"unknown --index {', '.join(unknown)}; choose from {', '.join(INDICES)}")
    if workers < 1:
        raise UsageError("--workers must be 1 or more")

    given = {}
    for item in var:
        role, sep, name = item.partition("=")
        if not sep or role not in ROLES or not name:
            raise UsageError(
                f"--var {item!r} must be ROLE=NAME with ROLE one of {', '.join(ROLES)}"
            )
        given[role] = name

    datasets = [dequantify_dataset(d) for d in (ds if isinstance(ds, list) else [ds])]
    try:
        merged = xr.merge(
            datasets, join="inner", compat="no_conflicts", combine_attrs="drop_conflicts"
        )
    except Exception as exc:
        raise UsageError(f"inputs do not combine: {exc}") from exc
    missing_vars = [n for n in given.values() if n not in merged.data_vars]
    if missing_vars:
        raise UsageError(
            f"--var names not in the input: {', '.join(missing_vars)}; "
            f"variables: {', '.join(merged.data_vars)}"
        )

    vdim = _pressure_dim(merged)
    if vdim is None:
        raise UsageError(
            "no pressure-level dim found (a dim whose coordinate has pressure units); "
            "fetch pressure-level fields, e.g. dynamical-fetch -v t -v q"
        )
    # Surface first: MetPy's profile functions want decreasing pressure.
    merged = merged.sortby(vdim, ascending=False)
    p_levels = _as(merged[vdim], "hPa", mu)

    t_name = _match(merged, "temperature", vdim, given)
    if t_name is None:
        raise UsageError("no temperature on pressure levels found; pass --var temperature=NAME")
    moist = {
        role: _match(merged, role, vdim, given)
        for role in ("dewpoint", "specific_humidity", "relative_humidity")
    }
    moist_role = next((r for r, n in moist.items() if n), None)
    if moist_role is None:
        raise UsageError(
            "no moisture on pressure levels found (dewpoint, q or r); pass e.g. "
            "--var specific_humidity=NAME"
        )
    sources = {"temperature": t_name, moist_role: moist[moist_role]}

    # Level arrays as (level, column).
    t_da = merged[t_name]
    other = [d for d in t_da.dims if d != vdim]
    shape = [t_da.sizes[d] for d in other]

    def levels(da):
        return xr.broadcast(da, t_da)[0].transpose(vdim, *other).values.reshape(len(p_levels), -1)

    def flat(da):
        return xr.broadcast(da, t_da.isel({vdim: 0}, drop=True))[0].transpose(*other)

    t_lev = _as(xr.DataArray(levels(t_da), attrs=t_da.attrs, name=t_name), "degC", mu)
    moist_da = merged[moist[moist_role]]
    raw = xr.DataArray(levels(moist_da), attrs=moist_da.attrs, name=moist_da.name)
    p2 = np.broadcast_to(p_levels[:, None], t_lev.shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if moist_role == "dewpoint":
            td_lev = _as(raw, "degC", mu)
        elif moist_role == "specific_humidity":
            q = _as(raw, "dimensionless", mu)
            td_lev = mpcalc.dewpoint_from_specific_humidity(p2 * mu.hPa, q).m_as("degC")
        else:
            rh = _as(raw, "percent", mu) / 100.0
            td_lev = mpcalc.dewpoint_from_relative_humidity(t_lev * mu.degC, rh).m_as("degC")

    # Surface: drop levels below ground, start the parcel at the surface when
    # the 2 m fields are there.
    sp = None
    sp_name = _match(merged, "surface_pressure", vdim, given)
    if sp_name:
        sp = _as(
            flat(merged[sp_name]).rename(sp_name).assign_attrs(merged[sp_name].attrs), "hPa", mu
        )
        sp = sp.reshape(-1)
        sources["surface_pressure"] = sp_name
    t2_name = _match(merged, "surface_temperature", vdim, given)
    d2_name = _match(merged, "surface_dewpoint", vdim, given)
    use_surface = sp is not None and t2_name and d2_name
    below = p2 > sp[None, :] if sp is not None else np.zeros_like(p2, dtype=bool)
    if use_surface:
        below = p2 >= sp[None, :]
        t2 = _as(flat(merged[t2_name]).assign_attrs(merged[t2_name].attrs), "degC", mu).reshape(-1)
        d2 = _as(flat(merged[d2_name]).assign_attrs(merged[d2_name].attrs), "degC", mu).reshape(-1)
        sources.update(surface_temperature=t2_name, surface_dewpoint=d2_name)
        p_prof = np.vstack([sp[None, :], np.where(below, np.nan, p2)])
        t_prof = np.vstack([t2[None, :], t_lev])
        td_prof = np.vstack([d2[None, :], td_lev])
    else:
        p_prof = np.where(below, np.nan, p2)
        t_prof, td_prof = t_lev, td_lev
    valid = np.isfinite(p_prof) & np.isfinite(t_prof) & np.isfinite(td_prof)
    p_c, t_c, td_c = _compact(valid, p_prof, t_prof, td_prof)
    n_valid = valid.sum(axis=0)

    wind = None
    if set(_KINEMATIC) & set(wanted):
        u_name = _match(merged, "u", vdim, given)
        v_name = _match(merged, "v", vdim, given)
        z_name = _match(merged, "height", vdim, given)
        geo = None if z_name else _match(merged, "geopotential", vdim, given)
        if not (u_name and v_name and (z_name or geo)):
            raise UsageError(
                "bulk_shear and srh need u, v and geopotential height (gh) or geopotential (z) "
                "on pressure levels; add them with another -i or --var, or drop those indices"
            )
        u_lev = _as(xr.DataArray(levels(merged[u_name]), attrs=merged[u_name].attrs), "m/s", mu)
        v_lev = _as(xr.DataArray(levels(merged[v_name]), attrs=merged[v_name].attrs), "m/s", mu)
        if z_name:
            z_lev = _as(xr.DataArray(levels(merged[z_name]), attrs=merged[z_name].attrs), "m", mu)
        else:
            phi = _as(xr.DataArray(levels(merged[geo]), attrs=merged[geo].attrs), "m^2/s^2", mu)
            z_lev = mpcalc.geopotential_to_height(phi * mu("m^2/s^2")).m_as("m")
        sources.update(u=u_name, v=v_name, height=z_name or geo)
        # Heights above ground: the height of the lowest level above ground, or of
        # the surface pressure (log-p interpolation) when sp is given.
        okw = ~below & np.isfinite(u_lev) & np.isfinite(v_lev) & np.isfinite(z_lev)
        pk, uk, vk, zk = _compact(okw, p2.copy(), u_lev, v_lev, z_lev)
        if sp is not None:
            z_sfc = np.array(
                [
                    np.interp(np.log(sp[i]), np.log(p_levels[::-1]), z_lev[::-1, i])
                    for i in range(z_lev.shape[1])
                ]
            )
        else:
            z_sfc = zk[0]
        wind = {"p": pk, "u": uk, "v": vk, "z": zk - z_sfc[None, :], "n": okw.sum(axis=0)}

    mapping = ", ".join(f"{k}={v}" for k, v in sources.items())
    print(f"sounding-indices-metpy: {', '.join(wanted)} from {mapping}", file=sys.stderr)
    if sp is None:
        print(
            "sounding-indices-metpy: no surface pressure, so every level is used as above "
            "ground; over high terrain pass sp (and t2m, d2m) with another -i",
            file=sys.stderr,
        )

    results = _fast_indices(
        [i for i in wanted if not INDICES[i][3]],
        p2,
        t_lev,
        td_lev,
        sp,
        p_c,
        t_c,
        td_c,
        mu,
        mpcalc,
        consts,
    )

    column_indices = [i for i in wanted if INDICES[i][3]]
    if column_indices:
        ncol = p_c.shape[1]
        n_jobs = 1 if workers == 1 else min(ncol, workers * 4)
        bounds = np.linspace(0, ncol, n_jobs + 1).astype(int)
        jobs = []
        for a, b in itertools.pairwise(bounds):
            job = {
                "p": p_c[:, a:b],
                "t": t_c[:, a:b],
                "td": td_c[:, a:b],
                "n": n_valid[a:b],
                "indices": column_indices,
            }
            if wind is not None:
                job["wind"] = {k: (v[:, a:b] if v.ndim == 2 else v[a:b]) for k, v in wind.items()}
            jobs.append(job)
        if workers == 1:
            parts = [_column_batch(j) for j in jobs]
        else:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=min(workers, os.cpu_count() or 1)) as pool:
                parts = list(pool.map(_column_batch, jobs))
        failed = dict.fromkeys(column_indices, 0)
        for part in parts:
            for idx, count in part.pop("__failed__").items():
                failed[idx] += count
        for name in parts[0]:
            results[name] = np.concatenate([p[name] for p in parts])
        for idx, count in failed.items():
            if count:
                print(
                    f"sounding-indices-metpy: {idx}: MetPy could not compute {count} of "
                    f"{ncol} profiles (NaN there)",
                    file=sys.stderr,
                )

    template = t_da.isel({vdim: 0}, drop=True)
    coords = {k: v for k, v in template.coords.items() if vdim not in v.dims}
    out = xr.Dataset(coords=coords)
    for idx in wanted:
        names, units, long_names, per_column = INDICES[idx]
        unit_list = units.split(",") if "," in units else [units] * len(names)
        for name, unit, long_name in zip(names, unit_list, long_names, strict=True):
            data = np.asarray(results[name], dtype="float64").reshape(shape)
            out[name] = xr.DataArray(data, dims=other, coords=coords).assign_attrs(
                units=_unit_spelling(unit),
                long_name=long_name,
                comment=(
                    f"MetPy {metpy.__version__}, "
                    + ("per profile" if per_column else "whole field")
                    + f"; inputs {mapping}"
                ),
            )
    out.attrs = dict(merged.attrs)
    out.attrs.setdefault("Conventions", "CF-1.13")
    out.attrs["metpy_version"] = metpy.__version__
    return out


if __name__ == "__main__":
    sounding_indices()
