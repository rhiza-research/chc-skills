"""Correctness tests for resolve-chc-region."""

import json

import pytest
from conftest import load_skill, run_skill
from weather_skills_core import UsageError

_TANA_BBOX = (0.4879, 36.5667, -3.0297, 40.5667)


@pytest.fixture(scope="module")
def mod():
    return load_skill("resolve-chc-region", "resolve")


@pytest.fixture
def resolve(mod):
    return mod.resolve_chc_region


def test_tana_basin_prints_bbox(capsys, resolve):
    run_skill(resolve, "Tana River basin")
    out = capsys.readouterr().out.strip()
    n, w, s, e = (float(v) for v in out.split("/"))
    assert (n, w, s, e) == pytest.approx(_TANA_BBOX, abs=1e-4)


def test_tana_basin_geojson_is_polygon(tmp_path, capsys, resolve):
    path = tmp_path / "tana.json"
    run_skill(resolve, "Tana River basin", "--geojson", str(path))
    captured = capsys.readouterr()
    assert "Wrote boundary polygon" in captured.err
    fc = json.loads(path.read_text())
    assert fc["type"] == "FeatureCollection"
    (feature,) = fc["features"]
    props = feature["properties"]
    assert props["name"] == "Tana River basin"
    assert props["region_name"] == "tana_river_basin"
    assert props["level"] == "basin"
    assert props["iso3"] == "KEN"
    assert props["country"] == "Kenya"
    assert "HydroBASINS" in props["source"]
    assert props["bbox"] == pytest.approx(list(_TANA_BBOX), abs=1e-4)
    # A real outline, not a four-corner rectangle.
    assert feature["geometry"]["type"] == "Polygon"
    assert len(feature["geometry"]["coordinates"][0]) > 5


@pytest.mark.parametrize("alias", ["Tana basin", "Tana River catchment", "tana catchment"])
def test_aliases(mod, alias):
    assert mod.lookup(alias)["properties"]["name"] == "Tana River basin"


def test_bare_tana_river_is_ambiguous(mod):
    """Bare "Tana River" is also Tana River County, so it must not pick the basin."""
    with pytest.raises(UsageError, match="kenya-tana_river"):
        mod.lookup("Tana River")


def test_unknown_region_points_to_resolve_region(resolve, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(resolve, "Nile basin")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Tana River basin" in err
    assert "resolve-region" in err
