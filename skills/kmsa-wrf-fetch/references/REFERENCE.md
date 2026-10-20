# KMSA WRF archive

## Source and mirror

KMSA runs WRF on its NWP workstation and writes post-processed runs to
`/home/nwp1/LIBS2/7_day/` and `/home/nwp1/LIBS2/Daily_Data/`. A daily job
(`tools/kmsa_wrf_sync.py`, scheduled by `.github/workflows/kmsa-wrf-sync.yml`)
copies the files byte for byte to:

```
gs://sheerwater-datalake/kmsa-wrf/7_day/
gs://sheerwater-datalake/kmsa-wrf/Daily_Data/
```

The job logs in over SFTP with the `KMSA_SERVER` (`user@host:port`) and
`KMSA_SECRET` (password) secrets, and copies every run from the last 10 days
that is new or has changed size. A run still being written is copied again
on the next sync.

## Files

`{YYYYMMDD}_to_{YYYYMMDD}_fcst.nc`. The first date is the run start date
(`--date`); the second is a nominal end and is ignored. The `.txt` files with
the same names, and any other files or folders in the archive, are not read.

Each NetCDF was built with `cdo import_binary` from the GrADS output and holds:

| Variable | Meaning | Units |
| --- | --- | --- |
| `rainc` | accumulated cumulus precipitation since the run start | mm |
| `rainnc` | accumulated grid-scale precipitation since the run start | mm |
| `t2` | 2 m temperature | K |

on `(time, lev, lat, lon)`: a 3-hourly `time` in `hours since 1-1-1`, a dummy
`lev` of 1, and a regular 291 × 328 lon/lat grid from August 2023 on (about
0.036°, 32.67–43.11°E, 5.55°S–6.19°N).

| Product | Run start | Full length |
| --- | --- | --- |
| `7day` | 06Z on the file date | 57 steps (to +168 h); 65 steps (to +192 h) from 2026-04-17 |
| `daily` | 03Z on the file date | 26 steps (to +75 h) |

About 15% of 7-day runs are shorter than full length.

## Checks

The skill refuses a file, rather than guessing, when:

- `rainc`, `rainnc` or `t2` is missing. The July 2023 files (0.045° grid,
  `rainc` only, repeated times) fail this.
- its times are not strictly increasing.
- its first time is on a different day than the filename date. A few files
  hold the previous day's run.
- rain is not zero everywhere at the first time, so it is not accumulated from
  the run start. Some daily files from March–May 2024 were de-accumulated and
  split by day before they were archived.

## Conversion

`tp = rainc + rainnc`. It is de-accumulated along `step` into a 3-hourly rate
in `mm day-1`, labeled at each interval's start. `t2` becomes `t2m` in
`degree_Celsius` and keeps its instantaneous values on the same labels, so the
last time drops off both variables.
