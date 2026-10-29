---
name: quantile
description: "Quantiles (percentiles, terciles, deciles, the median or any probability) of a weather-skills Zarr over one or more named dimensions, e.g. the ensemble 10th/50th/90th percentile across members, tercile edges of a climate across years and members, or a pooled percentile across members and lead days. The summarized dims are replaced by a `quantile` dim."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/quantile.py *)
metadata:
  catalog-group: transforms
---

# quantile

Takes the requested quantiles of each data variable along the named dimensions, pooling all of them into one sample.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/quantile.py -i IN.zarr -o OUT.zarr --dim DIM [--dim DIM ...] -q Q [-q Q ...] [-v VAR ...] [--method METHOD] [--squeeze]
```

### Arguments
- `--input`, `-i` — input Zarr.
- `--output`, `-o` — output Zarr, replaced if it exists.
- `--dim` — dimension to take quantiles over; repeat for several (e.g. `--dim year --dim number` pools years and members). Required. Each must be a dim of the input.
- `--quantile`, `-q` — a probability between 0 and 1 (`0.9`, not `90`), a comma list (`0.1,0.5,0.9`), or a preset: `terciles` (1/3, 2/3), `quartiles`, `quintiles`, `deciles` (0.1–0.9), `percentiles` (0.01–0.99). Repeatable; duplicates are merged and the levels sorted. Required.
- `--variable`, `-v` — only this variable; repeat for several. Default: every data variable.
- `--method` — quantile estimator, numpy names: `linear` (default), `hazen`, `weibull`, `median_unbiased`, `normal_unbiased`, `lower`, `higher`, `midpoint`, `nearest`.
- `--squeeze` — with exactly one `-q`, drop the `quantile` dim (the level is kept in the variable's `quantile` attr).

### Output
- Each selected variable that has any of the `--dim`s gets a `quantile` dim (coordinate values are the probabilities, `units: 1`) in place of those dims. Variables without them, and unselected variables, pass through. A summarized dim is removed once no variable carries it.
- NaNs are skipped. Integer input becomes float.
- `units` are kept. `long_name` becomes `Quantiles of <long_name> across <dims>`. Attrs `quantile_dims` and `quantile_method` record what was done; `GRIB_name`, `short_name`, `comment` are dropped.

### Notes
- An input that already has a `quantile` dim is refused unless that dim is in `--dim`.
- For the share of members above a fixed threshold use `exceedance-probability`; for mean/std/min/max use `summarize-dim`.
- Write to a new path: an `--output` equal to the input destroys the input.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/quantile.py -i ecmwf_kenya_weekly_totals.zarr -o ecmwf_kenya_p10_p50_p90.zarr \
  --dim number -q 0.1,0.5,0.9
```
Writes the 10th, 50th and 90th percentile of weekly rainfall totals across the 101 ECMWF members, in mm.
