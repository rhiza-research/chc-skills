---
name: exceedance-probability
description: Compute the percentage of ensemble members (along a named dim) whose forecast value satisfies a comparison against a fixed threshold, e.g. "chance that week-1 total precip exceeds 28mm." Use whenever a dataset needs ensemble exceedance probability for a scalar threshold.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/exceedance_probability.py *)
metadata:
  catalog-group: transforms
metadata:
  catalog-group: transforms
---

# exceedance-probability

Source-agnostic ensemble exceedance probability along a named dim. For each
selected data variable, computes the percentage of entries along `--dim`
satisfying `value <comparison> threshold`, per remaining grid cell/step. Data
variables that don't carry `--dim` pass through untouched.

## When to use

- Chance of a threshold event: "probability that week-1 total precip exceeds
  28mm" — `--dim number --threshold 28 --comparison ge` on an ECMWF/GEFS
  ensemble forecast (`number` is the ensemble dim's on-disk name; the dim
  ontology aliases it to `member`).
- Any other named-dim exceedance: chance of a heatwave day (`--comparison ge`
  on temperature), chance of staying below a minimum (`--comparison lt`).

This skill takes a **fixed scalar** threshold only — it does not read a
per-gridcell threshold from a second Zarr. For a climatology-relative
threshold, compute the difference against the climatology first (`difference`
skill) and apply `exceedance-probability --threshold 0` to the result.

This skill has no time-window concept. "Chance of exceeding 28mm in week 1"
vs. "in month 1" is encoded in the *input Zarr's* time-bin shape — resample
first with `aggregate-temporal --period weekly|monthly` (a mean rate), then
`convert-to-totals` to get period totals in mm, and feed the result to this
skill.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/exceedance_probability.py \
    --input <in.zarr> --output <out.zarr> \
    --dim DIM --threshold FLOAT --comparison gt|ge|lt|le \
    [--variable VAR ...]
```

### Arguments
- `--input`, `-i` — input Zarr (any).
- `--output`, `-o` — output Zarr.
- `--dim` — the ensemble/member dimension to compute the percentage over
  (e.g. `number`, aliased to `member`, for an ECMWF/GEFS ensemble forecast).
  Must be a dim of the input.
- `--threshold` — value to compare each member against, in the target
  variable's own units. No unit conversion happens in this skill; use
  `unit-convert` upstream if needed.
- `--comparison` — the comparison applied as `value <op> threshold`: `gt`
  (greater than), `ge` (greater than or equal), `lt` (less than), or `le`
  (less than or equal).
- `--variable`, `-v` — repeatable; restricts the computation to the named
  data variable(s). Each name must be a data variable of the input and must
  carry `--dim`; violations exit non-zero. Default (unset) computes over
  every data variable carrying `--dim`. Unselected or untouched data
  variables pass through unchanged (a stderr note lists them); computing
  against a default selection where no data variable carries `--dim` exits
  non-zero.

### Output

Each selected variable becomes the percentage (0-100) of `--dim` entries
satisfying the comparison, with `--dim` collapsed. Output attrs are rebuilt
from scratch rather than carried over from the source variable: `units` is
set to `%`, and `long_name`/`GRIB_name` are both set to the same compact
descriptive label, built from the source's own `long_name` (falling back to
the variable name) and a comparison symbol, e.g. `"P(tp) ≥ 28 millimeter"`,
or `"P(precip spell (< 1.0 mm)) ≥ 3.0"` when chained after `spell-length` —
the source's `units` are omitted from the label when dimensionless, i.e.
`units == "1"`. Inheriting the source `long_name` keeps context from an
upstream derived quantity (e.g. a spell length) visible in the final label
instead of collapsing back to the bare variable name. Both `GRIB_name` and
`long_name` are set because `plot`'s colorbar-label resolution reads
`GRIB_name` first, falling back to `long_name`. `standard_name` is set to
`None` explicitly (not simply omitted): the decorator otherwise heals an
attr missing on an output variable from the same-named input variable, which
would silently reattach the source's physical-quantity `standard_name` (e.g.
`precipitation_amount`) to this differently-kinded derived percentage — and
CF has no `standard_name` for "probability of exceeding a threshold" to
verify against regardless. The collapsed dim disappears from the output
(along with its coordinates) once no data variable carries it; a dim still
carried by a pass-through variable stays. Remaining dims, coords, and
pass-through variables are unchanged.

### Provenance

The output stamps a JSON-encoded `weather_skills_history` attr: the input's
chain plus an entry for this run, `{skill, version, args, input}` (`version`
is the value printed by `--help`). Inspect a written output's lineage with
the `provenance` skill. There is no cache: every run recomputes and rewrites
`--output`, even against an unchanged input with identical flags.

## Examples

```bash
# Chance that week-1 total precip exceeds 28mm across the ECMWF ensemble.
uv run ${CLAUDE_SKILL_DIR}/scripts/exceedance_probability.py \
    -i /tmp/ecmwf_week1.zarr -o /tmp/ecmwf_week1_p28.zarr \
    --dim number --threshold 28 --comparison ge
```

```bash
# Chance of staying below a 2mm dry-day threshold, restricted to `tp`.
uv run ${CLAUDE_SKILL_DIR}/scripts/exceedance_probability.py \
    -i /tmp/ecmwf.zarr -o /tmp/ecmwf_dry.zarr \
    --dim number --threshold 2 --comparison lt --variable tp
```
