---
name: accumulate
description: "Total (accumulate) a variable over any length of a weather-skills Zarr's time or forecast lead (step) axis: one window between two leads or dates (e.g. forecast days 5 to 18), repeated windows of any length (back to back or rolling, e.g. 3-day or 21-day totals), or a running total. Rates such as mm day-1 become amounts (mm); amounts are summed. Use for rainfall totals over a custom period."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py *)
metadata:
  catalog-group: transforms
---

# accumulate

Sums each variable over windows of its time or lead axis, integrating rates into amounts.

## Usage

```bash
# One window
uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py -i IN.zarr -o OUT.zarr [--from EDGE] --to EDGE [-v VAR ...] [--time-dim DIM] [--min-coverage F]
# Repeated windows (back to back, or rolling with a shorter --stride)
uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py -i IN.zarr -o OUT.zarr --length DURATION [--stride DURATION] [--from EDGE] [-v VAR ...] [--time-dim DIM] [--min-coverage F]
# Running total
uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py -i IN.zarr -o OUT.zarr --cumulative [--from EDGE] [-v VAR ...] [--time-dim DIM]
```

### Arguments
- `--input`, `-i` — input Zarr with a `time` or `step` axis.
- `--output`, `-o` — output Zarr, replaced if it exists.
- `--from` — start edge of the (first) window. On `step`, a lead time with a unit (`0 day`, `4 day`, `36 hour`); on `time`, a date (`2026-11-01`, `2026-11-01T06:00`). Default: the start of the first sample.
- `--to` — end edge of a single window, written like `--from`. Exclusive: `--from "4 day" --to "18 day"` covers lead hours 96–432, i.e. forecast days 5–18.
- `--length` — window length with a unit (`7 day`, `21 day`, `36 hour`, `1 dekad`). Windows start at `--from` and repeat while they fit in the data.
- `--stride` — with `--length`: distance between window starts. Default: `--length` (back to back). Shorter gives overlapping rolling totals (`--length "7 day" --stride "1 day"`).
- `--cumulative` — running total from `--from` to the end of each sample.
- Pass exactly one of `--to`, `--length`, `--cumulative`.
- `--variable`, `-v` — accumulate only this variable; repeat for several. Default: every variable with the axis.
- `--time-dim` — axis to total over. Default: `time`, or `step` when there is no `time` axis or `time` has one value.
- `--min-coverage` — share (0–1) of a window the samples must cover; windows below it are NaN. Default: `1` (complete windows only). A partial window's total is the sum of the samples it has, not scaled up.

### Output
- **Units:** a rate (a unit per time: `mm day-1`, `kg m-2 s-1`) is multiplied by each sample's length and becomes an amount (`mm`, `kg m-2`); a flux like `W m-2` becomes `W s m-2`. Amounts (`mm`) and unitless counts (e.g. an indicator, giving a number of days) are summed as they are.
- The axis keeps its name and holds one label per window: the window's start for `--to`/`--length` (as `aggregate-temporal` labels), its end for `--cumulative`. Ensemble members and other dims stay.
- Attrs: `cell_methods` gains `<dim>: sum`; `aggregation_period` is the window length and `data_interval` the stride (single window: both the window length). `--cumulative` has no `aggregation_period`; it writes `<dim>_bounds` instead. A rate's `standard_name` is dropped (precip amounts get the amount name back on write); `long_name` says what was accumulated.
- Coordinate `aggregation_coverage` (0–1) on the axis. Windows below `--min-coverage` are listed on stderr.
- Variables without the axis pass through; unselected variables with it are dropped (listed on stderr).

### Notes
- Each sample covers a cell: from `<dim>_bounds` if present, else from its label for the `data_interval` attr, else for the axis spacing. Only samples whose whole cell lies inside a window count. Unevenly spaced axes without either attr exit 2: run `aggregate-temporal --period` first.
- A cell that is NaN in one grid point makes that point's window NaN; a total never silently skips a missing day.
- Absolute temperatures exit 2 (use `aggregate-temporal` for window means). Overlapping samples (rolling or already-accumulated data) exit 2. Cumulative-since-init archives must be deaccumulated first.
- Windows are fixed durations; for calendar months on `time`, use `aggregate-temporal --period monthly` then `convert-to-totals`.
- `--length`/`--stride` need a unit: `7` alone exits 2.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py -i ecmwf_kenya_2026-10-22.zarr -o ecmwf_kenya_days5-18.zarr \
  --from "4 day" --to "18 day"
```
Writes each member's total rainfall (mm) over forecast days 5–18 from daily `mm day-1` rates.

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/accumulate.py -i chirps_kenya_oct.zarr -o chirps_kenya_3day.zarr \
  --length "3 day" --stride "1 day"
```
Writes rolling 3-day CHIRPS totals, one per day, labelled at each window's first day.
