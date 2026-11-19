# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["rasterio>=1.3"]
# ///
"""Cut a box out of a LandScan Global count GeoTIFF, for landscan-population-fetch's data/.

    uv run --script tools/make_landscan_subset.py SRC.tif|SRC.zip N/W/S/E OUT.tif

N/W/S/E must sit on multiples of 1.5 degrees from 180W / 90N, so every coarse grid the skill
sums to (0.05, 0.1, 0.25, 0.5, 1.5) lines up with the global file. The output keeps the
source's int32 counts, nodata and georeferencing, deflate-compressed.
"""

import sys
import zipfile
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds


def main(src: str, box: str, out: str) -> None:
    n, w, s, e = (float(v) for v in box.split("/"))
    for v, origin in ((w, -180.0), (e, -180.0), (n, 90.0), (s, 90.0)):
        if abs(((v - origin) / 1.5) - round((v - origin) / 1.5)) > 1e-9:
            sys.exit(f"{box}: every edge must be a multiple of 1.5 deg from 180W / 90N")
    path = src
    if src.lower().endswith(".zip"):
        with zipfile.ZipFile(src) as zf:
            member = next(m for m in zf.namelist() if m.endswith(".tif") and "colorized" not in m)
        path = f"/vsizip/{Path(src).resolve().as_posix()}/{member}"
    with rasterio.open(path) as ds:
        win = from_bounds(w, s, e, n, ds.transform).round_offsets().round_lengths()
        data = ds.read(1, window=win)
        profile = ds.profile | {
            "width": win.width,
            "height": win.height,
            "transform": ds.window_transform(win),
            "compress": "deflate",
            "predictor": 2,
            "zlevel": 9,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(
            source=f"LandScan Global, cut to {box} from {Path(src).name}",
            license="CC BY 4.0",
        )
    print(f"{out}: {win.height} x {win.width}, {Path(out).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
