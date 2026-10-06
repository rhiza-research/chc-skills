# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@dev",
# ]
# ///
"""Print the path of the CHC plot theme file (CHC and KMSA rainfall colormaps)."""

from pathlib import Path

from weather_skills_core import weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.1"

THEME = Path(__file__).resolve().parent.parent / "data" / "chc_theme.json"


@weather_skill(name="chc-plot-theme", version=_SKILL_VERSION, output=False)
def chc_plot_theme(**kwargs):
    """Print the path of the CHC plot theme file (CHC and KMSA rainfall colormaps)."""
    print(THEME)


if __name__ == "__main__":
    chc_plot_theme()
