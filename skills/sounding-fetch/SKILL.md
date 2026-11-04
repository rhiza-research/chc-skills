---
name: sounding-fetch
description: "Fetch observed radiosonde (weather balloon) soundings, read with Siphon, for stations by WMO number or all stations in a bbox and a date range: temperature, dewpoint, wind and geopotential height on a common pressure grid, plus the launch-site pressure, temperature and dewpoint. Two sources: NOAA's quality-controlled IGRA2 archive (default; global, back to the 1940s, about two days behind) and the University of Wyoming service (within hours, high vertical resolution, slow and sometimes overloaded), which fills the last days IGRA2 lacks. No credentials. The output feeds sounding-indices-metpy and plot-skewt-metpy directly, e.g. 'show the observed Nairobi sounding' or 'compare observed CAPE with the forecast'."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads NOAA NCEI's IGRA2 archive and the University of Wyoming sounding service (no credentials).
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py *)
metadata:
  catalog-group: fetchers
---

# sounding-fetch

Downloads every launch of the chosen stations in the date range with Siphon, from the [Integrated Global Radiosonde Archive (IGRA2)](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive) (`IGRAUpperAir`) and/or the [University of Wyoming sounding service](https://weather.uwyo.edu/upperair/sounding.shtml) (`WyomingUpperAir`), puts each launch on a common pressure grid with MetPy (`log_interpolate_1d`), and writes a station Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time YYYY-MM-DD --end-time YYYY-MM-DD \
  (--station WMO_OR_IGRA2_ID ... | --bbox N/W/S/E) [--source auto|igra2|wyoming] \
  [--levels 10|mandatory|P1,P2,...] -o OUT.zarr
```

### Arguments
- `--start-time`, `--end-time` — inclusive UTC date range.
- `--station` — 5-digit WMO number (`63741` Nairobi/Dagoretti, `06260` De Bilt) or 11-character IGRA2 id (`KEM00063741`); repeatable.
- `--bbox` — every IGRA2 station inside `N/W/S/E` whose record overlaps the dates. With `--station` too, both sets are fetched.
- `--source` — `auto` (default), `igra2` or `wyoming`; see below.
- `--levels` — the pressure grid: a step in hPa from 1050 to 100 (default `10`), `mandatory` (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100), or a comma list.
- `--output`, `-o` — output Zarr.

### Two sources: IGRA2 preferred, Wyoming as the backup

|  | IGRA2 (NOAA NCEI) | Wyoming (University of Wyoming) |
|---|---|---|
| Quality | quality-controlled archive | raw reports as received, no archive QC |
| History | back to the 1940s | mainly recent years |
| Delay | about 2 days | within hours |
| Vertical detail | standard + significant levels (~40–100 per launch) | often the full high-resolution ascent (thousands of levels) where stations send it |
| Speed | one download per station for the whole window, ~5–15 s | one request per launch, ~20 s each |
| Reliability | steady | shared service; has been overloaded ("503 too busy") for days at a time |

IGRA2 is the default: it is checked, complete for past dates, and fast over date ranges. With `--source auto` the skill takes IGRA2, then asks Wyoming only for 00/12 UTC launches **after IGRA2's latest one and within the last 3 days**, the gap IGRA2 has not received yet. Stations that simply did not launch never trigger Wyoming requests. If IGRA2 cannot be reached at all, `auto` falls back to Wyoming for every launch in the window. If Wyoming is overloaded, the IGRA2 launches are kept and stderr says so.

An outage holds for the whole run. A server that does not answer at all (connection timeout, refused) is not retried, and the other stations of the run skip it instead of waiting again. IGRA2 is checked with a quick request (15 s) before the first download. Only "busy" answers (HTTP 429/5xx) are retried, 3 times.

`--source wyoming` uses only Wyoming: for the most recent hours, or when the full vertical detail of a launch matters (one detailed Skew-T, inversions, thin moist or dry layers; set a finer `--levels`, e.g. `2`). `--source igra2` uses only IGRA2.

The same launch from both sources gives the same picture: Nairobi on 14 April 2025 00 UTC gave CAPE 731 J kg-1 from both, identical lifted index (−2.7 K) and LCL (810 hPa), and precipitable water 28.6 vs 28.7 mm.

**Telling the user.** When the user asks for soundings from the last two days, a sounding IGRA2 does not have yet, more vertical detail, or when the result shows a `wyoming` source, mention that Wyoming is a second, unchecked source with these trade-offs, and that IGRA2 is used where it has the launch. When IGRA2 is down and Wyoming filled in, say so.

### Output
Dims `(station_id, time, level)`, CF `featureType=timeSeriesProfile`; `time` is the nominal launch time (UTC); `latitude`, `longitude`, `elevation`, `name` along `station_id`; `level` in hPa. A `source` coordinate `(station_id, time)` says where each launch came from (`igra2`, `wyoming`, or empty where none). Dataset attrs include `weather_skills_source` (`igra2`, `wyoming` or `igra2+wyoming`), `siphon_version` and `metpy_version`.

| Variable | Dims | Units |
|---|---|---|
| `t`, `dewpoint` | station, time, level | degree_Celsius |
| `u`, `v` | station, time, level | m s-1 |
| `gh` | station, time, level | m (IGRA2: reported at standard levels, interpolated between) |
| `sp` | station, time | hPa, at the launch site |
| `t2m`, `d2m` | station, time | degree_Celsius, at the launch site |

The names match what `sounding-indices-metpy`, `plot-skewt-metpy` and `vertical-interpolate-metpy` look for: they take this Zarr as it is, drop the levels below the launch site and start the parcel at the surface.

### Notes
- **Coverage changes over time.** IGRA2's station list gives each station's years; a station whose record ended before the dates is skipped with a note (except with `--source wyoming`). In Kenya only Nairobi/Dagoretti (63741) reports in recent years, roughly daily at 00 UTC; its IGRA2 record stops in October 2025 and Wyoming has no recent launches for it either (none in November 2026). Lodwar ended in 2020, most others in the 1980s–1990s. Neighbouring countries and Europe/US are far denser (De Bilt launches daily through today).
- **The grid**: each launch's levels are interpolated in log-pressure, variable by variable, with no extrapolation. Below the launch site and above the balloon's burst level are NaN. The default 10 hPa grid keeps the structure of a typical IGRA2 launch; `mandatory` gives the exact standard levels only.
- **Comparing with IGRA2's own indices**: IGRA2 publishes derived values too. Its LCL and lifted index match `sounding-indices-metpy` closely, but its precipitable water covers only surface to 500 hPa (ours the whole column, ~4 mm more at Nairobi), and its CAPE uses a different method (often ~1.5–2× MetPy's). Compare like with like.
- Wyoming takes WMO numbers, so IGRA2 stations without one (ids not of the form `..M000.....`) are IGRA2-only.
- The IGRA2 station list is cached in `~/.cache/weather-skills/` for a week, so an NCEI outage does not stop Wyoming requests.

### Errors
- Neither `--station` nor `--bbox` → exit 2.
- A station not in the IGRA2 list → exit 2.
- No launches in the window (stopped or irregular stations) → exit 1; widen the window, pick another station, or try the other `--source`.
- `--source igra2` with IGRA2 unreachable, or `--source wyoming` with Wyoming unreachable or still busy after 3 tries → exit 1; retry later or use the other source.

### Examples
```bash
# Nairobi soundings during the 2025 long rains (IGRA2), then their instability indices and one Skew-T
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time 2025-04-10 --end-time 2025-04-16 \
  --station 63741 -o nairobi_soundings.zarr

# The last three days at De Bilt: IGRA2, with Wyoming for the launches IGRA2 has not received yet
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time 2026-11-01 --end-time 2026-11-03 \
  --station 06260 -o debilt_recent.zarr

# One launch in full vertical detail from Wyoming, on a 2 hPa grid
uv run ${CLAUDE_SKILL_DIR}/scripts/sounding_fetch.py --start-time 2026-11-03 --end-time 2026-11-03 \
  --station 06260 --source wyoming --levels 2 -o debilt_detail.zarr
```
