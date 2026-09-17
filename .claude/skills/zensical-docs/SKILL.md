---
name: zensical-docs
description: Write, configure, preview, and publish project documentation with Zensical (the Rust-cored static site generator from the Material for MkDocs team). Use when creating or editing pages under docs/, editing zensical.toml, bootstrapping the docs site, or answering "how do I do X" for Zensical / Material-for-MkDocs-flavored Markdown (admonitions, content tabs, code annotations, grids, mermaid, math, mkdocstrings).
---

# Writing docs with Zensical

Zensical is a static site generator by the Material for MkDocs team: Markdown in,
self-contained static site out. It renders Python Markdown + PyMdown Extensions
(same content syntax as Material for MkDocs) and is configured with a
`zensical.toml` file. It can also read an existing `mkdocs.yml` — but for new
projects use `zensical.toml`.

Verified against zensical 0.0.52; this repo runs 0.0.57 (PyPI and conda-forge), docs at
<https://zensical.org/docs/>. Zensical is pre-1.0 and moving fast; if something
here doesn't work, check the live docs before assuming the user's setup is broken.

## Reference files

Read these on demand — don't inline their content into answers you can look up:

- `references/authoring.md` — Markdown syntax cheat sheet: admonitions, content
  tabs, code blocks/annotations, grids, images, tables, math, mermaid, icons,
  footnotes, tooltips, front matter.
- `references/configuration.md` — `zensical.toml` reference: project settings,
  theme features and palettes, nav, markdown extensions (and their defaults),
  validation, plugins, mkdocstrings, publishing workflows.
- `references/cnems-setup.md` — how Zensical should be wired into *this* repo
  (pixi tasks, docs layout, math + API-reference config). Read before bootstrapping.

## Commands

```sh
zensical new .        # bootstrap: docs/, zensical.toml, .github/workflows/docs.yml
zensical serve        # preview on localhost:8000, rebuilds + reloads on change
zensical build        # render static site into site/ (default)
```

Useful flags: `serve -o` (open browser), `serve -a IP:PORT`, `build --clean`
(no cache — what CI should use), `build --strict` (fail on warnings),
`-f/--config-file PATH` on both. `zensical <cmd> --help` for the rest.

In this repo Zensical runs inside the pixi env: `pixi run zensical serve`, or
`pixi shell` first. Never suggest a bare `pip install` into the system Python here.

## Minimum config

```toml
[project]
site_name = "My site"          # only required setting
site_url  = "https://example.com"   # needed for instant navigation/previews + 404 pages
```

Everything lives under the `[project]` scope for now. If no
`markdown_extensions` are declared, Zensical enables a sensible default set
(admonition, attr_list, def_list, footnotes, md_in_html, toc, arithmatex,
details, emoji, highlight, superfences w/ mermaid, tabbed, tasklist, …) — so most
authoring features work with zero config. Declaring *any* extension config
replaces that default set, so re-declare the ones you still want.

## Authoring rules that actually bite

- **Link to `.md` files, relatively** — `[sets](model_sets.md#temporal)`, never to
  built `.html` and never absolute. Zensical rewrites them correctly for
  `use_directory_urls` and validates them at build time.
- **Four-space indentation.** Python Markdown (not CommonMark) — admonition
  bodies, nested list content, and content inside `=== "Tab"` must be indented
  four spaces or the block silently degrades to plain text.
- **Don't put both `README.md` and `index.md` in the same docs directory** — both
  map to `index.html` and the winner is undefined.
- **Page title precedence**: `nav` entry → front-matter `title:` → first `# H1` →
  filename. Give every page an `# H1`; if you set an explicit `nav` title and omit
  the H1, the H1 falls back to the filename.
- **Blank line before a fence inside a list/admonition**, and use four backticks
  when a fenced block itself contains a fence.
- Run `zensical build --strict` before calling a docs change done; link/anchor
  validation is on by default and strict mode turns those warnings into failures.

## Workflow for a docs change

1. Check whether `zensical.toml` exists. If not, this is a bootstrap — read
   `references/cnems-setup.md` first, and confirm the plan with the user before
   running `zensical new`, since it writes a GitHub Actions workflow too.
2. Write or edit pages under `docs/`. Prefer prose + one worked example over
   feature-tour lists; match the voice of the existing pages.
3. Add the page to `nav` in `zensical.toml` if an explicit nav is in use
   (otherwise it's picked up from the directory tree).
4. Build with `--strict` and fix every warning. Offer `zensical serve` if the user
   wants to look at it; don't leave a server running in the background unasked.

## Content conventions for this project

- Docs source lives in `docs/`; `site/` is build output and belongs in
  `.gitignore` — never edit or commit files under `site/`.
- The model formulation is LaTeX-heavy (see `src/models/electricity/README.md`).
  Math needs MathJax or KaTeX wired up explicitly — config in
  `references/configuration.md`. `$...$` inline, `$$...$$` display.
- API reference pages come from NumPy-style docstrings via mkdocstrings
  (`docstring_style = "numpy"`), not hand-written signatures.
- Don't migrate `old_docs/` content into the new site unless asked — CLAUDE.md
  marks it as stale.
