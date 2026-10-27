---
name: lead-climatology
description: "Lay daily observations onto a forecast's lead days once per year: for every year, the observed value on init month-day + each step, written as (year, step, latitude, longitude). Use to build an observed climatology sample (IMERG, CHIRPS, ERA5, ...) that matches a forecast's leads, e.g. as `--qq-reference` for rank-downscale."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/lead_climatology.py *)
metadata:
  catalog-group: transforms
---

# lead-climatology

Reshapes a multi-year daily observation record into the forecast's own lead structure, one row per year.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/lead_climatology.py -i OBS.zarr [-i OBS2.zarr ...] --like FORECAST.zarr --years YYYY-YYYY [-v VAR ...] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — daily observation Zarr with a `time` dim; repeat for several (e.g. one fetch per year). The inputs are joined along `time`; repeated days keep the first.
- `--like` — forecast Zarr with a scalar init `time` and a daily `step` dim (e.g. `dynamical-fetch` output before aggregation). Only its init and step values are read.
- `--years` — years to lay out, `YYYY-YYYY` or `YYYY`.
- `--variable`, `-v` — keep only these variables; repeat for several. Default: all.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(year, step, latitude, longitude)`; `step` takes `--like`'s values. Row `year`, step `s` is the observation on `init(month-day, year) + s`. `init_time(year)` holds each year's start date.
- Variables, units and attrs as in the inputs. Global attr `lead_climatology_like_init`.

### Notes
- A year missing any lead day, or with an all-NaN lead day, is dropped and listed on stderr. Fewer than 2 years left exits 1.
- Sub-daily inputs exit 2: run `aggregate-temporal --period daily` on each first. A `--like` with sub-daily steps also exits 2.
- A Feb 29 init uses Feb 28 in non-leap years.
- Daily values are matched by date: observations labelled at the start of the day, like `aggregate-temporal --period daily` output, line up with forecast steps labelled at the start of the day.
- Run the same `aggregate-temporal --time-dim step` call on this output as on the forecast, so weekly or dekadal windows match one to one.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/lead_climatology.py -i imerg_2006.zarr -i imerg_2007.zarr -i imerg_2008.zarr \
  --like ecmwf_kenya_2026-10-22.zarr --years 2006-2008 -o imerg_leads.zarr
```
Writes 3 years of daily IMERG over Kenya on the forecast's 46 lead days from 22 October.
