# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@main",
# ]
# ///
"""Fetch BoM IOD / ENSO observation or ACCESS-S forecast index PNGs."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

IMG_HOST = "https://www.bom.gov.au/clim_data"
OBS_PAGE = "https://www.bom.gov.au/climate/enso/"
FORECAST_PAGE = "https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs"
FORECAST_ARCHIVE = "https://www.bom.gov.au/climate/ocean/outlooks/archive"
ARCHIVE_INDEX_URL = f"{FORECAST_ARCHIVE}/archive_index.json"
_HTTP_TIMEOUT = 60

# BoM switches Niño outlook plumes to Relative Niño filenames on this issue date.
RNINO_START = date(2025, 7, 1)

# BoM Akamai returns 403 for library-style User-Agents (e.g. "chc-skills/…").
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Observation graphs overwritten in place (product IDCK000072 / IDCKGSM000).
INDEX_FILES: dict[str, tuple[str, str]] = {
    "iod": ("IDCK000072", "iod1.png"),
    "enso": ("IDCK000072", "rnino_3.4.png"),
    "nino3.4": ("IDCK000072", "nino3_4.png"),
    "relative-nino3.4": ("IDCK000072", "rnino_3.4.png"),
    "soi": ("IDCKGSM000", "soi30.png"),
}

# ACCESS-S plume region token (prefix ``r`` added for operational Niño).
FORECAST_REGIONS: dict[str, str] = {
    "iod": "iod",
    "enso": "nino34",
    "nino3.4": "nino34",
    "relative-nino3.4": "nino34",
}

INDEX_CHOICES = tuple(INDEX_FILES)
PRODUCT_CHOICES = ("observation", "forecast")


def _browser_headers(referer: str, accept: str) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Referer": referer,
        "Accept": accept,
    }


def _open(url: str, *, referer: str, accept: str):
    req = urllib.request.Request(url, headers=_browser_headers(referer, accept))
    try:
        return urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise DataError(f"download failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"download failed for {url!r}: {exc.reason}") from None


def _image_url(index: str) -> str:
    if index not in INDEX_FILES:
        raise UsageError(f"unknown --index {index!r}; choose one of: {', '.join(INDEX_CHOICES)}")
    product, filename = INDEX_FILES[index]
    return f"{IMG_HOST}/{product}/{filename}"


def _forecast_prefix(index: str, issue: date) -> str:
    if index == "relative-nino3.4":
        if issue < RNINO_START:
            raise UsageError(
                f"--index relative-nino3.4 starts {RNINO_START.isoformat()} "
                f"(BoM Relative Niño switch); pass --index nino3.4 or a later --date."
            )
        return "r"
    if index in {"enso"} and issue >= RNINO_START:
        return "r"
    return ""


def _forecast_url(index: str, issue: date) -> str:
    if index not in FORECAST_REGIONS:
        raise UsageError(
            f"--product forecast has no plume for --index {index!r}; "
            f"choose iod, enso, nino3.4, or relative-nino3.4."
        )
    region = FORECAST_REGIONS[index]
    prefix = _forecast_prefix(index, issue)
    ymd = issue.strftime("%Y%m%d")
    return f"{FORECAST_ARCHIVE}/{ymd}/plumes/sstOutlooks.{prefix}{region}.hr.png"


def _request_url(url: str) -> str:
    """Append a cache-buster query, matching the BoM pages."""
    return f"{url}?{int(time.time())}"


def _parse_archive_dates(payload: dict) -> list[date]:
    try:
        rows = payload["archive_index"]["data"]["index"]
    except (KeyError, TypeError) as exc:
        raise DataError(
            "BoM outlook archive_index.json is missing archive_index.data.index"
        ) from exc
    dates: list[date] = []
    for row in rows:
        raw = row.get("init_date") if isinstance(row, dict) else None
        if not raw:
            continue
        dates.append(date.fromisoformat(str(raw)))
    if not dates:
        raise DataError("BoM outlook archive_index.json listed no init dates.")
    return sorted(dates)


def _archive_dates() -> list[date]:
    req_url = _request_url(ARCHIVE_INDEX_URL)
    with _open(req_url, referer=FORECAST_PAGE, accept="application/json,text/plain,*/*") as resp:
        payload = json.load(resp)
    return _parse_archive_dates(payload)


def _resolve_issue_date(when: date | None, dates: list[date]) -> date:
    if not dates:
        raise DataError("BoM outlook archive listed no ACCESS-S issue dates.")
    if when is None:
        return dates[-1]
    candidates = [d for d in dates if d <= when]
    if not candidates:
        raise UsageError(
            f"--date {when.isoformat()} is before the ACCESS-S outlook archive "
            f"(first issue {dates[0].isoformat()})."
        )
    return candidates[-1]


def _observation_mtime(url: str) -> date | None:
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers=_browser_headers(OBS_PAGE, "image/png,*/*"),
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.headers.get("Last-Modified")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def _download(url: str, dest: Path, *, referer: str = OBS_PAGE) -> None:
    req_url = _request_url(url)
    with _open(req_url, referer=referer, accept="image/png,image/*;q=0.8,*/*;q=0.5") as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")

    if not data:
        raise DataError(f"download of {url!r} returned an empty body.")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DataError(
            f"downloaded {url!r} is not a PNG (content-type={content_type!r}, size={len(data)})."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


@weather_skill(name="iod-enso-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--index",
    required=True,
    choices=list(INDEX_CHOICES),
    help=(
        "Index graph: iod (weekly DMI), enso / relative-nino3.4 "
        "(operational Relative Niño3.4), nino3.4 (traditional), or soi "
        "(30-day SOI; observation only)."
    ),
)
@weather_skill.argument(
    "--product",
    default="observation",
    choices=list(PRODUCT_CHOICES),
    help=(
        "observation (default): current monitoring timeseries. "
        "forecast: ACCESS-S plume from the outlooks archive."
    ),
)
@weather_skill.argument(
    "--date",
    help=(
        "Forecast issue date YYYY-MM-DD. Snaps to the latest ACCESS-S archive "
        "issue on or before this day. Omit for the latest issue. Observation "
        "graphs have no dated archive."
    ),
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    default=None,
    probe=True,
    help=(
        "Print the latest available YYYY-MM-DD on stdout and exit. "
        "Forecast: latest ACCESS-S issue. Observation: Last-Modified of the "
        "current graph, or none."
    ),
)
def fetch(index, product, date, output, **kwargs):
    """Fetch a BoM IOD or ENSO observation or ACCESS-S forecast PNG.

    Observation graphs are the current overwritten clim_data timeseries
    (https://www.bom.gov.au/climate/enso/). Forecasts are ACCESS-S plumes
    from https://www.bom.gov.au/climate/ocean/outlooks/ (dated archive;
    omit ``--date`` for the latest issue).
    """
    if product not in PRODUCT_CHOICES:
        raise UsageError(
            f"unknown --product {product!r}; choose one of: {', '.join(PRODUCT_CHOICES)}"
        )

    if kwargs.get("probe_latest") is not None:
        if product == "forecast":
            latest = _resolve_issue_date(None, _archive_dates())
            print(latest.isoformat())
        else:
            latest = _observation_mtime(_image_url(index))
            print(latest.isoformat() if latest is not None else "none")
        return

    if product == "observation":
        if date is not None:
            raise UsageError(
                "Observation graphs are current-only (the PNG is a historical "
                "timeseries updated in place). For a dated ACCESS-S forecast "
                "plume pass --product forecast --date YYYY-MM-DD."
            )
        url = _image_url(index)
        referer = OBS_PAGE
        print(f"Fetching {product} {index}: {url}", file=sys.stderr)
    else:
        issue = _resolve_issue_date(date, _archive_dates())
        if date is not None and issue != date:
            print(
                f"Note: no ACCESS-S issue on {date.isoformat()}; using {issue.isoformat()}.",
                file=sys.stderr,
            )
        url = _forecast_url(index, issue)
        referer = FORECAST_PAGE
        print(f"Fetching {product} {index} issue {issue.isoformat()}: {url}", file=sys.stderr)

    output = Path(output)
    _download(url, output, referer=referer)
    return output


if __name__ == "__main__":
    fetch()
