# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "cftime>=1.6",
#   "matplotlib>=3.8",
#   "numpy",
#   "pandas",
#   "pillow",
#   "xarray",
#   "zarr",
# ]
# ///
"""Render a local wave phase PNG (rose, phase diagram, amplitude) from olr-wave-filter output."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

KINDS = ("rose", "rose-daily", "phase", "amplitude")
WAVE_NAMES = ("kelvin", "er", "mjo", "mrg_td", "low")
LABELS = {"low": "Low", "mjo": "MJO", "kelvin": "Kelvin", "er": "ER", "mrg_td": "MRG/TD"}
AMP_LAT = 5  # A and B are the 5S-5N filtered anomaly and its time derivative
OBS_DAYS = 30  # observed days drawn before the forecast
WEEK_DAYS = (3, 10, 17, 24, 31, 38)  # days after init: mid-week of forecast weeks 1-6
LIM = 4.5
WEEK_COLORS = ("#1B5E20", "#00A6B8", "#F57C00", "#C2185B", "#1A237E", "#5D4037")
ROSE_AMP_BINS = (1, 1.5, 2, 3, float("inf"))
ROSE_COLORS = ("#FFD54F", "#FB8C00", "#D84315", "#6D1B7B")
PHASE_COLORS = (
    "#A84A12",
    "#E8920C",
    "#8C7A00",
    "#3FAE2A",
    "#00918A",
    "#2A86E8",
    "#8B3FBF",
    "#C9356E",
)


def _phase_of(x, y):
    """Phase 1..8 of (x, y) = (-A, -B): P5 (wet) centred on +x, P1 (dry) on -x, clockwise."""
    import numpy as np

    theta = np.degrees(np.arctan2(y, x))
    return (np.round(((180 - theta) % 360) / 45).astype(int) % 8) + 1


def _phase_label(p, sep="\n"):
    return f"P{p}" + {1: f"{sep}dry", 5: f"{sep}wet"}.get(p, "")


def _local(ds, wave, lon):
    """Members' (x, y) = (-A, -B) over the seasonal ERA5 std at the nearest longitude,
    plus the climatology rows there: (x, y, clim dict, longitude used)."""
    import numpy as np

    lons = ds.longitude.values
    i = int(np.argmin(np.abs((lons - lon + 180) % 360 - 180)))
    field = ds[f"olr_{wave}"].isel(longitude=i)
    lats = field.latitude
    band = field.sel(latitude=lats[np.abs(lats) <= AMP_LAT + 1e-6])
    a = band.weighted(np.cos(np.deg2rad(band.latitude))).mean("latitude")
    a = a.transpose("number", "time").values.astype(np.float64)
    b = np.gradient(a, axis=1)
    sa = float(ds[f"olr_{wave}_local_std_a"].isel(longitude=i))
    sb = float(ds[f"olr_{wave}_local_std_b"].isel(longitude=i))
    clim = {
        "p90": ds[f"olr_{wave}_clim_amp_p90"].isel(longitude=i).values,
        "p99": ds[f"olr_{wave}_clim_amp_p99"].isel(longitude=i).values,
        "p90_all": float(ds[f"olr_{wave}_clim_amp_p90_all"].isel(longitude=i)),
    }
    return -a / sa, -b / sb, clim, float(lons[i])


def _plot_phase(x, y, clim, times, init, where, wave):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import patheffects
    from matplotlib.patches import Circle, Wedge

    fc_days = np.flatnonzero(times >= init)
    obs_days = np.flatnonzero((times < init) & (times >= init - np.timedelta64(OBS_DAYS, "D")))
    weeks = [fc_days[d] for d in WEEK_DAYS if d < len(fc_days)]
    colors = WEEK_COLORS[: len(weeks)]
    xm, ym = x.mean(0), y.mean(0)

    fig, ax = plt.subplots(figsize=(15, 10.5), layout="constrained")
    for p in range(1, 9):
        centre = 180 - 45 * (p - 1)
        for r, c in ((clim["p99"][p - 1], "#8c8cc4"), (clim["p90"][p - 1], "#d8c9a0")):
            if np.isfinite(r) and r > 1:
                ax.add_patch(
                    Wedge(
                        (0, 0),
                        r,
                        centre - 22.5,
                        centre + 22.5,
                        width=r - 1,
                        color=c,
                        lw=0,
                        zorder=0,
                    )
                )
        edge = np.radians(centre - 22.5)
        ax.plot(
            [np.cos(edge), 2 * LIM * np.cos(edge)],
            [np.sin(edge), 2 * LIM * np.sin(edge)],
            color="#4060a0",
            lw=0.8,
            ls="--",
            zorder=1,
        )
        ax.text(
            0.87 * LIM * np.cos(np.radians(centre)),
            0.87 * LIM * np.sin(np.radians(centre)),
            _phase_label(p),
            ha="center",
            va="center",
            fontsize=16,
            zorder=6,
        )
    ax.add_patch(Circle((0, 0), 1, facecolor="white", edgecolor="k", lw=1, zorder=1))

    ax.plot(xm[obs_days], ym[obs_days], "o-", color="tab:red", ms=3, lw=1.5, zorder=4)
    for i in obs_days[::7]:
        ax.annotate(
            times[i].strftime("%d %b"),
            (xm[i], ym[i]),
            fontsize=15,
            fontweight="bold",
            color="tab:red",
            xytext=(5, 5),
            textcoords="offset points",
            zorder=7,
            path_effects=[patheffects.withStroke(linewidth=3, foreground="white")],
        )
    for i, c in zip(weeks, colors, strict=True):
        ax.scatter(x[:, i], y[:, i], s=45, color=c, alpha=0.7, edgecolors="none", zorder=3)
    ax.plot(
        np.r_[xm[obs_days[-1:]], xm[fc_days]],
        np.r_[ym[obs_days[-1:]], ym[fc_days]],
        color="k",
        lw=2.5,
        zorder=5,
    )
    for i, c in zip(weeks, colors, strict=True):
        ax.scatter(xm[i], ym[i], s=200, color=c, edgecolors="k", linewidths=1.5, zorder=6)

    i0 = fc_days[0]
    amp0 = np.hypot(xm[i0], ym[i0])
    state = (
        f"P{_phase_of(xm[i0], ym[i0])}, amplitude {amp0:.1f}"
        if amp0 > 1
        else f"weak (amplitude {amp0:.1f})"
    )
    ax.set_xlim(-LIM, LIM)
    ax.set_ylim(-LIM, LIM)
    ax.set_aspect("equal")
    ax.tick_params(labelsize=14)
    ax.set_xlabel(r"filtered OLR anomaly, sign flipped (std)   wet $\rightarrow$", fontsize=15)
    ax.set_ylabel("its time derivative, sign flipped (std)", fontsize=15)
    handles = [
        plt.Line2D(
            [], [], color="tab:red", marker="o", ms=4, lw=1.5, label=f"ERA5, last {OBS_DAYS} days"
        ),
        plt.Line2D([], [], color="k", lw=2.5, label="ensemble mean"),
    ]
    handles += [
        plt.Line2D(
            [],
            [],
            ls="",
            marker="o",
            ms=9,
            color=c,
            alpha=0.8,
            label=f"week {k + 1}: members on {times[i]:%d %b}",
        )
        for k, (i, c) in enumerate(zip(weeks, colors, strict=True))
    ]
    handles += [
        plt.Rectangle((0, 0), 1, 1, color="#d8c9a0", label="ERA5, amplitude 1 to 90th pct"),
        plt.Rectangle((0, 0), 1, 1, color="#8c8cc4", label="ERA5, 90th to 99th pct"),
    ]
    fig.legend(handles=handles, loc="outside right center", fontsize=15, frameon=False)
    return (
        fig,
        (f"{LABELS[wave]} at {where}: {state} on {init:%d %b}\nforecast from {init:%Y-%m-%d}"),
        18,
    )


def _plot_roses(x, y, times, init, where, wave, n_members, days_per_rose, n_roses, ncols):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import PercentFormatter

    fc_days = np.flatnonzero(times >= init)
    groups = [
        fc_days[days_per_rose * k : days_per_rose * (k + 1)]
        for k in range(min(n_roses, len(fc_days) // days_per_rose))
    ]
    if not groups:
        raise UsageError("the forecast is too short for one rose")
    centres = np.radians([(180 - 45 * (p - 1)) % 360 for p in range(1, 9)])
    daily = days_per_rose == 1
    ncols = min(ncols, len(groups))
    nrows = -(-len(groups) // ncols)
    scale = 0.75 if daily else 1
    amp, ph = np.hypot(x, y), _phase_of(x, y)

    shares = []
    for days in groups:
        a, p = amp[:, days].ravel(), ph[:, days].ravel()
        shares.append(
            np.array(
                [
                    [np.mean((a > lo) & (a <= hi) & (p == k)) * 100 for k in range(1, 9)]
                    for lo, hi in pairwise(ROSE_AMP_BINS)
                ]
            )
        )
    rmax = max(max(sh.sum(0).max() for sh in shares) * 1.1, 1.0)

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(17 / 3 * ncols * scale, 6.25 * nrows * scale),
        subplot_kw={"projection": "polar"},
        layout="constrained",
        squeeze=False,
    )
    for ax in axes.flat[len(groups) :]:
        ax.remove()
    for k, (ax, days, sh) in enumerate(zip(axes.flat, groups, shares, strict=False)):
        bottom = np.zeros(8)
        for c, part in zip(ROSE_COLORS, sh, strict=True):
            ax.bar(
                centres,
                part,
                width=np.radians(40),
                bottom=bottom,
                color=c,
                edgecolor="k",
                lw=0.6,
                zorder=3,
            )
            bottom += part
        ax.text(
            0.5,
            0.5,
            f"weak\n{100 - sh.sum():.0f}%",
            ha="center",
            va="center",
            fontsize=13 * scale,
            zorder=5,
            transform=ax.transAxes,
            bbox={"boxstyle": "circle", "facecolor": "white", "edgecolor": "k"},
        )
        ax.set_ylim(0, rmax)
        ax.set_rorigin(-0.2 * rmax)
        ax.set_thetalim(0, 2 * np.pi)
        ax.set_xticks(centres)
        ax.set_xticklabels([_phase_label(p) for p in range(1, 9)], fontsize=14 * scale)
        ax.tick_params(axis="y", labelsize=11 * scale)
        ax.set_rlabel_position(112.5)
        ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
        title = (
            f"Day {k + 1}: {times[days[0]]:%d %b}"
            if daily
            else f"Week {k + 1}: {times[days[0]]:%d %b} - {times[days[-1]]:%d %b}"
        )
        ax.set_title(title, fontsize=16 * scale, pad=14 * scale)
    handles = [
        plt.Rectangle(
            (0, 0),
            1,
            1,
            facecolor=c,
            edgecolor="k",
            label=f"amplitude {lo:g}-{hi:g}" if np.isfinite(hi) else f"amplitude > {lo:g}",
        )
        for c, lo, hi in zip(ROSE_COLORS, ROSE_AMP_BINS[:-1], ROSE_AMP_BINS[1:], strict=True)
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=4, fontsize=14, frameon=False)
    per, sample = ("day", "") if daily else ("week", f" x {days_per_rose} days")
    return (
        fig,
        (
            f"{LABELS[wave]} phase at {where} per forecast {per}: share of all "
            f"{n_members} members{sample}, forecast from {init:%Y-%m-%d}"
        ),
        17,
    )


def _segments(t, x, y, **kwargs):
    """LineCollection of the amplitude of (x, y) against t, coloured by phase."""
    import numpy as np
    from matplotlib.collections import LineCollection
    from matplotlib.colors import to_rgb

    amp = np.hypot(x, y)
    points = np.stack([np.broadcast_to(t, amp.shape), amp], axis=-1)
    segments = np.stack([points[..., :-1, :], points[..., 1:, :]], axis=-2).reshape(-1, 2, 2)
    phase = _phase_of((x[..., :-1] + x[..., 1:]) / 2, (y[..., :-1] + y[..., 1:]) / 2).ravel()
    colors = np.array([to_rgb(c) for c in PHASE_COLORS])[phase - 1]
    return LineCollection(segments, colors=colors, **kwargs)


def _plot_amplitude(x, y, clim, times, init, where, wave, n_members):
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import patheffects
    from matplotlib.transforms import blended_transform_factory

    t = mdates.date2num(times)
    fc_days = np.flatnonzero(times >= init)
    show = np.flatnonzero(times >= init - np.timedelta64(OBS_DAYS, "D"))
    fan = np.r_[fc_days[0] - 1, fc_days] if fc_days[0] > 0 else fc_days
    weeks = [fc_days[d] for d in WEEK_DAYS if d < len(fc_days)]
    xm, ym = x.mean(0), y.mean(0)
    amp_m = np.hypot(xm, ym)
    top = max(LIM, float(np.hypot(x, y)[:, fan].max()) * 1.05)

    fig, ax = plt.subplots(figsize=(17, 9), layout="constrained")
    ax.axhspan(0, 1, color="0.92", zorder=0)
    ax.text(
        0.995,
        0.5,
        "weak",
        ha="right",
        va="center",
        fontsize=13,
        color="0.35",
        transform=blended_transform_factory(ax.transAxes, ax.transData),
    )
    ax.axhline(clim["p90_all"], color="0.35", lw=1, ls="--", zorder=1)
    ax.add_collection(_segments(t[fan], x[:, fan], y[:, fan], linewidths=1, alpha=0.22, zorder=2))
    ax.plot(
        times[fan], np.median(np.hypot(x, y)[:, fan], axis=0), color="k", lw=1.5, ls=":", zorder=4
    )
    ax.plot(times[show], amp_m[show], color="k", lw=6.5, solid_capstyle="round", zorder=5)
    ax.add_collection(
        _segments(t[show], xm[show], ym[show], linewidths=4, capstyle="round", zorder=6)
    )
    for i in weeks:
        ax.annotate(
            f"P{_phase_of(xm[i], ym[i])}",
            (times[i], amp_m[i]),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            fontsize=13,
            fontweight="bold",
            zorder=7,
            path_effects=[patheffects.withStroke(linewidth=3, foreground="white")],
        )
    ax.axvline(init, color="k", lw=1.5)
    ax.text(
        init,
        1.005,
        "forecast starts",
        ha="center",
        va="bottom",
        fontsize=13,
        transform=blended_transform_factory(ax.transData, ax.transAxes),
    )
    ax.set_xlim(times[show[0]], times[-1])
    ax.set_ylim(0, top)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=init.dayofweek))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.grid(axis="x", color="0.85", lw=0.8)
    ax.tick_params(labelsize=14)
    ax.set_ylabel("local wave amplitude (std)", fontsize=15)
    handles = [
        plt.Line2D([], [], color="0.3", lw=4, label="ensemble-mean wave\n(ERA5 before the line)"),
        plt.Line2D([], [], color="0.3", lw=1, alpha=0.6, label=f"the {n_members} members"),
        plt.Line2D([], [], color="k", lw=1.5, ls=":", label="median member amplitude"),
        plt.Line2D([], [], color="0.35", lw=1, ls="--", label="ERA5 90th pct for the season"),
    ]
    handles += [
        plt.Line2D([], [], color=c, lw=4, label=_phase_label(p, " "))
        for p, c in enumerate(PHASE_COLORS, start=1)
    ]
    fig.legend(handles=handles, loc="outside right center", fontsize=14, frameon=False)
    return (
        fig,
        (f"{LABELS[wave]} amplitude at {where}, coloured by phase\nforecast from {init:%Y-%m-%d}"),
        18,
    )


@weather_skill(name="plot-wave-phase", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    required=True,
    dest="ds",
    help="olr-wave-filter output Zarr.",
)
@weather_skill.argument(
    "--wave", choices=WAVE_NAMES, default="kelvin", help="Wave band (default kelvin)."
)
@weather_skill.argument(
    "--lon",
    type=float,
    default=37.5,
    help="Longitude of the point (degrees east, default 37.5 = Kenya).",
)
@weather_skill.argument(
    "--kind",
    choices=KINDS,
    default="rose",
    help="rose (weekly phase roses), rose-daily (one rose per day), phase (phase-amplitude "
    "diagram), amplitude (amplitude against time, coloured by phase). Default: rose.",
)
@weather_skill.argument(
    "--days", type=int, default=20, help="Number of daily roses for --kind rose-daily (default 20)."
)
@weather_skill.argument(
    "--label", default=None, help="Place name in the title (default: the longitude)."
)
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_wave_phase(ds, wave, lon, kind, days, label, title, output, **kwargs):
    """Render a local wave phase PNG (rose, phase diagram, amplitude) from olr-wave-filter output."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from weather_skills_core.units import dequantify_dataset

    ds = dequantify_dataset(ds)
    if "forecast_reference_time" not in ds.attrs:
        raise UsageError("input is not olr-wave-filter output (no forecast_reference_time attr)")
    needed = [f"olr_{wave}{s}" for s in ("", "_local_std_a", "_local_std_b", "_clim_amp_p90")]
    if any(v not in ds.data_vars for v in needed):
        raise UsageError(f"the input has no olr_{wave}; rerun olr-wave-filter with --waves {wave}")
    if days < 1:
        raise UsageError("--days must be at least 1")
    init = pd.Timestamp(ds.attrs["forecast_reference_time"])
    times = ds.get_index("time")
    if not (times >= init).any() or not (times < init).any():
        raise UsageError("the input needs observed days before and forecast days after the init")

    x, y, clim, used_lon = _local(ds, wave, lon)
    where = (
        f"{label} ({used_lon:g}E, {AMP_LAT}S-{AMP_LAT}N OLR)"
        if label
        else (f"{used_lon:g}E ({AMP_LAT}S-{AMP_LAT}N OLR)")
    )
    n_members = ds.sizes["number"]
    if kind == "phase":
        fig, auto, size = _plot_phase(x, y, clim, times, init, where, wave)
    elif kind == "amplitude":
        fig, auto, size = _plot_amplitude(x, y, clim, times, init, where, wave, n_members)
    elif kind == "rose":
        fig, auto, size = _plot_roses(x, y, times, init, where, wave, n_members, 7, 6, 3)
    else:
        fig, auto, size = _plot_roses(x, y, times, init, where, wave, n_members, 1, days, 5)
    fig.suptitle(title or auto, fontsize=size)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=110)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_wave_phase()
