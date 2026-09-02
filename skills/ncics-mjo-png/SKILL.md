---
name: ncics-mjo-png
description: Fetch an NCICS tropical-monitoring MJO / equatorial-wave PNG — latitude-longitude maps or longitude-time Hovmöller diagrams — for OLR, wind, velocity potential, streamfunction, shear, or precipitable water. Defaults to the live Africa 7-day OLR CFS map; Hovmöllers default to the tropics (15S–15N). Pass --date YYYY-MM-DD for a historical snapshot from https://ncics.org/pub/mjo/archive/ (typically weekly, v2 PNGs from mid-2017). Use when a task needs the published figure from https://ncics.org/mjo; does not produce gridded data. Distinct from mjo-forecast-fetch (CPC Wheeler–Hendon phase space).
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from ncics.org; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  version: "0.0.1"
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: Omit --date for the live overwritten image (typically daily). --date reads /pub/mjo/archive/YYYY/YYYY-MM-DD/v2/ (typically weekly snapshots; v2 PNGs from mid-2017).
---

# ncics-mjo-png

Downloads an NCICS tropical-monitoring map or Hovmöller PNG and writes it to
`--output`. There is no Zarr input — this skill only produces a figure.

Source page (Carl Schreck / NCICS):

https://ncics.org/mjo

**Live** (omit `--date`) — NCICS updates the same filenames in place:

```
https://ncics.org/pub/mjo/v2/map/{var}.{algorithm}.{wave}.{region}.{days}.png
https://ncics.org/pub/mjo/v2/hov/{var}.{algorithm}.{latitude}.png
```

**Archive** (`--date YYYY-MM-DD`) — dated snapshots, typically weekly:

```
https://ncics.org/pub/mjo/archive/{YYYY}/{YYYY-MM-DD}/v2/map/{file}.png
https://ncics.org/pub/mjo/archive/{YYYY}/{YYYY-MM-DD}/v2/hov/{file}.png
```

If that calendar day is not a published snapshot, the skill uses the latest
archive date **on or before** `--date` and reports it on stderr. v2 PNGs start
around mid-2017; earlier years are HTML pages only. `--probe-latest` prints
the newest snapshot date.

Default live map (Africa, 7-day OLR, CFS, all waves):

https://ncics.org/pub/mjo/v2/map/olr.cfs.all.africa.7.png

## Products

| `--product` | What you get | Defaults that apply |
| --- | --- | --- |
| `map` (default) | Multi-panel lat/lon maps (observed + CFS forecast) | `--region africa`, `--days 7`, `--wave all` |
| `hovmoller` | Longitude-time Hovmöller (aliases: `hov`, `hovmuller`) | `--latitude wide` (tropics, 15S–15N) |

`--algorithm sst` (CFS with SSTs: SST anomaly shading, variable contours) is
Hovmöller-only.

## Variables

`--variable` / `-v` is the NCICS form family. Pressure-level fields take
`--level 850` or `--level 200` (default **200**). Complete filename tokens
(`uwnd850`, `chi200`, …) skip `--level`.

| `--variable` | NCICS label | Uses `--level` |
| --- | --- | --- |
| `olr` (default) | Outgoing Longwave Radiation | no |
| `uwnd` | Zonal wind | yes |
| `vwnd` | Meridional wind | yes |
| `chi` | Velocity potential | yes |
| `psi` | Streamfunction | yes |
| `ushear` | Zonal shear | no |
| `shear` | Shear magnitude | no |
| `pwat` | Precipitable water | no |

## Algorithms, waves, regions, latitude bands

| Flag | Ids | Default |
| --- | --- | --- |
| `--algorithm` | `cfs` (CFS forecasts), `orig` (Wheeler–Weickmann), `notc` (TCs removed), `sst` (Hovmöller-only) | `cfs` |
| `--wave` (maps) | `all`, `tc`, `sum`, `low`, `mjo`, `er`, `kelvin`, `mtd` (MRG/TD) | `all` |
| `--region` (maps) | `africa`, `global`, `west` (Western Hemisphere), `east` (Eastern Hemisphere), `pacific`, `atlantic` (South Atlantic), `indonesia`, `asia` | `africa` |
| `--latitude` (Hovmöllers) | `wide` / `tropics` (15S–15N), `eqtr` (5S–5N), `north` (5N–15N), `north2` (10N–20N), `south` (15S–5S) | `wide` |
| `--days` (maps) | `1`, `2`, `3`, `5`, `7`, `10` | `7` |

## When to use

- Need the published NCICS MJO / equatorial-wave **map** or **Hovmöller**.
- Prefer the official filtered-OLR (or wind / χ / ψ) figure rather than
  recomputing Wheeler–Weickmann filters.

## When not to use

- You need a Wheeler–Hendon RMM phase-space diagram — use
  `mjo-forecast-fetch` (CPC CLIVAR).
- You need analyzable gridded OLR / u850 / u200 — this skill only fetches
  PNGs.
- You need a pre-mid-2017 figure — v2 PNG snapshots start around July 2017.
  Older `/pub/mjo/archive/` folders are HTML pages, not this filename tree.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    [--date YYYY-MM-DD] \
    [--product map|hovmoller] \
    [--variable olr|uwnd|vwnd|chi|psi|ushear|shear|pwat] \
    [--level 850|200] \
    [--algorithm cfs|orig|notc|sst] \
    [--wave all|tc|sum|low|mjo|er|kelvin|mtd] \
    [--region africa|global|west|east|pacific|atlantic|indonesia|asia] \
    [--latitude wide|eqtr|north|north2|south] \
    [--days 1|2|3|5|7|10] \
    -o <out.png>
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments

- `--date` — optional archive snapshot `YYYY-MM-DD`. Default: live overwritten
  image. Calendar day: `resolve-time`. Nearest published snapshot on or before
  this date is used when that day is not archived. Latest snapshot:
  `--probe-latest`.
- `--probe-latest` — print the latest archive `YYYY-MM-DD` on stdout and exit.
  No `-o`.
- `--product` — `map` (default) or `hovmoller`.
- `--variable` / `-v` — field family or complete token (default `olr`).
- `--level` — 850 or 200 hPa for uwnd/vwnd/chi/psi (default 200).
- `--algorithm` — `cfs` (default), `orig`, `notc`, or `sst`.
- `--wave` — map wave overlay (default `all`). Ignored for Hovmöllers.
- `--region` — map region (default `africa`). Ignored for Hovmöllers.
- `--latitude` — Hovmöller band (default `wide` = tropics). Ignored for maps.
- `--days` — map averaging window (default 7). Ignored for Hovmöllers.
- `--output`, `-o` — PNG output path.

### Output

A PNG at `--output`. The decorator stamps `weather_skills_history` into the PNG
metadata.

## Examples

```bash
# Default: latest Africa 7-day OLR CFS map
uv run skills/ncics-mjo-png/scripts/fetch.py -o /tmp/olr_africa.png

# Tropical OLR Hovmöller (15S–15N) with CFS forecasts
uv run skills/ncics-mjo-png/scripts/fetch.py \
  --product hovmoller -o /tmp/olr_hov.png

# Equatorial (5S–5N) 850-hPa zonal-wind Hovmöller
uv run skills/ncics-mjo-png/scripts/fetch.py \
  --product hov --variable uwnd --level 850 --latitude eqtr \
  -o /tmp/u850_hov.png

# Global 10-day 200-hPa velocity-potential map, MJO overlay
uv run skills/ncics-mjo-png/scripts/fetch.py \
  --variable chi --region global --days 10 --wave mjo \
  -o /tmp/chi200_global.png

# Historical Africa map (exact snapshot if published; else nearest on or before)
uv run skills/ncics-mjo-png/scripts/fetch.py \
  --date 2024-12-30 -o /tmp/olr_africa_20241230.png

# Latest published archive date
uv run skills/ncics-mjo-png/scripts/fetch.py --probe-latest
```
