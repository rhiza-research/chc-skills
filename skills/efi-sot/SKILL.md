---
name: efi-sot
description: "Extreme Forecast Index (EFI) and Shift of Tails (SOT) of an ensemble forecast against a model climate (reforecast/hindcast samples, or a pre-computed m-climate with a quantile dim). EFI (-1 to 1) says how far the whole ensemble sits from its climate; SOT says whether the wettest/hottest (or driest/coldest) 10% of members go beyond the climate's 99th (or 1st) percentile. Use to flag unusual or extreme weeks in a forecast."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/efi_sot.py *)
metadata:
  catalog-group: transforms
---

# efi-sot

Compares each grid cell's forecast ensemble with the model climate for the same lead and window.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/efi_sot.py -i FORECAST.zarr --climate CLIMATE.zarr -o OUT.zarr [-v VAR] [--climate-variable VAR] [--member-dim DIM] [--climate-dim DIM ...] [--sot both|upper|lower|none]
```

### Arguments
- `--input`, `-i` — forecast ensemble Zarr with a member dim.
- `--climate` — model-climate Zarr in one of two forms:
  - **samples**: values per hindcast member and/or year (e.g. reforecasts with dims `(year, number, step, latitude, longitude)`). Their quantiles are computed here.
  - **quantiles**: a `quantile` dim with probabilities in 0–1 that span at least 0.01–0.99 (e.g. 101 levels 0, 0.01, …, 1). Levels between those given are linearly interpolated.
- `--output`, `-o` — output Zarr, replaced if it exists.
- `--variable`, `-v` — forecast variable. Default: the single variable both inputs share.
- `--climate-variable` — climate variable when its name differs. Default: `--variable`.
- `--member-dim` — forecast ensemble dim. Default: `number`.
- `--climate-dim` — climate sample dim to pool; repeat for several. Default: every climate dim the forecast's field lacks plus the climate's own member dim (e.g. `year` and `number`). Not used with a quantile climate.
- `--sot` — which Shift of Tails to write: `both` (default), `upper`, `lower`, `none`.

### Output
- `<var>_efi` — Extreme Forecast Index, −1 to 1, units `1`. `EFI = 2/π ∫ (p − F_f(p)) / √(p(1−p)) dp` over p = 0.01…0.99 (trapezoid), where `F_f(p)` is the share of members below the climate p-quantile (Lalaurette 2003). 0: the ensemble matches climate; ±1: every member beyond the climate's extremes.
- `<var>_sot_upper` — `(Q_f(0.9) − Q_c(0.99)) / (Q_c(0.99) − Q_c(0.9))`. Above 0: the wettest/warmest 10% of members exceed the climate's 99th percentile.
- `<var>_sot_lower` — `(Q_c(0.01) − Q_f(0.1)) / (Q_c(0.1) − Q_c(0.01))`. Above 0: the driest/coldest 10% fall below the climate's 1st percentile.
- Dims: the forecast's dims without the member dim, on the cells and leads both inputs share (inner join). The forecast's `aggregation_period` is kept.
- SOT is NaN where the climate tail is flat (denominator ≤ 0, e.g. dry seasons where Q_c(0.9) = Q_c(0.99) = 0). EFI is NaN where all members or any climate quantile is missing.

### Notes
- Forecast and climate must describe the same quantity over the same window: both weekly totals in mm, both daily means in °C. Climate units are converted to the forecast's (m → mm); a rate against a total exits 2, and so do differing `aggregation_period` stamps. Build matching windows with the same `accumulate` (or `aggregate-temporal`) call on both.
- Leads must match by label: give the climate the forecast's `step` values (e.g. `lead-climatology --like`, or `planette-reforecast-fetch` for the same init). Grids must match exactly: `coarsen --reference-grid` one onto the other. No overlap exits 2.
- A sample climate needs at least 10 values per cell; tail quantiles from fewer than ~100 are noisy.
- Ties count as not below: where many climate quantiles are 0 (dry regions) an all-dry forecast gets a positive EFI. Read precipitation EFI together with SOT and the climate there.
- Uses strict `<` and `linear` quantiles, matching the ECMWF-S2S4AFRICA pipeline's `efi_sot.py`.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/efi_sot.py -i ecmwf_kenya_weekly_totals.zarr \
  --climate reforecast_kenya_weekly_totals.zarr -o ecmwf_kenya_efi.zarr
```
Writes weekly-rainfall EFI and upper/lower SOT per lead week over Kenya, against reforecast members pooled over years and members.
