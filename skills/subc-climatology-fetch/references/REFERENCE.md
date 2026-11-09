# SubC model climatology archive

Origin (no mirror yet):

`https://data.chc.ucsb.edu/experimental/SubC/climatology/1999_2016/`

```
{var}/{model}/{MM}/{var}_{model}_{MMDD}.daily.nc
{var}/{model}/{MM}/{var}_{model}_{MMDD}.daily.nc.inputs.json
```

366 files per model and variable (02-29 included), about 965 GiB in all (2026-11).
Most files are 40-100 MB; GMAO-GEOS_V3 files are about 350-600 MB (0.5° grid,
15 members).

## File contents

NetCDF4/HDF5, dims `M` (member), `L` (lead, 0.5, 1.5, ... days), `Y`, `X`. Global
attrs: `title = SubC daily climatology`, `years = 1999-2016`, `window_days = 10`,
`source = SubX hindcasts`.

| Variable | Dims | Meaning |
| --- | --- | --- |
| `{var}` | M, L, Y, X | member-wise mean over historical samples (not read) |
| `{var}_ens_mean` | L, Y, X | ensemble mean of the member means |
| `{var}_ens_p05` … `_p95` | L, Y, X | percentiles pooled over samples × members (5, 20, 33.333, 66.667, 80, 95) |
| `{var}_n_draws` | L, Y, X | pooled draws per cell (not read) |
| `{var}_{acc\|avg}{7,15,30}_ens_mean` | Y, X | window from the init: `acc` = precip total (mm), `avg` = temperature mean |
| `{var}_{acc\|avg}{N}_p05` … `_p95` | Y, X | window percentiles |
| `{var}_{acc\|avg}{N}_n_draws` | Y, X | draws per cell (not read) |

Each variable is stored as one HDF5 chunk, so a bbox read still transfers the
whole field. Grids differ by model: latitude runs north to south in CFSv2 and
south to north elsewhere; longitude is 0..359 except GMAO-GEOS_V3 (-180..179.5).

The `.inputs.json` lists the hindcast files pooled for that calendar day
(`used_files`) and the screening settings (`ensemble_screening`,
`sample_min_frac`).

## Server behaviour

- Byte ranges are supported (`Accept-Ranges: bytes`).
- Bursts of requests get HTTP 429 for several minutes.
- The Let's Encrypt chain verifies against certifi's CA bundle; Python's default
  context on Windows can fail it with "certificate has expired" (an old cross-sign
  in the system store), so the skill always verifies against certifi.
