# Cropland data used by cropland-fetch

## Licence and citation

The Global Synergy Cropland Map is published on Harvard Dataverse
([doi:10.7910/DVN/ZWSFAA](https://doi.org/10.7910/DVN/ZWSFAA), version 3) under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain): it may be copied,
redistributed and adapted without conditions. Cite it as:

> Lu, M., Wu, W., You, L., See, L., Fritz, S., Yu, Q., Wei, Y., Chen, D., Yang, P. and Xue, B.
> (2020). A cultivated planet in 2010 – Part 1: The global synergy cropland map. Earth System
> Science Data, 12, 1913–1928. https://doi.org/10.5194/essd-12-1913-2020

The files here are the unmodified `Global-cropland-percentage-map.tif` values (float32 cropland
fraction, 0–1, nodata -3.4028230607370965e38 over the sea), only cut to a box or re-laid out as a
Cloud-Optimized GeoTIFF. The GeoTIFF's embedded georeferencing matches the dataset's
`Global-cropland-Geoinformation.tfw` (15″ cells, upper-left corner 173.1125°W, 71.19167°N).

## `global-synergy-cropland-2010-kenya.tif` (bundled)

Cut to 6°N–6°S, 33–43.5°E (2520 × 2880 cells at 15″, ~4.1 MB). The edges are multiples of 1.5°
from 180°W / 90°N, so every coarse grid the skill aggregates to lines up exactly with the global
file. Built with:

```python
import rasterio
from rasterio.windows import from_bounds

with rasterio.open("Global-cropland-percentage-map.tif") as ds:
    win = from_bounds(33, -6, 43.5, 6, ds.transform).round_offsets().round_lengths()
    data = ds.read(1, window=win)
    profile = ds.profile | dict(
        width=win.width,
        height=win.height,
        transform=ds.window_transform(win),
        compress="deflate",
        predictor=3,
        zlevel=9,
        tiled=True,
        blockxsize=256,
        blockysize=256,
    )
with rasterio.open("global-synergy-cropland-2010-kenya.tif", "w", **profile) as dst:
    dst.write(data, 1)
```

## Global file (hosted, not in the repo)

`https://storage.googleapis.com/africa-forecasting-data/cropland/global-synergy-cropland-2010.tif`:
the whole map as a Cloud-Optimized GeoTIFF (deflate, predictor 3, 512 × 512 tiles, no overviews,
~484 MB). The skill reads only the tiles a `--bbox` touches. Built and uploaded with:

```python
from rasterio.shutil import copy

copy(
    "Global-cropland-percentage-map.tif",
    "cog/global-synergy-cropland-2010.tif",
    driver="COG",
    COMPRESS="DEFLATE",
    PREDICTOR="3",
    LEVEL="9",
    BLOCKSIZE="512",
    OVERVIEWS="NONE",
    BIGTIFF="IF_SAFER",
)
```

```bash
gcloud storage cp cog/global-synergy-cropland-2010.tif gs://africa-forecasting-data/cropland/
```
