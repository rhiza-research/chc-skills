---
name: metar-fetch
description: "Fetch METAR airport weather reports for any airports worldwide (by ICAO code or every airport in a bbox) for a date range, decoded with MetPy, as a weather-skills station Zarr on an hourly (or 30-min / 3-hourly) axis: temperature, dewpoint, wind and gusts, visibility, QNH, cloud cover and base, present weather, and thunderstorm / precipitation / cumulonimbus flags. No credentials. Use for sub-daily observations, observed thunderstorms, fog or gusts, e.g. to verify a forecast at Nairobi JKIA or Mombasa. Each airport is a separate download, so a bbox over a large or busy region (Western Europe, the US: hundreds to thousands of airports) can take hours: name the main airports with --station instead and keep the window short."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads the Iowa Environmental Mesonet METAR archive (no credentials).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/metar_fetch.py *)
metadata:
  catalog-group: fetchers
---

# metar-fetch

Downloads the raw METARs (routine and special reports) from the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/request/download.phtml) archive, decodes each with MetPy (`metpy.io.metar.parse_metar`) and writes a station Zarr in the same layout as `ghcn-daily-fetch` and `tahmo-fetch`.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/metar_fetch.py --start-time YYYY-MM-DD --end-time YYYY-MM-DD \
  (--station ICAO ... | --bbox N/W/S/E) [--interval 1h] -o OUT.zarr
```

### Arguments
- `--start-time`, `--end-time` — inclusive UTC date range.
- `--station` — ICAO airport code (`HKJK` Nairobi JKIA, `HKMO` Mombasa, `HKKI` Kisumu, `EHAM` Amsterdam, …); repeatable, any case.
- `--bbox` — every airport inside `N/W/S/E` (found through MetPy's station table and IEM's per-country lists). With `--station` too, both sets are fetched.
- `--interval` — `1h` (default), `30min` or `3h`. Each slot takes the report nearest to it; the weather flags count every report in the slot.
- `--output`, `-o` — output Zarr.

### Output
Dims `(station_id, time)` with `latitude`, `longitude`, `elevation` (m) and `name` along `station_id`; `time` is regular UTC from `--start-time` to the last slot with a report. CF timeSeries attrs, `data_interval` = the interval, `metpy_version`.

| Variable | Units | From |
|---|---|---|
| `air_temperature`, `dew_point_temperature` | degree_Celsius | `25/23` group |
| `wind_from_direction` | degree | NaN when variable (`VRB`) |
| `wind_speed`, `wind_speed_of_gust` | knot | gust NaN when none reported |
| `visibility` | m | `9999` means 10 km or more |
| `qnh` | hPa | `Q1009` (or `A2992` converted) |
| `cloud_cover` | oktas (0–8, 9 = sky obscured) | sky condition groups |
| `cloud_base_height` | ft | lowest reported layer |
| `present_weather` | text | e.g. `-TSRA`, `BR`, `VCSH` |
| `present_weather_code` | 1 | MetPy's code for its present-weather symbols (`plot-stations-metpy`) |
| `thunderstorm`, `precipitation`, `cumulonimbus` | 0/1 | any report in the slot with `TS` (incl. `VCTS`), precipitation codes, or a `CB` cloud group |
| `metar` | text | the report used for the slot |

Slots without a report are NaN (text fields empty).

### Notes
- Kenya has ~20 reporting airports (JKIA, Wilson, Mombasa, Kisumu, Eldoret, Lodwar, Wajir, Garissa, Malindi, Nyeri, Nakuru, …). Big airports report every 30 minutes around the clock; smaller ones only in opening hours, so their nights are NaN.
- `thunderstorm` and `precipitation` record weather *at or near the airport at report time*; they are not rain amounts. METARs outside North America rarely carry rainfall totals: use `chirps-fetch`, `imerg-fetch` or `tahmo-fetch` for amounts.
- `cumulonimbus` follows the station's reporting habits: some airports (Musoma, Eldoret) add a `CB` group to most reports. Compare a station with itself rather than across stations.
- `qnh` is the altimeter setting (pressure reduced to sea level with the standard atmosphere), close to but not the same as model mean-sea-level pressure.
- Wind is in knots as reported; `unit-convert` gives m s-1. `derive-metpy` turns temperature and dewpoint into relative humidity or heat index; `point-value` + `verify` compares a forecast with these stations.
- **Many airports are slow.** Requests go one airport at a time, as IEM asks of scripts, at a few seconds each. A week over Kenya (~30 candidate airports) takes about a minute, but `--bbox` over a large or busy region pulls in every airfield IEM knows: Western Europe means ~600 airports (30–60 minutes; one run passed 30 minutes without finishing) and the US thousands (hours). For a big area:
  - pass the main airports with `--station` (about one per major city or region is enough for a map, e.g. `EHAM EGLL LFPG EDDF EDDM LEMD LIRF LSZH EBBR EKCH`);
  - or split it into country-sized bboxes;
  - keep `--start-time`..`--end-time` to the days needed: a map for one time needs one day.

  The first stderr line (`metar-fetch: N airport(s)`) shows how many airports will be fetched; at a few seconds each, that gives the run time.
- MetPy downloads its station tables (~2 MB) once, the first time `--bbox` is used.

### Errors
- Neither `--station` nor `--bbox` → exit 2.
- No airports in the bbox, or no reports for the stations and dates → exit 1 (check the ICAO codes or widen the window).
- IEM unreachable after 4 tries → exit 1; it is a shared free service, so retry later rather than in a loop.
- Reports MetPy cannot decode are skipped and counted on stderr.

### Examples
```bash
# Every Kenyan airport for a week of the long rains, hourly
uv run ${CLAUDE_SKILL_DIR}/scripts/metar_fetch.py --start-time 2026-04-10 --end-time 2026-04-16 \
  --bbox 5.5/33.5/-5/42.5 -o kenya_metar.zarr

# A Western Europe map at one time: main airports, one day (a bbox would fetch ~600 airfields)
uv run ${CLAUDE_SKILL_DIR}/scripts/metar_fetch.py --start-time 2026-10-30 --end-time 2026-10-30 \
  --station EHAM --station EGLL --station EGPH --station EIDW --station LFPG --station LFML \
  --station EDDF --station EDDM --station EDDH --station EBBR --station LSZH --station LEMD \
  --station LEBL --station LIRF --station LIMC --station EKCH -o west_europe_metar.zarr

# Nairobi JKIA and Mombasa every 30 minutes
uv run ${CLAUDE_SKILL_DIR}/scripts/metar_fetch.py --start-time 2026-04-15 --end-time 2026-04-15 \
  --station HKJK --station HKMO --interval 30min -o jkia_mombasa.zarr
```
