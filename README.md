# CHC Skills

Climate Hazards Center (CHC) skills for SubC multi-model-ensemble forecasts,
Indian Ocean Dipole (IOD) analysis, analog years, BoM IOD/ENSO observation
graphs, CPC MJO forecast diagrams, NCICS MJO / equatorial-wave maps and
Hovmöllers, OLR equatorial-wave filtering and the RMM MJO index computed from
a forecast (with Hovmöller, phase-rose and phase-diagram plots), Africa ITF
position figures, the DWD ICON-EPS ensemble regridded from its triangular grid, and
arithmetic transforms (EFI/SOT, totals over any window, quantiles, add/subtract/multiply/divide). Built on
[`weather-skills-core`](https://github.com/weather-skills/weather-skills-core)
(`>=0.1.1` from PyPI).

## Skills

| Skill | Role |
| --- | --- |
| [`subc-mme-fetch`](skills/subc-mme-fetch/) | Fetch one SubC global MME outlook (`--outlook 7d\|15d\|30d`; mean + anomaly per variable) as a forecast Zarr. `--probe-latest VAR` gives the latest init and warns when its window has already ended |
| [`kmsa-wrf-fetch`](skills/kmsa-wrf-fetch/) | Read one KMSA WRF run (`--product 7day` or `daily`; 3-hourly, ~0.036° Kenya grid) as a forecast Zarr of `tp` rates and `t2m`, from the private `gs://sheerwater-datalake/kmsa-wrf` mirror. **Private use only: requires `GCLOUD_SERVICE_CREDENTIALS` with read access to that bucket.** |
| [`iod-mode-index`](skills/iod-mode-index/) | Dipole Mode Index (West − East) plus west/east box means from a pre-computed temperature anomaly |
| [`iod-enso-fetch`](skills/iod-enso-fetch/) | Fetch BoM IOD / ENSO observation or ACCESS-S forecast PNGs, or official index values as a Zarr (`--format data` / `-o *.zarr`) |
| [`analog-years`](skills/analog-years/) | Analog years for a `--date` (stub: 2026 → 1982, 1994, 1997, 2006, 2015, 2019, 2023; other years error) |
| [`mjo-forecast-fetch`](skills/mjo-forecast-fetch/) | Fetch the latest CPC CLIVAR MJO Wheeler–Hendon phase-space PNG (GEFS, CFS, CMC, JMA, ECMWF 15-day, ECMWF 46-day extended range, BoM) |
| [`ncics-mjo-png`](skills/ncics-mjo-png/) | Fetch an NCICS MJO / equatorial-wave map or Hovmöller PNG (default live Africa 7-day OLR map; `--date` for archive snapshots; `--product hovmoller` for tropics 15S–15N) |
| [`olr-wave-filter`](skills/olr-wave-filter/) | Filter an ensemble OLR forecast (dynamical-fetch, every longitude) into MJO, Kelvin, ER, MRG/TD and low-frequency waves for every member, with a 90-day ERA5 lead-in from the public `africa-forecasting-data` bucket (Janiga et al. 2018; optional reforecast bias correction) |
| [`plot-wave-hovmoller`](skills/plot-wave-hovmoller/) | Time-longitude Hovmöller of `olr-wave-filter` output: NCICS-monitor look, wave-amplitude contours, multi-wave contours, or ensemble agreement on wave activity |
| [`plot-wave-phase`](skills/plot-wave-phase/) | Local wave phase at one longitude (default Kenya, 37.5E): weekly or daily phase roses |
| [`mjo-rmm-index`](skills/mjo-rmm-index/) | Wheeler–Hendon RMM1/RMM2, amplitude and phase for every member, from forecast OLR and u850/u200 with ERA5 EOFs rotated onto BoM's RMM |
| [`plot-rmm-phase`](skills/plot-rmm-phase/) | RMM phase-space diagram of `mjo-rmm-index` output: members on chosen days, ensemble-mean track, observed and BoM tracks |
| [`africa-itf`](skills/africa-itf/) | Fetch the latest NOAA/CPC Africa ITF / ITCZ figure: the continental position map (`--location africa`, default) or the west/east latitude-vs-dekad time series (`west-africa`, `east-africa`, not maps; CPC uses both terms) |
| [`kenya-forecast-fetch`](skills/kenya-forecast-fetch/) | Fetch a published KMSA / Rhiza Kenya forecast grid for one init: native ECMWF S2S precip (~1.5°, 101 members), its ~0.05° weekly and daily downscales, S2S temperatures, humidity and winds, medium-range precip, or GEFS. No credentials |
| [`icon-eps-fetch`](skills/icon-eps-fetch/) | Fetch the newest DWD ICON-EPS global run (40 members, to 7.5 days: precip, 2 m temperature/dewpoint/humidity, 10 m winds and gusts, cloud cover, surface pressure), regridded from ICON's triangular grid to a regular lat/lon grid. No credentials; DWD keeps only ~24 h of runs |
| [`ecmwf-04deg-fetch`](skills/ecmwf-04deg-fetch/) | Fetch one CHC-standardized ECMWF 0.4° daily precip ensemble (101 members, 46 lead days) from the private `gs://sheerwater-datalake/ecmwf-0_4/forecast/` archive, reading only the `--bbox` chunks (zarr, concurrently). Needs Google Cloud credentials; licensed data, keep it private |
| [`resolve-chc-region`](skills/resolve-chc-region/) | Bbox and boundary polygon for the Tana River basin, Kenya's KNSDI national outline (plus county lines), the Kenya OND briefing box, and East Africa clipped at 15°S, in the same shape as `resolve-region` |
| [`chc-plot-theme`](skills/chc-plot-theme/) | KMSA and classic CHC plot themes (`--theme kmsa` or `chc`, JSON to save for `plot --theme-file`) and CHC/KMSA rainfall colormaps (`chc_precip_<window>`, `chc_precip_anom_<window>`, `kmsa`; `--for ZARR` picks the window), printed as JSON for a trace's `meta.palette`, plus the KMSA-style Kenya map recipe |
| [`efi-sot`](skills/efi-sot/) | Extreme Forecast Index and upper/lower Shift of Tails of an ensemble against a model climate (reforecast samples, or an m-climate with a `quantile` dim) |
| [`accumulate`](skills/accumulate/) | Total a variable over any window of `step` or `time`: one window (forecast days 5–18), back-to-back or rolling windows of any length, or a running total. Rates become amounts |
| [`quantile`](skills/quantile/) | Quantiles (percentiles, terciles, deciles, any probability) over one or more dims, e.g. ensemble percentiles or climate tercile edges |
| [`arithmetic`](skills/arithmetic/) | Add, subtract, multiply or divide two Zarrs cell by cell, or a Zarr and a constant, with unit handling (percent of normal, rate × duration) |

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

# Equatorial waves and the RMM index from one ECMWF 46-day forecast. Fetch the inputs with
# forecasting-skills dynamical-fetch (--dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree
# --date 2026-10-10): OLR (-v net_long_wave_radiation_flux_top_of_atmosphere --bbox 30/-180/-30/180)
# and zonal wind (-v u --bbox 15/-180/-15/180)
uv run skills/olr-wave-filter/scripts/olr_wave_filter.py -i /tmp/olr.zarr -o /tmp/waves.zarr
uv run skills/plot-wave-hovmoller/scripts/plot_wave_hovmoller.py -i /tmp/waves.zarr --style ncics -o /tmp/hov.png
uv run skills/plot-wave-phase/scripts/plot_wave_phase.py -i /tmp/waves.zarr --wave kelvin --kind rose --label Kenya -o /tmp/rose.png
uv run skills/mjo-rmm-index/scripts/mjo_rmm_index.py --olr /tmp/olr.zarr --wind /tmp/u.zarr -o /tmp/rmm.zarr
uv run skills/plot-rmm-phase/scripts/plot_rmm_phase.py -i /tmp/rmm.zarr -o /tmp/rmm.png

# Latest CPC Africa ITF position map (default --location africa; the only map)
uv run skills/africa-itf/scripts/fetch.py -o /tmp/itf_africa.png

# Newest ICON-EPS 00 UTC run: daily precip rates for the coming week over Kenya
uv run skills/icon-eps-fetch/scripts/fetch.py --bbox 5.5/33.5/-5/42.5 -o /tmp/icon_kenya.zarr

# Tana River basin bbox + polygon (for clip-region --geojson / plot masks)
uv run skills/resolve-chc-region/scripts/resolve.py "Tana River basin" --geojson /tmp/tana.json

# CHC rainfall colormap sized to a totals Zarr's aggregation_period
uv run skills/chc-plot-theme/scripts/theme.py --colormap chc_precip --for /tmp/totals.zarr

# Forecast days 5-18 rainfall total, its ensemble percentiles, and the EFI / SOT against a
# reforecast climate built with the same accumulate call
uv run skills/accumulate/scripts/accumulate.py -i /tmp/fc.zarr --from "4 day" --to "18 day" -o /tmp/fc_d5-18.zarr
uv run skills/quantile/scripts/quantile.py -i /tmp/fc_d5-18.zarr --dim number -q 0.1,0.5,0.9 -o /tmp/fc_pct.zarr
uv run skills/efi-sot/scripts/efi_sot.py -i /tmp/fc_d5-18.zarr --climate /tmp/clim_d5-18.zarr -o /tmp/efi.zarr

# Percent of normal
uv run skills/arithmetic/scripts/arithmetic.py -i /tmp/fc_mean.zarr -i /tmp/clim_mean.zarr --op divide --units % -o /tmp/pct_normal.zarr
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
