"""Tests for iod-enso-fetch (mocked download; no live network)."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import pytest
from conftest import load_skill, run_skill
from PIL import Image
from weather_skills_core import DataError, UsageError


def _valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _archive(mod, monkeypatch, dates):
    monkeypatch.setattr(mod, "_archive_dates", lambda: list(dates))


@pytest.fixture
def mod():
    return load_skill("iod-enso-fetch", "fetch")


@pytest.mark.parametrize(
    ("index", "product", "filename"),
    [
        ("iod", "IDCK000072", "iod1.png"),
        ("enso", "IDCK000072", "rnino_3.4.png"),
        ("relative-nino3.4", "IDCK000072", "rnino_3.4.png"),
        ("nino3.4", "IDCK000072", "nino3_4.png"),
        ("soi", "IDCKGSM000", "soi30.png"),
    ],
)
def test_image_url_mapping(mod, index, product, filename):
    assert mod._image_url(index) == f"{mod.IMG_HOST}/{product}/{filename}"


def test_enso_is_relative_nino34(mod):
    assert mod._image_url("enso") == mod._image_url("relative-nino3.4")


def test_unknown_index_raises(mod):
    with pytest.raises(UsageError, match="unknown --index"):
        mod._image_url("pdo")


def test_fetch_writes_png(mod, monkeypatch, tmp_path):
    out = tmp_path / "iod.png"
    seen = {}

    def fake_download(url, dest, *, referer=None):
        seen["url"] = url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--index", "iod", "-o", str(out))
    assert Path(out).exists()
    assert seen["url"].endswith("/IDCK000072/iod1.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_fetch_enso(mod, monkeypatch, tmp_path):
    out = tmp_path / "enso.png"
    seen = {}

    def fake_download(url, dest, *, referer=None):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--index", "enso", "-o", str(out))
    assert seen["url"].endswith("/IDCK000072/rnino_3.4.png")


def test_observation_rejects_date(mod, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch,
            "--index",
            "iod",
            "--date",
            "2024-06-08",
            "-o",
            str(tmp_path / "iod.png"),
        )
    assert exc.value.code == 2
    assert "current-only" in capsys.readouterr().err


def test_forecast_url_latest_iod(mod):
    issue = date(2026, 8, 29)
    assert mod._forecast_url("iod", issue).endswith(
        "/archive/20260829/plumes/sstOutlooks.iod.hr.png"
    )


def test_forecast_url_enso_relative_after_switch(mod):
    url = mod._forecast_url("enso", date(2026, 8, 29))
    assert url.endswith("/archive/20260829/plumes/sstOutlooks.rnino34.hr.png")


def test_forecast_url_enso_traditional_before_switch(mod):
    url = mod._forecast_url("enso", date(2025, 6, 28))
    assert url.endswith("/archive/20250628/plumes/sstOutlooks.nino34.hr.png")


def test_forecast_url_nino34_stays_traditional(mod):
    url = mod._forecast_url("nino3.4", date(2026, 8, 29))
    assert url.endswith("/archive/20260829/plumes/sstOutlooks.nino34.hr.png")


def test_forecast_relative_before_switch_errors(mod):
    with pytest.raises(UsageError, match="relative-nino3.4 starts"):
        mod._forecast_url("relative-nino3.4", date(2025, 6, 28))


def test_forecast_soi_errors(mod):
    with pytest.raises(UsageError, match="no plume"):
        mod._forecast_url("soi", date(2026, 8, 29))


def test_resolve_issue_date_latest_and_snap(mod):
    dates = [date(2024, 6, 8), date(2024, 6, 22), date(2026, 8, 29)]
    assert mod._resolve_issue_date(None, dates) == date(2026, 8, 29)
    assert mod._resolve_issue_date(date(2024, 6, 22), dates) == date(2024, 6, 22)
    assert mod._resolve_issue_date(date(2024, 6, 20), dates) == date(2024, 6, 8)
    with pytest.raises(UsageError, match="before the ACCESS-S"):
        mod._resolve_issue_date(date(2018, 1, 1), dates)


def test_parse_archive_dates(mod):
    payload = {
        "archive_index": {
            "data": {"index": [{"init_date": "2024-06-08"}, {"init_date": "2026-08-29"}]}
        }
    }
    assert mod._parse_archive_dates(payload) == [date(2024, 6, 8), date(2026, 8, 29)]


def test_fetch_forecast_latest(mod, monkeypatch, tmp_path):
    out = tmp_path / "iod_fc.png"
    seen = {}
    _archive(mod, monkeypatch, [date(2026, 8, 22), date(2026, 8, 29)])

    def fake_download(url, dest, *, referer=None):
        seen["url"] = url
        seen["referer"] = referer
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--index", "iod", "--product", "forecast", "-o", str(out))
    assert seen["url"].endswith("/archive/20260829/plumes/sstOutlooks.iod.hr.png")
    assert seen["referer"] == mod.FORECAST_PAGE


def test_fetch_forecast_historical(mod, monkeypatch, tmp_path):
    out = tmp_path / "enso_fc.png"
    seen = {}
    _archive(mod, monkeypatch, [date(2025, 6, 28), date(2026, 8, 29)])

    def fake_download(url, dest, *, referer=None):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(
        mod.fetch,
        "--index",
        "enso",
        "--product",
        "forecast",
        "--date",
        "2025-06-28",
        "-o",
        str(out),
    )
    assert seen["url"].endswith("/archive/20250628/plumes/sstOutlooks.nino34.hr.png")


def test_probe_latest_forecast(mod, monkeypatch, capsys):
    _archive(mod, monkeypatch, [date(2026, 8, 22), date(2026, 8, 29)])
    run_skill(mod.fetch, "--index", "iod", "--product", "forecast", "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-08-29"


def test_probe_latest_observation(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_observation_mtime", lambda url: date(2026, 8, 31))
    run_skill(mod.fetch, "--index", "iod", "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-08-31"


def test_request_url_appends_cache_buster(mod, monkeypatch):
    monkeypatch.setattr(mod.time, "time", lambda: 1788374783)
    url = mod._image_url("iod")
    assert mod._request_url(url) == f"{url}?1788374783"


def test_download_sends_browser_headers(mod, monkeypatch, tmp_path):
    out = tmp_path / "iod.png"
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["ua"] = req.headers.get("User-agent")
        seen["referer"] = req.headers.get("Referer")

        class Resp:
            def __init__(self):
                self.headers = {"Content-Type": "image/png"}

            def read(self):
                return _valid_png_bytes()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "time", lambda: 1788374783)
    mod._download(mod._image_url("iod"), out)
    assert seen["url"].endswith("/iod1.png?1788374783")
    assert "Mozilla" in seen["ua"]
    assert seen["referer"] == mod.OBS_PAGE
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_download_http_error(mod, monkeypatch, tmp_path):
    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(DataError, match="HTTP 403"):
        mod._download(mod._image_url("iod"), tmp_path / "out.png")


def test_all_page_indices_registered(mod):
    assert set(mod.INDEX_CHOICES) == {
        "iod",
        "enso",
        "nino3.4",
        "relative-nino3.4",
        "soi",
    }
