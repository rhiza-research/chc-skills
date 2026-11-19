# LandScan data used by landscan-population-fetch

## Licence and attribution

LandScan Global is published by Oak Ridge National Laboratory under the
[Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).
It may be used, copied, redistributed and adapted, commercially or not, as long as the source is
clearly attributed. Cite it as:

> Lebakula, V., Nukavarapu, N., Gonzales, J., Reynolds, B., Martin, A., Basford, S., Sorokine, A.,
> Buck, W., Urban, M. (2026). LandScan Global 2026. Oak Ridge National Laboratory.
> https://doi.org/10.48690/LSG/0EXD-MK45

The files here are unmodified LandScan counts, only cut to a box or re-laid out as a
Cloud-Optimized GeoTIFF.

## `landscan-global-2026-kenya.tif` (bundled)

LandScan Global 2026 cut to 6°N–6°S, 33–43.5°E (1440 × 1260 cells at 30″, int32, nodata
-2147483647, ~1.2 MB). The edges are multiples of 1.5° from 180°W / 90°N, so every coarse grid the
skill sums to lines up exactly with the global file. Rebuilt with:

```bash
uv run --script tools/make_landscan_subset.py landscan-global-2026-assets.zip 6/33/-6/43.5 \
  skills/landscan-population-fetch/data/landscan-global-2026-kenya.tif
```

## Global file (hosted, not in the repo)

`https://storage.googleapis.com/africa-forecasting-data/landscan/landscan-global-2026.tif`: the
whole `landscan-global-2026.tif` as a Cloud-Optimized GeoTIFF (deflate, predictor 2, 512 × 512
tiles, no overviews, ~88 MB). The skill reads only the tiles a `--bbox` touches. Built and uploaded
with:

```python
from rasterio.shutil import copy

copy(
    "landscan-global-2026.tif",
    "cog/landscan-global-2026.tif",
    driver="COG",
    COMPRESS="DEFLATE",
    PREDICTOR="2",
    LEVEL="9",
    BLOCKSIZE="512",
    OVERVIEWS="NONE",
)
```

```bash
gcloud storage cp cog/landscan-global-2026.tif gs://africa-forecasting-data/landscan/
```

For a new LandScan release, rebuild both files and update `_BUNDLED`, `_REMOTE` and `_DOI` in
`scripts/fetch.py`.
