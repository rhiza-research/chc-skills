"""Correctness tests for chc-plot-theme."""

import json
from pathlib import Path

import pytest
from conftest import load_skill, run_skill


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


def test_prints_kmsa_theme_path(mod, capsys):
    run_skill(mod.chc_plot_theme, "--theme", "kmsa")
    path = Path(capsys.readouterr().out.strip())
    assert path == mod.THEMES["kmsa"] and path.is_file()


@pytest.mark.parametrize("args", [(), ("--theme", "kmsa", "--colormap", "kmsa")])
def test_needs_exactly_one_of_theme_or_colormap(mod, capsys, args):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.chc_plot_theme, *args)
    assert exc.value.code == 2
    assert "exactly one of --theme or --colormap" in capsys.readouterr().err


def test_kmsa_theme_file(mod):
    theme = json.loads(mod.THEMES["kmsa"].read_text())
    assert set(theme) <= {"template", "palettes", "defaults", "overlays"}
    # Rainfall totals only: other field kinds keep the standard colors.
    assert theme["defaults"] == {"precip": "kmsa"}
    kmsa = theme["palettes"]["kmsa"]
    # One color and one legend label per class; > 100 mm is a labelled class.
    assert len(kmsa["colors"]) == len(kmsa["labels"]) == len(kmsa["bounds"]) - 1
    assert kmsa["labels"][0] == "< 1" and kmsa["labels"][-1] == "> 100"
    # Same classes and swatches as the kmsa colormap (whose packed list adds under/over).
    colormap = mod.COLORMAPS["kmsa"]
    assert kmsa["bounds"][:-1] == colormap["bounds"]
    assert kmsa["colors"] == colormap["colors"][1:]
