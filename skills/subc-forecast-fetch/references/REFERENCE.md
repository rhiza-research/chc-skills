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
