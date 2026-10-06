"""Correctness tests for chc-plot-theme."""

import json

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
