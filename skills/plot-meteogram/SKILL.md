---
name: plot-meteogram
description: "Render a probabilistic meteogram PNG for one city or point from an ensemble forecast: cloud cover over a day/night sky, rain or snow drops whose darkness is the chance of light, medium or heavy rain, a 2 m temperature fan that fades where few members are, and windsocks for the chance of strong wind, in local time. Use when someone wants an easy-to-read multi-day weather forecast for a place (\"what will the weather be in Nairobi this week\") from ECMWF IFS-ENS, GEFS, ICON-EPS or any ensemble with 2 m temperature."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_meteogram.py *)
metadata:
  catalog-group: figure
---

# plot-meteogram

Draws a probabilistic meteogram for one point: the ensemble's spread is shown as transparency, not as numbers or percentiles, so it reads without a statistics background. The layout follows [milankl/prob_meteogram](https://github.com/milankl/prob_meteogram) (MIT licence, SciPy 2019 plotting contest).

- **Clouds** (top): a light-blue sky with a yellow sun at each local solar noon. The clouds are one bar centred in the panel, whose height is the ensemble-median total cloud cover, so more blue means clearer sky. When the input has low, mid and high cloud, the bar is split in proportion to the three layers' median cover: low (dark grey) at the bottom, mid, then high (light grey) on top, with a key on the right. The total comes from a total-cover field when one is given (`tcc`, `total_cloud_cover_atmosphere`, possibly from another `-i`). Otherwise it is estimated from the layers assuming random overlap, 1 − (1−low)(1−mid)(1−high). Without layers, the bar is plain grey. This panel shows the median only, not the spread.
- **Rain** (middle): one slanted drop per interval in three rows (light, medium, heavy; thresholds labelled on the right). A drop's darkness is the share of members with at least that much rain in the interval, as the key on the right shows. Where the median 2 m temperature is below 0 °C, snowflakes are drawn instead.
- **Temperature** (bottom): a colour background faded with white from the outermost members inward, so the colour is strongest where most members agree. The daily median high and low are labelled. The first day's sunrise and sunset are shown as `☼ ↑06:11 ↓18:21`.
- **Wind** (strip under the temperature fan): a red and white windsock per interval where members reach Beaufort 5 (8 m/s mean 10 m wind). Its darkness is the share of members that reach it. The sock droops at Beaufort 5 and flies straighter when the 75th percentile reaches Beaufort 6 or 7. With a gust field, the median daily maximum gust is written above the windsocks. When no member reaches Beaufort 5 and there is no gust field, the strip is left out and a note says so.
- **Time axis:** 06h, 12h and 18h under each day (for forecasts up to 10 days), and weekday and date labels with weekends in blue. The title is bold: `Meteogram <label> (lat, lon)`, with the model, init time and member count on the right.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_meteogram.py -i FC.zarr [-i MORE.zarr ...] --lat LAT --lon LON -o OUT.png \
  [--label NAME] [--max-lead 10D] [--utc] [--precip-interval 6h] [--precip-thresholds 0.1,1,4] \
  [--no-clouds] [--no-wind] [--title TEXT]
```

### Arguments
- `--input`, `-i` — forecast Zarr with `step`, a scalar init `time`, lat/lon and (usually) `number`. Repeat it to take fields from several stores; each field is taken from the first input that has it.
- `--lat`, `--lon` — the point. The nearest grid point is used, and stderr and the figure footnote give its position and distance.
- `--label` — place name for the title. Default: the coordinates.
- `--max-lead` — last lead to plot (pandas timedelta: `168h`, `10D`). Default `10D`.
- `--utc` — label times in UTC. By default times are the point's local time (time zone from `timezonefinder`).
- `--precip-interval` — length of one rain and windsock column (pandas timedelta). Default: `6h`, or the data's step when that is longer (daily for S2S).
- `--precip-thresholds` — light, medium and heavy rain thresholds in mm per `--precip-interval`. Default: `0.1,1,4` up to 6 h, `0.2,2,8` up to 12 h, `1,5,20` per day for longer intervals.
- `--no-clouds`, `--no-wind` — leave out the cloud panel or the windsocks.
- `--title` — bold figure title. Default: `Meteogram <label> (lat, lon)`. The model, init time and member count are always shown on the right.
- `--output`, `-o` — PNG path.

### Fields it looks for
Fields are matched by variable name first, then by CF `standard_name`:

| Field | Names | Needed |
|---|---|---|
| 2 m temperature | `t2m`, `2t`, `temperature_2m`, `tas` / `air_temperature` | required (exit 2 without it) |
| precipitation | `tp`, `precip`, `precipitation_surface`, `pr` / `lwe_precipitation_rate`, `precipitation_flux` | rain panel |
| 10 m wind | `u10`+`v10`, `wind_u_10m`+`wind_v_10m`, or a speed `si10`, `wind_speed_10m` | windsocks |
| gust | `fg10`, `10fg`, `i10fg`, `wind_gust_10m`, `vmax_10m` / `wind_speed_of_gust` | optional gust labels |
| cloud cover | low/mid/high `lcc`/`mcc`/`hcc`, `cloud_cover_low`/`_medium`/`_high`, `clcl`/`clcm`/`clch`; else total `tcc`, `total_cloud_cover_atmosphere`, `clct` | cloud panel |

A panel whose fields are missing is left out with a note on stderr. A field with extra dims (such as a `vertical` level) is skipped.

### Notes
- **Getting the point for a city:** pass the city's coordinates when you know them. Otherwise run `resolve-region "<City>, <Country>"`. It prints the city's bbox `N/W/S/E`; pass its centre, `--lat (N+S)/2 --lon (W+E)/2`.
- **Fetch a small box around the point**, not a country. One grid cell around the point is enough, for example `--bbox N/W/S/E` within ±0.5° for a 0.25° model.
- Precipitation must be a rate (`mm day-1`, as the fetchers write it); an amount (`mm`) exits 2. Each step's rate is turned into an amount over its interval: from `step_bounds` when present, otherwise from `data_interval` or the step spacing, starting at the step. The amounts are then summed into `--precip-interval` columns.
- Few models carry gusts: ICON-EPS (`fg10`) and the full ECMWF archive (`10fg`) do, but ECMWF S2S and dynamical GEFS do not. The windsocks always use the mean 10 m wind, so plots from different models compare.
- On coarse grids (1.5° S2S) the nearest cell can be several tens of km away, or over the sea for a coastal city. Check the footnote.
- Best for 1–15 day forecasts. For longer leads, day labels thin out to every few days.
- A deterministic run (no `number` dim) is plotted as one member. Temperature is a thick line coloured by temperature, every probability is 0 or 1, and the header says "single run (no ensemble)".
- Of the dynamical.org datasets, only `dwd-icon-eu-forecast-5-day` (deterministic, Europe, hourly, 5 days) has separate low, mid and high cloud and gusts. The ensembles (IFS-ENS, AIFS-ENS, GEFS) and ICON-EPS have total cloud cover only.

### Example
Nairobi from the ECMWF IFS ensemble on dynamical.org:
```bash
uv run <dynamical-fetch>/scripts/fetch.py --dataset ecmwf-ifs-ens-forecast-15-day-0-25-degree --date 2026-11-01 \
  --bbox -1/36.5/-1.5/37 -v temperature_2m -v precipitation_surface -v wind_u_10m -v wind_v_10m \
  -v total_cloud_cover_atmosphere -o ifs_nairobi.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_meteogram.py -i ifs_nairobi.zarr --lat -1.29 --lon 36.82 \
  --label Nairobi -o meteogram_nairobi.png
```
This draws 10 days in Nairobi time (Africa/Nairobi): rain columns every 6 h, 51 members, from the grid point at -1.25, 36.75 (9 km away).
