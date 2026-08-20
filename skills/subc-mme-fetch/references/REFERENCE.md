# SubC MME global archive

Public HTTPS root:

`https://data.chc.ucsb.edu/experimental/SubC/`

## Lead folders

| Folder | Filename lead tag | Lead (days) |
| --- | --- | --- |
| `07_day` | `7d` | 7 |
| `15_day` | `15d` | 15 |
| `30_day` | `30d` | 30 |

Each file is a single field for that lead: a mean (temps) or sum (precip) over
the window from the init date through that lead (`window_start` /
`window_end` / `window_days` in the NetCDF attrs). The skill maps lead →
`step` (forecast period), not a separate aggregation axis.

## Global archive paths

```
{lead_folder}/global/archive/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

Example:

`07_day/global/archive/mme_mean_pr_7d_20251201.nc`

## Variables (v1 skill)

`pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`

Each file holds one data variable named `mme_mean` or `mme_anom` on dims
`(Y, X)` with 1° coordinates. Global attrs include `window_start`,
`window_end`, `window_days`, and `operation` (`sum` for precip, `mean` for
temperatures).

Variable acronyms: `../variable_acronyms.txt` on the SubC root.
