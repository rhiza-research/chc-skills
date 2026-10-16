# CHC Skills

Climate Hazards Center (CHC) skills for SubC multi-model-ensemble forecasts,
Indian Ocean Dipole (IOD) analysis, analog years, BoM IOD/ENSO observation
graphs, CPC MJO forecast diagrams, NCICS MJO / equatorial-wave maps and
Hovmöllers, and Africa ITF position figures. Built on
[`weather-skills-core`](https://github.com/rhiza-research/weather-skills-core)
(pinned to `dev`).

## Skills

| Skill | Role |
| --- | --- |
| [`subc-mme-fetch`](skills/subc-mme-fetch/) | Fetch one SubC global MME outlook (`--outlook 7d\|15d\|30d`; mean + anomaly per variable) as a forecast Zarr. `--probe-latest VAR` gives the latest init and warns when its window has already ended |
| [`iod-mode-index`](skills/iod-mode-index/) | Dipole Mode Index (West − East) plus west/east box means from a pre-computed temperature anomaly |
| [`iod-enso-fetch`](skills/iod-enso-fetch/) | Fetch BoM IOD / ENSO observation or ACCESS-S forecast PNGs, or official index values as a Zarr (`--format data` / `-o *.zarr`) |
| [`analog-years`](skills/analog-years/) | Analog years for a `--date` (stub: 2026 → 1982, 1994, 1997, 2006, 2015, 2019, 2023; other years error) |
| [`mjo-forecast-fetch`](skills/mjo-forecast-fetch/) | Fetch the latest CPC CLIVAR MJO Wheeler–Hendon phase-space PNG (GEFS, CFS, CMC, JMA, ECMWF 15-day, ECMWF 46-day extended range, BoM) |
| [`ncics-mjo-png`](skills/ncics-mjo-png/) | Fetch an NCICS MJO / equatorial-wave map or Hovmöller PNG (default live Africa 7-day OLR map; `--date` for archive snapshots; `--product hovmoller` for tropics 15S–15N) |
| [`africa-itf`](skills/africa-itf/) | Fetch the latest NOAA/CPC Africa ITF / ITCZ figure: the continental position map (`--location africa`, default) or the west/east latitude-vs-dekad time series (`west-africa`, `east-africa`, not maps; CPC uses both terms) |
| [`resolve-chc-region`](skills/resolve-chc-region/) | Bbox and boundary polygon for the Tana River basin, Kenya's KNSDI national outline (plus county lines), the Kenya OND briefing box, and East Africa clipped at 15°S, in the same shape as `resolve-region` |
| [`chc-plot-theme`](skills/chc-plot-theme/) | KMSA and classic CHC plot themes (`--theme kmsa|chc`, JSON to save for `plot --theme-file`) and CHC/KMSA rainfall colormaps (`chc_precip_<window>`, `chc_precip_anom_<window>`, `kmsa`; `--for ZARR` picks the window), printed as JSON for a trace's `meta.palette`, plus the KMSA-style Kenya map recipe |

## Quick start

```bash
uv sync --group dev
uv run pytest

# Latest published 7-day SubC init for one variable (stderr warns if its window
# has already ended), then fetch that variable
INIT=$(uv run skills/subc-mme-fetch/scripts/fetch.py --outlook 7d --probe-latest ts)
uv run skills/subc-mme-fetch/scripts/fetch.py --date "$INIT" --outlook 7d -v ts -o /tmp/subc.zarr

# IOD from a temperature-anomaly field (forecast or observations)
uv run skills/iod-mode-index/scripts/iod.py \
  -i /tmp/subc.zarr -v ts_anomaly -o /tmp/iod.zarr

# Latest BoM IOD / ENSO observation graphs, official weekly values, or ACCESS-S forecast plumes
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod -o /tmp/iod.png
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod --format data -o /tmp/iod.zarr
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod --product forecast -o /tmp/iod_fc.png
IOD_INIT=$(uv run skills/iod-enso-fetch/scripts/fetch.py --index iod --product forecast --probe-latest)
uv run skills/iod-enso-fetch/scripts/fetch.py \
  --index iod --product forecast --date "$IOD_INIT" -o /tmp/iod_fc_dated.png

# Analog years for 2026 (stub)
uv run skills/analog-years/scripts/analog_years.py --date 2026-09-01

# Latest CPC MJO phase-space diagram (GEFS, bias-corrected by default)
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model gefs -o /tmp/mjo.png

# Latest NCICS Africa 7-day OLR map, tropical OLR Hovmöller, or a dated archive snapshot
uv run skills/ncics-mjo-png/scripts/fetch.py -o /tmp/olr_africa.png
uv run skills/ncics-mjo-png/scripts/fetch.py --product hovmoller -o /tmp/olr_hov.png
uv run skills/ncics-mjo-png/scripts/fetch.py --date 2024-12-30 -o /tmp/olr_africa_20241230.png

# Latest CPC Africa ITF position map (default --location africa; the only map)
uv run skills/africa-itf/scripts/fetch.py -o /tmp/itf_africa.png

# Tana River basin bbox + polygon (for clip-region --geojson / plot masks)
uv run skills/resolve-chc-region/scripts/resolve.py "Tana River basin" --geojson /tmp/tana.json

# CHC rainfall colormap sized to a totals Zarr's aggregation_period
uv run skills/chc-plot-theme/scripts/theme.py --colormap chc_precip --for /tmp/totals.zarr
```

If anomalies are not already in the input, compute them first (a climatology
from `clim-fetch` or `summarize-dim`, then `difference`, from weather-skills), then run `iod-mode-index`. To **view**
SST or precip over the Indian Ocean without computing the index, plot those
variables over an Indian Ocean bbox and optionally overlay the west/east
dipole boxes (see `iod-mode-index` SKILL.md).

## Install as a Claude plugin

```bash
./install_agent.sh
# or:
claude plugin marketplace add rhiza-research/chc-skills
claude plugin install rhiza-chc@chc-skills
```

## Layout

Same packaging model as
[`forecasting-skills`](https://github.com/rhiza-research/forecasting-skills):
canonical skills under `skills/<name>/`, Claude plugin via `.claude-plugin/` +
`agents/`, CLI via `skills-runner`.

See [CONTRIBUTING.md](CONTRIBUTING.md).
