# SubC MME global archive

Primary source: public GCS mirror of the CHC SubC tree (faster/more reliable
than the origin server):

`gs://sheerwater-public-datalake/chc-mirror/experimental/SubC/`

HTTPS:

`https://storage.googleapis.com/sheerwater-public-datalake/chc-mirror/experimental/SubC/`

Fallback origin (used when a mirror object is missing or unreadable): the CHC FTP
server, anonymous and passive, which CHC prefers for scripts over
`https://data.chc.ucsb.edu` (that site refuses scripts with HTTP 429):

`ftp://ftp.chc.ucsb.edu/experimental/SubC/`

The mirror was last updated on 2026-09-16, so every later issue comes from CHC.
Each CHC file is downloaded whole and opened locally. FTP 421 (too many
connections), other 4xx replies and dropped connections are retried with backoff
(15 s to 240 s); 550 means the file does not exist.

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

The mirror keeps the original flat layout:

```
{lead_folder}/global/archive/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

e.g. `chc-mirror/experimental/SubC/07_day/global/archive/mme_mean_pr_7d_20251201.nc`.

CHC now keeps every issue (old ones too) in its own date folder, with `mme_prob_*`
files and PNG maps beside the NetCDFs:

```
{lead_folder}/global/archive/{YYYYMMDD}/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

e.g. `ftp://ftp.chc.ucsb.edu/experimental/SubC/07_day/global/archive/20261104/mme_mean_pr_7d_20261104.nc`.
Issues are daily; CHC writes each one one to three days after its date.

## Variables

`pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`. Recent CHC issues (checked
2026-11-04) no longer include `tdps`.

Each file holds one data variable named `mme_mean` or `mme_anom` on dims
`(Y, X)` with 1° coordinates. Global attrs include `window_start`,
`window_end`, `window_days`, and `operation` (`sum` for precip, `mean` for
temperatures).

Variable acronyms: `../variable_acronyms.txt` on the SubC root (origin site).

## Probe-latest

The GCS JSON API lists the mirror's objects under
`chc-mirror/experimental/SubC/{lead_folder}/global/archive/` with prefix
`mme_mean_{var}_{lead_tag}_`. The CHC FTP listing of `{lead_folder}/global/archive/`
gives the date folders; for date folders newer than the mirror's latest date, the
newest ten are checked in turn and the first one holding the mean and anomaly of
every requested variable wins. If either listing fails, the other one is used.
Prefer `--probe-latest <var>`; omitting `VAR` requires a date with all six
variables, and recent CHC issues lack `tdps`.
