"""Correctness tests for chc-plot-theme."""

import json

import pytest
from conftest import load_skill, make_gridded, run_skill, write_zarr


@pytest.fixture(scope="module")
def mod():
    return load_skill("chc-plot-theme", "theme")


def test_prints_colormap_json(mod, capsys):
    run_skill(mod.chc_plot_theme, "--colormap", "kmsa")
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one line, ready to embed in --spec
    assert json.loads(out) == {"name": "kmsa", **mod.COLORMAPS["kmsa"]}


def test_rejects_unknown_colormap(mod):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, "--colormap", "nope")
    assert exc.value.code != 0


def test_theme_colormaps(mod):
    colormaps = json.loads(mod.THEME.read_text())["colormaps"]
    for window in ("daily", "week", "month", "season"):
        assert f"chc_precip_{window}" in colormaps
        assert f"chc_precip_anom_{window}" in colormaps
    kmsa = colormaps["kmsa"]
    assert kmsa["bounds"] == [0, 1, 10, 20, 50, 70, 100]
    # Packed under + one color per class + over.
    assert len(kmsa["colors"]) == len(kmsa["bounds"]) + 1
    assert kmsa["colors"][-1] == "#ff5500"
    for entry in colormaps.values():
        assert len(entry["colors"]) == len(entry["bounds"]) + 1


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (1, "chc_precip_daily"),
        (7, "chc_precip_week"),
        (10, "chc_precip_week"),
        (15, "chc_precip_month"),
        (30.4375, "chc_precip_month"),
        (92, "chc_precip_season"),
    ],
)
def test_window_days_picks_window(mod, capsys, days, expected):
    # A 10-day dekad stays on the week window, not the 0-400 mm month window.
    run_skill(mod.chc_plot_theme, "--colormap", "chc_precip", "--window-days", str(days))
    assert json.loads(capsys.readouterr().out)["name"] == expected


def test_for_reads_aggregation_period(mod, capsys, tmp_path):
    ds = make_gridded(n_time=1)
    ds["precip"].attrs.update(units="mm", aggregation_period="1 dekad")
    path = write_zarr(ds, tmp_path / "dekad.zarr")
    run_skill(mod.chc_plot_theme, "--colormap", "chc_precip_anom", "--for", str(path))
    captured = capsys.readouterr()
    assert json.loads(captured.out)["name"] == "chc_precip_anom_week"
    assert "chc_precip_anom_week" in captured.err


def test_for_without_aggregation_period_errors(mod, capsys, tmp_path):
    path = write_zarr(make_gridded(n_time=1), tmp_path / "plain.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, "--colormap", "chc_precip", "--for", str(path))
    assert exc.value.code != 0
    assert "--window-days" in capsys.readouterr().err


def test_family_needs_window(mod, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, "--colormap", "chc_precip")
    assert exc.value.code != 0


def test_window_days_rejected_for_fixed_colormap(mod):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, "--colormap", "kmsa", "--window-days", "7")
    assert exc.value.code != 0
