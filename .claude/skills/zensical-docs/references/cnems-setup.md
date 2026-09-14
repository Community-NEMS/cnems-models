# Wiring Zensical into cnems-models

State of the repo as of 2026-09-01: **bootstrapped.** `zensical.toml` and a
starter `docs/` tree exist and `pixi run docs` passes `--strict`. The pages are
deliberately thin placeholders awaiting real content.

`zensical new` was never run — the config and pages are hand-written, so there is
no `.github/workflows/docs.yml`. Confirm with the user before running it, since it
would write one.

## Install

Already installed: `zensical = ">=0.0.57,<0.0.58"` sits with the other conda
packages under `[tool.pixi.dependencies]` (requires Python ≥ 3.10; this repo pins
3.14). Add `mkdocstrings-python` the same way (`pixi add`) if/when API reference
pages are wanted.

The tasks in `[tool.pixi.tasks]` are live:

```toml
docs = { cmd = "zensical build --strict", description = "Build the documentation with Zensical into site/." }
docs-serve = { cmd = "zensical serve", description = "Serve the documentation locally, rebuilding on changes." }
```

Run everything through pixi: `pixi run docs`, `pixi run docs-serve`.

## Layout

```
docs/
  index.md            # Background / Getting Started / Project Structure
  models/
    electricity.md
    natural_gas.md
    magic.md
site/                 # build output, gitignored
zensical.toml
```

One page per model, grouped under a "Models" section by the explicit `nav` in
`zensical.toml`. `/site` is in `.gitignore`; the Sphinx-era `/docs/*` entries were
pruned when they stopped matching anything.

`docs/` used to hold a stale 41 MB Sphinx tree under `docs/build/` (deleted
2026-09-01). Zensical has no `exclude_docs`, so anything left inside `docs_dir` is
ingested as source — that tree produced 327 link/anchor warnings and failed
`--strict`. Keep build artifacts out of `docs/`.

## Config

The live config is `zensical.toml`; read it rather than the sketch below, which
records the intended shape:

```toml
[project]
site_name = "C-NEMS Models"
site_description = "Electricity capacity-expansion and dispatch model (C-NEMS Project)."
docs_dir = "docs"
site_dir = "site"

[project.theme]
variant = "modern"
features = ["navigation.instant", "navigation.sections", "toc.follow",
            "content.code.copy", "content.code.annotate", "search.highlight"]

[[project.theme.palette]]
media = "(prefers-color-scheme: light)"
scheme = "default"
toggle.icon = "lucide/sun"
toggle.name = "Switch to dark mode"

[[project.theme.palette]]
media = "(prefers-color-scheme: dark)"
scheme = "slate"
toggle.icon = "lucide/moon"
toggle.name = "Switch to light mode"
```

Set `site_url` and `repo_url`/`edit_uri` once the hosting target is decided —
`site_url` gates instant navigation, instant previews, and custom 404 pages.

## Math is not optional here

`src/models/electricity/README.md` is the formulation reference and is full of
LaTeX. Any docs site that absorbs it needs arithmatex + MathJax wired up exactly
as shown in `configuration.md` — otherwise every formula renders as literal text.

Related: `pixi run format` must never be pointed at math-bearing Markdown with
plain `mdformat`; it mangles LaTeX unless `mdformat-myst` is installed. The
`format-md` task is commented out for that reason — leave it that way, or scope
it to exclude `docs/`.

## Source material already in the repo

Candidate inputs when building out the site — read before writing anything new:

- `README.md` — upstream Project BlueSky text; **largely stale** (still describes
  the hydrogen/residential modules and run modes this fork removed). Don't copy
  it forward wholesale.
- `src/models/electricity/README.md` — the real sets/params/variables/constraints
  reference, including the LaTeX objective and constraints. The highest-value
  page to port.
- `src/integrator/README.md` — temporal mapping crosswalk format.
- `src/common/README.md`, `analysis_tools/README.md` — smaller module notes.
- `CONTRIBUTING.md`, `CLAUDE.md` — workflow and architecture facts.
- `old_docs/` — Sphinx-era, explicitly stale. Ignore unless asked.

## API reference

Docstrings are NumPy-style (per CLAUDE.md), so mkdocstrings must be configured
with `docstring_style = "numpy"` and `paths = ["src"]`. Since `src` sits outside
`docs_dir`, edits to source files won't trigger a rebuild during `zensical serve` —
add `watch = ["src"]` under `[project]` to compensate.

## Before committing

- `pixi run zensical build --strict` must pass (link and anchor validation).
- pre-commit blocks direct commits to `main` — branch and open a PR.
