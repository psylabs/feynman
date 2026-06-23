"""Render a Markdown file to a standalone HTML page with Mermaid support.

Used by ``.github/workflows/docs.yml`` to publish ``docs/architecture.md``
to GitHub Pages so the Mermaid diagrams render alongside the pdoc API
reference. The generated page is fully static — Mermaid is loaded from a
CDN at view time, no build-time JS toolchain required.

Usage::

    python tools/render_docs.py <input.md> <output.html> [--title TITLE]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import markdown

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      max-width: 920px;
      margin: 2rem auto;
      padding: 0 1.25rem 4rem;
      background: #0f1115;
      color: #d6d8de;
      line-height: 1.55;
    }}
    a {{ color: #8ab4f8; }}
    h1, h2, h3 {{ color: #f0f2f6; margin-top: 2rem; }}
    code {{
      background: #1a1d23;
      padding: 0.1rem 0.35rem;
      border-radius: 3px;
      font-size: 0.92em;
    }}
    pre {{
      background: #14161b;
      border: 1px solid #2a2d33;
      border-radius: 6px;
      padding: 1rem;
      overflow-x: auto;
    }}
    pre code {{ background: transparent; padding: 0; }}
    pre.mermaid {{
      background: #f7f7f9;
      color: #111;
      border: none;
      padding: 1rem;
    }}
    table {{ border-collapse: collapse; margin: 1rem 0; }}
    th, td {{ border: 1px solid #2a2d33; padding: 0.4rem 0.7rem; }}
    nav.topnav {{ font-size: 0.92em; margin-bottom: 1rem; }}
    nav.topnav a {{ margin-right: 1rem; }}
  </style>
</head>
<body>
<nav class="topnav">
  <a href="../">API reference</a>
  <a href="https://github.com/psylabs/feynman">Source</a>
</nav>
{content}
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
</script>
</body>
</html>
"""


def render_text(md_text: str, title: str | None) -> str:
    # Replace ```mermaid fenced blocks with <pre class="mermaid"> so the
    # client-side Mermaid runtime picks them up. Done before the markdown
    # renderer touches the text so it doesn't try to highlight the source.
    def to_mermaid(match: re.Match) -> str:
        body = match.group(1)
        return f'<pre class="mermaid">\n{body}\n</pre>'

    md_text = re.sub(
        r"```mermaid\n(.*?)\n```",
        to_mermaid,
        md_text,
        flags=re.DOTALL,
    )

    html = markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc"],
    )
    page_title = title or "Feynman docs"
    return TEMPLATE.format(title=page_title, content=html)


def render(md_path: Path, title: str | None) -> str:
    page_title = title or md_path.stem.replace("-", " ").replace("_", " ").title()
    return render_text(md_path.read_text(), page_title)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render(src, args.title))
    print(f"rendered {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
