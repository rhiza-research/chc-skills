---
name: spell-length
description: Compute the longest run of consecutive time-steps satisfying a threshold comparison (e.g. a dry-spell length where precipitation stays below a threshold, or a wet-spell/heatwave length where it stays at or above one). Use whenever a dataset needs a consecutive-run-length statistic along its time/step axis, especially upstream of exceedance-probability to get "likelihood of a spell longer than N".
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/spell_length.py *)
metadata:
  catalog-group: transforms
metadata:
  catalog-group: transforms
---

# spell-length

Source-agnostic consecutive-run-length statistic along the time-like dim.
For each selected data variable, computes the longest run of consecutive
entries along `time` (or a lead-time dim such as `step`) satisfying
`value <comparison> threshold`. Data variables that don't carry the time dim
pass through untouched.

## When to use

- Dry-spell length: longest run of drought-like steps below a rainfall
  cutoff — `--threshold 1 --comparison lt` on precipitation.
- Wet-spell or heatwave length: longest run at or above a cutoff —
  `--threshold 30 --comparison ge` on temperature, or a wet-spell threshold
  on precipitation.
- Chained with `exceedance-probability`: run this skill first to get each
  ensemble member's longest spell, then feed that into
  `exceedance-probability --dim number --threshold N --comparison ge` to get
  the percentage of members whose spell is at least `N` steps long — e.g.
  "likelihood of a dry spell longer than 10 days."

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/spell_length.py \
    --input <in.zarr> --output <out.zarr> \
    --threshold FLOAT --comparison gt|ge|lt|le \
    [--variable VAR ...] [--time-dim DIM]
```

### Arguments
- `--input`, `-i` — input Zarr (any).
- `--output`, `-o` — output Zarr.
- `--threshold` — value to compare each time-step against, in the target
  variable's own units. No unit conversion happens in this skill; use
  `unit-convert` upstream if needed.
- `--comparison` — the comparison applied as `value <op> threshold`: `gt`
  (greater than), `ge` (greater than or equal), `lt` (less than), or `le`
  (less than or equal). `lt`/`le` are the dry-spell direction; `gt`/`ge` are
  the wet-spell/heatwave direction.
- `--variable`, `-v` — repeatable; restricts the computation to the named
  data variable(s). Each name must be a data variable of the input and must
  carry the time dim; violations exit non-zero. Default (unset) computes
  over every data variable carrying the time dim. Unselected or untouched
  data variables pass through unchanged (a stderr note lists them);
  computing against a default selection where no data variable carries the
  time dim exits non-zero.
- `--time-dim` — name of the time-like dim when not auto-detectable.

### Time-dim detection

Without `--time-dim`, the skill first tries the dim ontology's time
detection (CF "T" axis, then a literal `time` dim). When that finds nothing —
a classic forecast dataset, where `time` is a scalar init-date coordinate
rather than a dim — it falls back to whichever dim the ontology aliases to
the lead-time axis (e.g. `step`) and prints a note naming the dim it picked.

### NaN handling and units

Any missing value (`NaN`) anywhere along the time dim for a given
gridpoint/member marks that element's spell length `NaN` — a run computed
across a data gap can't be trusted to be the true max, so the whole element
is treated as unknown rather than reporting a possibly-truncated run.

The output variable's `units` is `"1"` (the CF convention for a dimensionless
count). This equals real days only when the time dim's cadence is daily —
the skill counts *entries* along the axis, not calendar duration. Output
attrs are rebuilt from scratch rather than carried over from the source
variable: the source's `standard_name`/`long_name` describe the input
physical quantity (e.g. a precipitation rate), not this derived run-length
count. `long_name`/`GRIB_name` are both set to the same compact descriptive
label using a comparison symbol (e.g. `"precip spell (< 1.0 millimeter)"`) —
kept short since `exceedance-probability` commonly chains directly off this
output and inherits the label as its own described quantity; a longer
sentence here would compound into an overflowing colorbar label downstream.
Both `GRIB_name` and `long_name` are set because `plot`'s colorbar-label
resolution reads `GRIB_name` first, falling back to `long_name`.
`standard_name` is set to `None` explicitly (not simply omitted): the
decorator otherwise heals an attr missing on an output variable from the
same-named input variable, which would silently reattach the source's
physical-quantity `standard_name` to this differently-kinded derived count —
and CF has no entry for "consecutive spell length" to verify against
regardless.

### Output

Each selected variable becomes its longest consecutive-run length, with the
time dim collapsed. The collapsed dim disappears from the output (along with
its coordinates) once no data variable carries it; a dim still carried by a
pass-through variable stays. Remaining dims (e.g. `number`, `latitude`,
`longitude`), coords, and pass-through variables are unchanged.

### Provenance

The output stamps a JSON-encoded `weather_skills_history` attr: the input's
chain plus an entry for this run, `{skill, version, args, input}` (`version`
is the value printed by `--help`). Inspect a written output's lineage with
the `provenance` skill. There is no cache: every run recomputes and rewrites
`--output`, even against an unchanged input with identical flags.

## Examples

```bash
# Longest dry-spell run per ensemble member (ECMWF S2S forecast, step axis).
uv run ${CLAUDE_SKILL_DIR}/scripts/spell_length.py \
    -i /tmp/ecmwf.zarr -o /tmp/ecmwf_dry_spell.zarr \
    --threshold 1 --comparison lt --variable tp
```

```bash
# Chained: likelihood of a dry spell longer than 10 days across the ensemble.
uv run ${CLAUDE_SKILL_DIR}/scripts/spell_length.py \
    -i /tmp/ecmwf.zarr -o /tmp/ecmwf_dry_spell.zarr \
    --threshold 1 --comparison lt --variable tp
uv run ${CLAUDE_SKILL_DIR}/../exceedance-probability/scripts/exceedance_probability.py \
    -i /tmp/ecmwf_dry_spell.zarr -o /tmp/ecmwf_dry_spell_p10.zarr \
    --dim number --threshold 10 --comparison ge
```
