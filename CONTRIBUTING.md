# Contributing

## Publish model

`main` is the consumer-facing branch. Anything merged to `main` is considered
published. Each `skills/<name>/SKILL.md` carries a `metadata.version` field (Agent Skills
spec). That is the published identity. Scripts carry a matching `_SKILL_VERSION`
constant; the version-bump workflow rewrites both in lockstep.

## PR workflow

1. Branch off `main`.
2. Open a PR back into `main`.
3. CI must pass (ruff, inline-deps, pytest, per-script `--help`).
4. Rebase onto `main` before merging — linear history is preferred.
5. Use the GitHub merge button.

Authors **must not** edit `metadata.version` or `_SKILL_VERSION` by hand.

## Skill correctness tests

Per-skill tests live in `skills/<name>/tests/`. Shared helpers are in
`tests/conftest.py`. Run them with `uv sync --group dev && uv run pytest`.

## Version bumps

On push to `main`, `.github/workflows/version-bump.yml` bumps changed skills and
publishes a lean plugin payload to `plugin-dist`. Bump kind comes from PR labels:

| Label            | Bump kind |
| ---------------- | --------- |
| `release: major` | major     |
| `release: minor` | minor     |
| (none)           | patch     |

## Local development against weather-skills-core

```bash
tools/run_with_local_core.sh skills/subc-mme-fetch/scripts/fetch.py --help
```

Core is pinned to `main` in `pyproject.toml` and every
skill script's PEP 723 header.
