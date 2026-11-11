# SubC individual-model forecast archive

Origin (no mirror yet):

`ftp://ftp.chc.ucsb.edu/experimental/SubC/forecasts/`
(also browsable at `https://data.chc.ucsb.edu/experimental/SubC/forecasts/`)

```
{var}/daily/{model}/{MM}/{var}_{model}_{YYYYMMDD}.daily.nc
```

Each `MM` folder holds that calendar month for every year (2017/2018 onward), so
finding the latest init means listing this month's folder and walking back. The
files are SubX real-time forecasts mirrored from IRIDL
(`source_url` attr: `iridl.ldeo.columbia.edu/SOURCES/.Models/.SubC/...`).
`dir_acronyms.txt` at the root lists the variable names.

## Init calendars (2026-11)

| Model | Members | Leads (days) | Inits |
| --- | --- | --- | --- |
| NCEP-CFSv2 | 4 | 44 | daily |
| ECCC-GEPS8 | 21 (0 = control) | 39 | twice weekly |
| GMAO-GEOS_V3 | — | — | weekly-ish, 0.5° grid, ~140 MB per `pr` file |
| EMC-GEFSv12_CPC, ESRL-FIMr1p1, RSMAS-CCSM4, GMAO-GEOS_V2p1_5daily | — | — | weekly-ish |

## File contents

NetCDF4/HDF5 written from IRIDL OPeNDAP (`Conventions = IRIDL`). One variable per
file, dims `S` (init, length 1), `M` (member), optional `P` (pressure, hPa, e.g. 500
and 200 for `zg`), `L` (lead, 0.5 ... days), `Y`, `X`. Dim order varies by model
(`S, M, ...` for CFSv2, `M, S, ...` for ECCC). `L` is float days in some files and a
CF timedelta in others. Latitude runs north to south (CFSv2) or south to north
(ECCC); longitude 0..359.

Precipitation is `kg m-2 s-1` (`precipitation_flux`), a daily-mean flux;
temperatures in K; `zg` in m.

Chunking (tiles) varies: CFSv2 `(1, 4, 44, 30, 60)` (all members and leads per
30°x60° tile), ECCC `(11, 1, 20, 91, 180)` (half the members, 20 leads, a
quarter of the globe). The skill downloads whole files, so the tiling does not matter for it.

## Server behaviour

CHC asks download scripts to use FTP (`ftp://ftp.chc.ucsb.edu/`, the same tree as
`https://data.chc.ucsb.edu/`; CHC's note gives a `/pub/org/chc/` prefix, but the
anonymous root is already the CHC tree). The HTTPS side answers bursts with HTTP 429
for several minutes.

- Anonymous login, passive mode. `REST` byte ranges work, but each one opens a new
  data connection (~3.5 s), so the skill downloads whole files (~4 MB/s measured
  2026-11-09) into a temporary directory and opens them locally.
- Reply codes: 550 = no such file/directory; 421 = too many connections (retried
  with backoff); other 4xx are temporary.

## Icechunk mirror

`gs://africa-forecasting-data/chc-mirror/experimental/SubC/forecasts/{var}.icechunk`
(same path as CHC, `.icechunk` per variable). One group per model; each a forecast
dataset `{var}(time, number, step, latitude, longitude)` plus `valid_time(time, step)`,
in this skill's output conventions (`pr` mm day-1, latitude ascending, longitude
-180..180, coordinates rounded to 4 decimals). Chunks `(1, all members, all leads,
30°, 30°)`; one commit per init, so appends never rewrite stored chunks.

Contents (2026-11-10): `pr` only, inits 2026-09-01 to 2026-11-09. GMAO-GEOS_V3's
15-member end-of-month runs (09-28, 10-28) keep members 1-5, matching its regular
5-member runs. Built with `convert_to_icechunk.py` from the CHC files; not yet
refreshed automatically. Moving it to the public datalake
(`sheerwater-public-datalake/chc-mirror/...`) needs write access there.

## Bias-corrected forecasts

`ftp://ftp.chc.ucsb.edu/products/SubX/Data/Bias-corrected/{region}_pr_sfc_{model}.daily.nc`
(HTTPS: `https://data.chc.ucsb.edu/products/SubX/Data/Bias-corrected/`). Regions `EA`,
`WA`, `SA`; models `NCEP-CFSv2`, `EMC-GEFSv12`, `GMAO-GEOS_V3`, `RSMAS-CCSM4` (only
precipitation). Checked 2026-11-11:

| Region | Latitude (cell centres) | Longitude | Grid |
| --- | --- | --- | --- |
| EA | -12.975 to 24.975 | 20.025 to 51.975 | 760 x 640 |
| WA | 0.025 to 19.975 | -19.975 to 29.975 | 400 x 1000 |
| SA | -35.975 to -2.025 | 10.025 to 54.975 | 680 x 900 |

- Layout: SubX as in the raw files: `pr(L, M, Y, X)` with scalar `S` (days since
  1960-01-01, the init), `L` 0.5, 1.5, ... days (GEFSv12 starts at 1.5), `M` the
  members (CFSv2 1-4, GEFSv12 0-30, GEOS_V3 1-5, CCSM4 1-9). The full lead range of
  the run (44, 34, 36 and 45 days). `pr` has no attrs; values are mm/day (they follow
  the raw run day by day; Kenya-box mean over 44 days 2.67 against 2.32 raw for CFSv2
  2026-11-05). Global attr `obs_climatology = chirps3-sat`. Land and sea are both filled.
- Storage: float32, uncompressed, contiguous (no HDF5 chunks). A bbox subset would need
  one byte-range read per lead and member (~3.5 s each over FTP), so the skill
  downloads the whole file. Sizes (EA): CFSv2 342 MB, GEOS_V3 350 MB, CCSM4 788 MB,
  GEFSv12 2.05 GB.
- `Processed_forecasts/` holds the same values with `precip(time, ens, latitude,
  longitude)` cut to 31 days from the issue Thursday (`time` in days since that date;
  GEFSv12 30 days from the Friday), plus `plot_latest_forecasts_as_of_*.png`. Verified
  identical to the root file at the matching lead for CFSv2, GEOS_V3, CCSM4 and GEFSv12.
- Refresh: weekly. Per CHC, the files hold the forecasts starting on the latest Thursday
  through the next 30 days; each model's latest run up to that Thursday (2026-11-05
  issue: CFSv2 and GEFSv12 2026-11-05, GEOS_V3 2026-11-02, CCSM4 2026-11-01), written
  2026-11-06 to 2026-11-08. The previous issue is overwritten; nothing is archived.
- Freshness check in the skill: today's latest Thursday T; the run is current when
  T-6 <= init <= T and the processed file's first day equals max(T, init + first lead).
  Both are read from the file headers by byte ranges (a few seconds each).
