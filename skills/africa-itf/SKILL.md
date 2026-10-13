---
name: africa-itf
description: Fetch the latest NOAA/CPC Africa Intertropical Front (ITF) / Intertropical Convergence Zone (ITCZ) figure as a PNG (CPC uses both terms; https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.shtml). Only the default `--location africa` is a map (continental ITF position, current vs mean). `west-africa` and `east-africa` are latitude-vs-dekad time series averaged over a fixed longitude band — east is 20E–35E, which excludes Kenya — not regional maps. Latest dekad only (printed on stderr); does not produce gridded data.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.cpc.ncep.noaa.gov; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  version: "0.0.3"
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: CPC overwrites fixed image URLs with the latest dekadal ITF analysis (typically Apr–Oct, a few days after each dekad ends)
---

# africa-itf

Downloads the **latest** NOAA Climate Prediction Center (CPC) Africa
Intertropical Front (ITF) position figure and writes it as a PNG to
`--output`. CPC labels the same product **ITCZ** (Intertropical Convergence
Zone) on its source page — users may ask for either term. There is no Zarr
input — this skill only produces a figure.

Source page (ITF / ITCZ):

https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.shtml

**Only `africa` is a map.** `west-africa` and `east-africa` are line charts of
the ITF's mean latitude against dekad since April, averaged over a fixed
longitude band (CPC: "Mean Eastern Portion of the ITF: Averaged 20E to 35E").
The eastern band lies west of Kenya (34–42E), so for a Kenya or Horn of Africa
briefing use the continental `africa` map.

Image URLs are **static** (CPC updates the same filenames in place each
dekadal analysis). Upstream formats are JPEG (continental map) or PNG bytes
under a `.gif` suffix (regional time series); this skill always writes PNG.

## Location → URL mapping

| `--location` | CPC page label | Source URL |
| --- | --- | --- |
| `africa` (default) | Mean vs Current (continental ITF position map) | `https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.jpg` |
| `west-africa` | West Region (western ITF latitudinal time series) | `https://www.cpc.ncep.noaa.gov/products/international/itf/west.gif` |
| `east-africa` | East Region (eastern ITF latitudinal time series) | `https://www.cpc.ncep.noaa.gov/products/international/itf/east.gif` |

## When to use

- User asks for the Africa **ITF** or **ITCZ** position, map, or latitude
  time series (CPC treats them as the same product).
- Need the published dekadal ITF/ITCZ position map (`africa`) or the west/east
  latitude time series.
- Prefer the official CPC figure rather than reconstructing ITF/ITCZ positions.

## When not to use

- You need analyzable ITF latitude coordinates — use the CPC FEWS ITF text
  archive (`ftp.cpc.ncep.noaa.gov/fews/itf/`), not this figure skill.
- You need a historical dekad other than the latest overwritten image.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    [--location africa|west-africa|east-africa] \
    -o <out.png>
```

### Arguments

- `--location` — `africa` (default, the only map), `west-africa` or
  `east-africa` (latitude-vs-dekad time series; east = 20E–35E band).
- `--output`, `-o` — PNG output path.

### Output

A PNG at `--output`. The decorator stamps `weather_skills_history` into the PNG
metadata. stderr names the analysed dekad read from the CPC page text (e.g.
`Analysed dekad: September 21-30`); the images themselves carry no
machine-readable date.

## Examples

```bash
# Continental Africa ITF map (default)
uv run skills/africa-itf/scripts/fetch.py -o /tmp/itf_africa.png

# West Africa regional time series
uv run skills/africa-itf/scripts/fetch.py --location west-africa -o /tmp/itf_west.png

# East Africa regional time series
uv run skills/africa-itf/scripts/fetch.py --location east-africa -o /tmp/itf_east.png
```
