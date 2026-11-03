"""Correctness tests for chc-plot-theme."""

import json
import re

import pytest
from conftest import load_skill, make_gridded, run_skill, write_zarr


@pytest.fixture(scope="module")
def mod():
    return load_skill("chc-plot-theme", "theme")


def test_prints_colormap_json(mod, capsys):
    run_skill(mod.chc_plot_theme, "--colormap", "kmsa")
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one line, ready to paste into a spec
    assert json.loads(out) == mod.PALETTES["kmsa"]


def test_rejects_unknown_colormap(mod):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, "--colormap", "nope")
    assert exc.value.code != 0


def test_palettes_shape(mod):
    for window in ("daily", "week", "month", "season"):
        assert f"chc_precip_{window}" in mod.PALETTES
        assert f"chc_precip_anom_{window}" in mod.PALETTES
    for name, p in mod.PALETTES.items():
        assert set(p) <= {"colors", "bounds", "labels"}, name
        n_classes = len(p["bounds"]) - 1
        # One color per class, or packed as under + classes + over.
        assert len(p["colors"]) in (n_classes, n_classes + 2), name
        assert p["bounds"] == sorted(p["bounds"]), name


def test_kmsa_palette(mod):
    kmsa = mod.PALETTES["kmsa"]
    # One color and one legend label per class; > 100 mm is a labelled class.
    assert kmsa["bounds"][:-1] == [0, 1, 10, 20, 50, 70, 100]
    assert len(kmsa["colors"]) == len(kmsa["labels"]) == len(kmsa["bounds"]) - 1
    assert kmsa["labels"][0] == "< 1" and kmsa["labels"][-1] == "> 100"
    assert kmsa["colors"][-1] == "#ff5500"


def test_classic_chc_palettes(mod):
    assert mod.PALETTES["chc_ppt_total"]["bounds"][-1] == 2500
    assert mod.PALETTES["chc_precip_anom_season"]["bounds"][0] == -500
    assert {"chc_ppt_poa", "chc_spi"} <= set(mod.PALETTES)


def test_prints_libsonnet(mod, capsys):
    run_skill(mod.chc_plot_theme)
    out = capsys.readouterr().out
    assert "import 'chc_palettes.libsonnet'" in out
    # Jsonnet with comments stripped and the trailing comma dropped is the palettes as JSON.
    body = "\n".join(line for line in out.splitlines() if not line.startswith("//"))
    body = re.sub(r"\b(\w+):", r'"\1":', body).replace(",\n}", "\n}")
    assert json.loads(body) == mod.PALETTES


def test_writes_output_file(mod, capsys, tmp_path):
    path = tmp_path / "chc_palettes.libsonnet"
    run_skill(mod.chc_plot_theme, "-o", str(path))
    assert capsys.readouterr().out == ""
    run_skill(mod.chc_plot_theme)
    assert path.read_text() == capsys.readouterr().out


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
    captured = capsys.readouterr()
    assert json.loads(captured.out) == mod.PALETTES[expected]
    assert expected in captured.err


def test_for_reads_aggregation_period(mod, capsys, tmp_path):
    ds = make_gridded(n_time=1)
    ds["precip"].attrs.update(units="mm", aggregation_period="1 dekad")
    path = write_zarr(ds, tmp_path / "dekad.zarr")
    run_skill(mod.chc_plot_theme, "--colormap", "chc_precip_anom", "--for", str(path))
    captured = capsys.readouterr()
    assert json.loads(captured.out) == mod.PALETTES["chc_precip_anom_week"]
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


@pytest.mark.parametrize(
    "args",
    [("--colormap", "kmsa", "--window-days", "7"), ("--window-days", "7")],
)
def test_window_days_needs_family(mod, args):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, *args)
    assert exc.value.code != 0
