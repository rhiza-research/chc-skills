# CHC Skills

Climate Hazards Center (CHC) skills for SubC multi-model-ensemble forecasts,
Indian Ocean Dipole (IOD) analysis, analog years, BoM IOD/ENSO observation
graphs, CPC MJO forecast diagrams, NCICS MJO / equatorial-wave maps and
Hovmöllers, and Africa ITF position figures. Built on
[`weather-skills-core`](https://github.com/rhiza-research/weather-skills-core)
(pinned to `main`).

## Skills

| Skill | Role |
| --- | --- |
| [`subc-mme-fetch`](skills/subc-mme-fetch/) | Fetch and stitch SubC global MME mean + anomaly NetCDFs (7/15/30-day leads) into one forecast Zarr |
| [`iod-mode-index`](skills/iod-mode-index/) | Dipole Mode Index (West − East) plus west/east box means from a pre-computed temperature anomaly |
| [`iod-enso-fetch`](skills/iod-enso-fetch/) | Fetch BoM IOD / ENSO observation or ACCESS-S forecast PNGs (`--index iod\|enso\|nino3.4\|relative-nino3.4\|soi`, `--product observation\|forecast`, `--date` for archive issues) |
| [`analog-years`](skills/analog-years/) | Analog years for a `--date` (stub: 2026 → 1982, 1997, 2006, 2015, 2019, 2023; other years error) |
| [`mjo-forecast-fetch`](skills/mjo-forecast-fetch/) | Fetch the latest CPC CLIVAR MJO Wheeler–Hendon phase-space PNG (GEFS / ECMWF / ECMWF extended-range) |
| [`ncics-mjo-png`](skills/ncics-mjo-png/) | Fetch an NCICS MJO / equatorial-wave map or Hovmöller PNG (default live Africa 7-day OLR map; `--date` for archive snapshots; `--product hovmoller` for tropics 15S–15N) |
| [`africa-itf`](skills/africa-itf/) | Fetch the latest NOAA/CPC Africa ITF / ITCZ position PNG for one region (`--location africa\|west-africa\|east-africa`; CPC uses both terms) |

## Quick start

```bash
uv sync --group dev
uv run pytest

# Latest published 7-day SubC init, then fetch it
INIT=$(uv run skills/subc-mme-fetch/scripts/fetch.py --outlook 7d --probe-latest ts)
uv run skills/subc-mme-fetch/scripts/fetch.py --date "$INIT" --outlook 7d -o /tmp/subc.zarr

# IOD from a temperature-anomaly field (forecast or observations)
uv run skills/iod-mode-index/scripts/iod.py \
  -i /tmp/subc.zarr -v ts_anomaly -o /tmp/iod.zarr

# Latest BoM IOD / ENSO observation graphs, or ACCESS-S forecast plumes
uv run skills/iod-enso-fetch/scripts/fetch.py --index iod -o /tmp/iod.png
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

# Latest CPC Africa ITF position map (default --location africa)
uv run skills/africa-itf/scripts/fetch.py -o /tmp/itf_africa.png
```

If anomalies are not already in the input, compute them first (climatology +
`difference` from forecasting-skills), then run `iod-mode-index`. To **view**
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
