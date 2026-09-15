# `zensical.toml` reference

All settings currently live under the `[project]` scope. Zensical can also read
`mkdocs.yml`, but new config goes in TOML. The upstream docs show every option in
both formats: <https://zensical.org/docs/setup/basics/>.

## Core project settings

```toml
[project]
site_name = "C-NEMS Models"          # required
site_url = "https://example.org/"    # needed for instant nav/previews, 404, sitemap
site_description = "..."             # default meta description
site_author = "..."
copyright = "&copy; 2026 ..."        # HTML allowed; rendered in the footer
docs_dir = "docs"                    # relative to this file; cannot be "."
site_dir = "site"                    # build output
use_directory_urls = true            # false -> /page.html URLs
dev_addr = "localhost:8000"
watch = ["src", "fragments"]         # extra paths that trigger a full rebuild
extra_css = ["stylesheets/extra.css"]
extra_javascript = ["javascripts/mathjax.js"]

[project.extra]                       # arbitrary values available to templates
key = "value"
```

`extra_javascript` entries can be tables when you need attributes:

```toml
[[project.extra_javascript]]
path = "javascripts/extra.js"
type = "module"   # or async = true / defer = true
```

Not supported (yet) from `mkdocs.yml`: `remote_branch`, `remote_name`,
`exclude_docs`, `draft_docs`, `not_in_nav`, `hooks`.

## Navigation

Omit `nav` and the sidebar is derived from the directory tree. Explicit form:

```toml
[project]
nav = [
  { "Home" = "index.md" },
  { "Guide" = [
      "guide/index.md",          # section index page (needs navigation.indexes)
      { "Configuration" = "guide/config.md" },
  ]},
  { "Repository" = "https://github.com/..." },   # unresolvable path => external link
]
```

## Theme

```toml
[project.theme]
variant = "modern"        # or "classic" = exact Material for MkDocs look
language = "en"
custom_dir = "overrides"  # template overrides
features = [
  "navigation.instant", "navigation.instant.progress", "navigation.tracking",
  "navigation.tabs", "navigation.sections", "navigation.expand",
  "navigation.indexes", "navigation.path", "navigation.prune", "navigation.top",
  "navigation.footer",
  "toc.follow", "toc.integrate",
  "search.highlight",
  "content.code.copy", "content.code.select", "content.code.annotate",
  "content.tabs.link", "content.tooltips", "content.footnote.tooltips",
  "content.action.edit", "content.action.view",
  "header.autohide", "announce.dismiss",
]

[project.theme.icon]
repo = "fontawesome/brands/git-alt"
edit = "material/pencil"
```

Incompatible pairs: `navigation.prune` + `navigation.expand`;
`navigation.indexes` + `toc.integrate`.

### Palette

Single palette:

```toml
[project.theme.palette]
scheme = "default"     # "slate" for dark
primary = "indigo"
accent = "indigo"
```

Light/dark toggle following system preference (array of tables):

```toml
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

## Repository links

```toml
[project]
repo_url = "https://github.com/org/repo"
repo_name = "org/repo"
edit_uri = "edit/main/docs/"    # powers content.action.edit
```

## Markdown extensions

If you declare **nothing**, Zensical enables this default set. Declaring any
extension config replaces the defaults, so copy forward what you still need:

```toml
[project.markdown_extensions.abbr]
[project.markdown_extensions.admonition]
[project.markdown_extensions.attr_list]
[project.markdown_extensions.def_list]
[project.markdown_extensions.footnotes]
[project.markdown_extensions.md_in_html]
[project.markdown_extensions.toc]
permalink = true
[project.markdown_extensions.pymdownx.arithmatex]
generic = true
[project.markdown_extensions.pymdownx.betterem]
[project.markdown_extensions.pymdownx.caret]
[project.markdown_extensions.pymdownx.details]
[project.markdown_extensions.pymdownx.emoji]
emoji_generator = "zensical.extensions.emoji.to_svg"
emoji_index = "zensical.extensions.emoji.twemoji"
[project.markdown_extensions.pymdownx.highlight]
anchor_linenums = true
line_spans = "__span"
pygments_lang_class = true
[project.markdown_extensions.pymdownx.inlinehilite]
[project.markdown_extensions.pymdownx.keys]
[project.markdown_extensions.pymdownx.magiclink]
[project.markdown_extensions.pymdownx.mark]
[project.markdown_extensions.pymdownx.smartsymbols]
[project.markdown_extensions.pymdownx.superfences]
custom_fences = [
  { name = "mermaid", class = "mermaid", format = "pymdownx.superfences.fence_code_format" }
]
[project.markdown_extensions.pymdownx.tabbed]
alternate_style = true
combine_header_slug = true
[project.markdown_extensions.pymdownx.tasklist]
custom_checkbox = true
[project.markdown_extensions.pymdownx.tilde]
```

Others worth adding: `tables`, `pymdownx.snippets` (file embedding, glossary
auto-append), `pymdownx.blocks.caption` (figure captions).
`markdown_extensions = {}` turns the defaults off entirely.

## Math

Arithmatex only marks the math; a browser library renders it.

MathJax — `docs/javascripts/mathjax.js`:

```js
window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [["\\[", "\\]"]],
    processEscapes: true,
    processEnvironments: true
  },
  options: { ignoreHtmlClass: ".*|", processHtmlClass: "arithmatex" }
};

document$.subscribe(() => {          // keeps math working with instant navigation
  MathJax.startup.output.clearCache()
  MathJax.typesetClear()
  MathJax.texReset()
  MathJax.typesetPromise()
})
```

```toml
[project]
extra_javascript = [
  "javascripts/mathjax.js",
  "https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js",
]

[project.markdown_extensions.pymdownx.arithmatex]
generic = true
```

KaTeX is the faster alternative (`renderMathInElement` inside `document$.subscribe`,
plus `katex.min.js`, `auto-render.min.js`, `katex.min.css`) — use it only if
render speed matters more than LaTeX coverage.

## Validation

On by default for links and anchors; `--strict` promotes warnings to errors.

```toml
[project.validation]
invalid_links = true
invalid_link_anchors = true
unresolved_references = false
unused_definitions = false
# ... unresolved_footnotes, unused_footnotes, shadowed_definitions, shadowed_footnotes
```

`validation = false` under `[project]` disables all checks.

## Plugins

```toml
[project.plugins.offline]          # self-contained build for filesystem browsing
```

Offline mode forces `use_directory_urls = false`; don't set `site_url` for it.

### mkdocstrings (API reference from docstrings)

Not bundled — install `mkdocstrings-python` separately. Support is preliminary
(backlinks missing).

```toml
[project.plugins.mkdocstrings.handlers.python]
paths = ["src"]
inventories = ["https://docs.python.org/3/objects.inv"]

[project.plugins.mkdocstrings.handlers.python.options]
docstring_style = "numpy"
inherited_members = true
show_source = false
```

Then in a page: `::: package.module.Class`. Source paths outside the project
directory are not watched for changes.

## Other extensions

- **GLightbox** — image zoom/lightbox.
- **Macros** (`zensical.extensions.macros`) — Jinja variables/macros in Markdown;
  its `module`, `modules`, `include_yaml`, `include_dir` paths are auto-watched.
- **markdown-exec** — execute code blocks at build time.

## Publishing

GitHub Pages (also generated by `zensical new` at `.github/workflows/docs.yml`;
repo Pages source must be set to "GitHub Actions"):

```yaml
name: Documentation
on:
  push:
    branches: [main]
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/configure-pages@v6
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: 3.x
      - run: pip install zensical
      - run: zensical build --clean
      - uses: actions/upload-pages-artifact@v5
        with:
          path: site
      - uses: actions/deploy-pages@v5
        id: deployment
```

GitLab Pages: same idea in `.gitlab-ci.yml` with `pages.publish: site`.
Caching on CI is not recommended yet — use `--clean`.
