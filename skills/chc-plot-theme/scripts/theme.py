# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
# ]
# ///
"""Print the KMSA and CHC rainfall palettes as a Jsonnet library, or one palette as JSON."""

import json
import sys
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import parse_aggregation_period

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

PALETTES_FILE = Path(__file__).resolve().parent.parent / "data" / "chc_palettes.json"
PALETTES = json.loads(PALETTES_FILE.read_text(encoding="utf-8"))
# CHC families whose window (daily / week / month / season) --window-days or --for picks.
FAMILIES = ("chc_precip", "chc_precip_anom")
HEADER = """\
// KMSA and CHC rainfall palettes, written by chc-plot-theme.
// Each is {colors, bounds} (labels too for kmsa); colors hold one color per class,
// or under + classes + over. kmsa's top bound only closes the > 100 class.
// local chc = import 'chc_palettes.libsonnet';  then  ws.classBar(chc.kmsa, 'mm')
"""


@weather_skill(name="chc-plot-theme", version=_SKILL_VERSION, output=False)
@weather_skill.argument(
    "--colormap",
    choices=[*PALETTES, *FAMILIES],
    help=(
        "Print only this palette, as one line of JSON, e.g. kmsa or chc_precip_week. "
        "Pass the family chc_precip or chc_precip_anom with --window-days or --for to have "
        "the window picked for you. Without it, print every palette as a .libsonnet file."
    ),
)
@weather_skill.argument(
    "--window-days",
    type=float,
    help="Length of the totals window in days (e.g. 10 for a dekad, 15 for a SubC 15d outlook).",
)
@weather_skill.argument(
    "--for",
    type=Dataset("spatial"),
    dest="for_ds",
    help="Zarr to plot; the window is read from its aggregation_period attr.",
)
@weather_skill.argument(
    "-o",
    "--output",
    type=Path,
    help="Write to this file instead of stdout (e.g. chc_palettes.libsonnet).",
)
def chc_plot_theme(colormap=None, window_days=None, for_ds=None, output=None, **kwargs):
    """Print the KMSA and CHC rainfall palettes as a Jsonnet library, or one palette as JSON."""
    if colormap is None:
        if window_days is not None or for_ds is not None:
            raise UsageError("--window-days / --for go with --colormap chc_precip or chc_precip_anom.")
        text = HEADER + "{\n" + ",\n".join(
            f"  {name}: {json.dumps(palette)}" for name, palette in PALETTES.items()
        ) + ",\n}\n"
    else:
        if for_ds is not None and window_days is None:
            periods = {
                da.attrs["aggregation_period"]
                for da in for_ds.data_vars.values()
                if "aggregation_period" in da.attrs
            }
            if len(periods) != 1:
                raise UsageError(
                    f"--for needs exactly one aggregation_period on the data variables, found "
                    f"{sorted(periods) or 'none'}; pass --window-days N instead."
                )
            window_days = float(parse_aggregation_period(periods.pop()).to("day").magnitude)

        if colormap in FAMILIES:
            if window_days is None:
                raise UsageError(f"--colormap {colormap} needs --window-days N or --for PATH.zarr.")
            if window_days < 2:
                window = "daily"
            elif window_days <= 10:
                window = "week"
            elif window_days < 40:
                window = "month"
            else:
                window = "season"
            colormap = f"{colormap}_{window}"
            print(f"Using {colormap} for a {window_days:g}-day window", file=sys.stderr)
        elif window_days is not None:
            raise UsageError(
                "--window-days / --for pick the window only for --colormap chc_precip or chc_precip_anom."
            )
        text = json.dumps(PALETTES[colormap], separators=(",", ":")) + "\n"

    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
        print(f"Wrote {output}", file=sys.stderr)


if __name__ == "__main__":
    chc_plot_theme()
