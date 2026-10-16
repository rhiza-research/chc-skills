# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@dev",
# ]
# ///
"""Print the KMSA plot theme file path, or one CHC or KMSA rainfall colormap as JSON."""

import json
from pathlib import Path

from weather_skills_core import weather_skill
from weather_skills_core.errors import UsageError

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

DATA = Path(__file__).resolve().parent.parent / "data"
THEME = DATA / "chc_theme.json"
COLORMAPS = json.loads(THEME.read_text(encoding="utf-8"))["colormaps"]
THEMES = {"kmsa": DATA / "kmsa_theme.json"}


@weather_skill(name="chc-plot-theme", version=_SKILL_VERSION, output=False)
@weather_skill.argument(
    "--theme",
    choices=list(THEMES),
    help="Print the path of this plot theme file, for plot --theme-file.",
)
@weather_skill.argument(
    "--colormap",
    choices=list(COLORMAPS),
    help="Print this colormap as JSON, e.g. chc_precip_week, for a trace's meta.palette.",
)
def chc_plot_theme(theme=None, colormap=None, **kwargs):
    """Print the KMSA plot theme file path, or one CHC or KMSA rainfall colormap as JSON."""
    if (theme is None) == (colormap is None):
        raise UsageError("pass exactly one of --theme or --colormap")
    if theme is not None:
        print(THEMES[theme])
        return
    print(json.dumps({"name": colormap, **COLORMAPS[colormap]}, separators=(",", ":")))


if __name__ == "__main__":
    chc_plot_theme()
