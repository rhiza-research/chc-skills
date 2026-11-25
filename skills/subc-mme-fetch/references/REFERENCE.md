# SubC MME global archive

Source: the CHC SubC archive, `https://data.chc.ucsb.edu/experimental/SubC/`.

Fallback for a file the origin does not serve: the public GCS mirror of the
older flat archive,
`https://storage.googleapis.com/sheerwater-public-datalake/chc-mirror/experimental/SubC/`
(`gs://sheerwater-public-datalake/chc-mirror/experimental/SubC/`), which can
lag the origin.

## Outlook folders

| `--outlook` | Folder | Filename lead tag | Lead (days) |
| --- | --- | --- | --- |
| `7d` | `07_day` | `7d` | 7 |
| `15d` | `15_day` | `15d` | 15 |
| `30d` | `30_day` | `30d` | 30 |

Each file is a single field for that outlook: a mean (temps) or sum (precip)
over the window from the init date through that lead (`window_start` /
`window_end` / `window_days` in the NetCDF attrs). The skill fetches **one**
outlook per call. In the output Zarr, scalar `time` is the init, `step` is
`0 days`, and the window length is each variable's `aggregation_period`.

## Global archive paths

Origin, one folder per init:

```
{lead_folder}/global/archive/{YYYYMMDD}/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

Mirror, flat:

```
{lead_folder}/global/archive/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

Example origin URL:

`https://data.chc.ucsb.edu/experimental/SubC/07_day/global/archive/20251201/mme_mean_pr_7d_20251201.nc`

Before downloading, the skill reads the init folder's index and stops if any
requested mean or anomaly file is missing, listing the nearest init folders
from `{lead_folder}/global/archive/`.

## Variables

`pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`

Each file holds one data variable named `mme_mean` or `mme_anom` on dims
`(Y, X)` with 1° coordinates. Global attrs include `window_start`,
`window_end`, `window_days`, and `operation` (`sum` for precip, `mean` for
temperatures).

Variable acronyms: `../variable_acronyms.txt` on the SubC root (origin site).

## Probe-latest

Reads the init folders listed under `{lead_folder}/global/archive/` on the
origin, newest first, and returns the first whose index holds the mean and
anomaly file of every requested variable. Prefer `--probe-latest <var>`:
omitting `VAR` needs all six variables, and recent inits can lack `tdps`, so
the scan walks further back.
