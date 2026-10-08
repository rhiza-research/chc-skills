# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@dev",
# ]
# ///
"""Print one CHC or KMSA rainfall colormap as JSON for plot's meta.palette."""

import json
from pathlib import Path

from weather_skills_core import weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

THEME = Path(__file__).resolve().parent.parent / "data" / "chc_theme.json"
COLORMAPS = json.loads(THEME.read_text(encoding="utf-8"))["colormaps"]


@weather_skill(name="chc-plot-theme", version=_SKILL_VERSION, output=False)
@weather_skill.argument(
    "--colormap",
    required=True,
    choices=list(COLORMAPS),
    help="Colormap to print, e.g. kmsa or chc_precip_week.",
)
def chc_plot_theme(colormap, **kwargs):
    """Print one CHC or KMSA rainfall colormap as JSON for plot's meta.palette."""
    print(json.dumps({"name": colormap, **COLORMAPS[colormap]}, separators=(",", ":")))


if __name__ == "__main__":
    chc_plot_theme()
