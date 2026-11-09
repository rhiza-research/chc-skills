---
name: arithmetic
description: "Add, subtract, multiply or divide weather-skills Zarrs cell by cell, optionally with a constant on either side, with unit handling: a forecast as a ratio or percent of normal, the sum of several fields, a rate times a duration, a scale factor, or a constant minus fields (near-normal share = 100 % − above − below). Keeps only the coordinates both inputs share and broadcasts over dims only one has."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/arithmetic.py *)
metadata:
  catalog-group: transforms
---

# arithmetic

Writes `A op B op C …` for each data variable, evaluated left to right. Operands are Zarrs, plus an optional constant at the end (`A op VALUE`) or, with `--constant-first`, at the start (`VALUE op A op B …`).

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/arithmetic.py -i A.zarr -i B.zarr [-i C.zarr ...] --op add|subtract|multiply|divide -o OUT.zarr [-v VAR ...] [--units UNITS]
uv run ${CLAUDE_SKILL_DIR}/scripts/arithmetic.py -i A.zarr [-i B.zarr ...] --constant VALUE [--constant-first] --op add|subtract|multiply|divide -o OUT.zarr [-v VAR ...] [--units UNITS]
```

### Arguments
- `--input`, `-i` — the operands in order. At least two, or at least one with `--constant`.
- `--op` — `add` (A + B + …), `subtract` (A − B − …), `multiply` (A × B × …), `divide` (A / B / …). One op for the whole chain. Required.
- `--constant` — a number (`2`, `0.5`) or a quantity with units (`25.4 mm`, `7 day`, `100 %`), used as the last operand.
- `--constant-first` — use the constant as the first operand instead: `VALUE − A − B`, `VALUE / A`.
- `--output`, `-o` — output Zarr, replaced if it exists. Do not point it at an input.
- `--variable`, `-v` — only these variables; repeatable. Each must be in every input. Default: every variable all inputs share.
- `--units` — convert the result to these units (`mm`, `%`, `degree_Celsius`). Incompatible units exit 2.

### Output
- One variable per name, on the aligned dims (inner join on shared coordinates; dims only some inputs have are broadcast). Unselected variables are dropped. Dataset attrs come from the first input.
- **Units follow the maths:** add/subtract need compatible units and convert B to A's (`mm` + `m` works; °C − °C is a temperature difference, `delta_degC`); multiply/divide combine them (`mm day-1` × `7 day` = `mm`, `mm` / `mm` = `1`). A dimensionless result is written as `1`, or `%` when it is already in percent (`30 % + 25 %` = `55 %`) or with `--units %`.
- `x / 0` is written as NaN.
- Attrs come from the first input only. `long_name` gains ` sum`, ` difference`, ` product`, ` ratio`, or ` scaled` (multiply/divide by a constant). When the result is a different kind of quantity than A (a ratio, mm²), `standard_name`, `GRIB_name`, `short_name` and `cell_methods` are dropped so it is not read as A's quantity.

### Notes
- Adding or subtracting precipitation totals over different `aggregation_period`s exits 2.
- When A has dims B lacks and B has dims A lacks (e.g. `time` vs `dayofyear`), it exits 2 instead of building every pairing: put both on the same axis first.
- Labels must match exactly; no overlap exits 2. Put grids together with `coarsen --reference-grid`.
- Absolute temperatures cannot be added or multiplied (°C + °C exits 2); subtract them, or convert to K first.
- For A − B with an anomaly label, `difference` is the same operation.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/arithmetic.py -i forecast_week2_total.zarr -i chirps_week2_mean_total.zarr \
  --op divide --units % -o week2_percent_of_normal.zarr
```
Writes week-2 forecast rainfall as a percent of the CHIRPS mean for the same week.

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/arithmetic.py -i prob_above.zarr -i prob_below.zarr   --constant "100 %" --constant-first --op subtract -o prob_near_normal.zarr
```
Writes the near-normal tercile share, `100 % − above − below`, from two `exceedance-probability` outputs in `%` (the variable names must match). Use `--constant 1` for fractions.
