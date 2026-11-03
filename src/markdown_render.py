"""One Markdown renderer for every document the app shows.

Notices, release notes and a firmware's post-install guide all arrive as
Markdown written by someone else (a GitHub release body, a ``docs/install.md``
in a project's repository). Rendering them here, once, is what keeps them
looking like the rest of the app instead of like a web page: the writer's
colours are dropped in favour of the live palette, and headings, lists,
tables, code and images get the app's own typography.

Qt's Markdown reader does the parsing: headings, emphasis, lists, code, block
quotes, links, images and GitHub-style pipe tables. This module only rewrites
that output so it sits on a card — no paper background, no hard-coded colours
(the one thing a document may not decide is colour) — and adds the spacing
rules the reader does not carry.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QTextDocument

#: Inline declarations the document may not choose. Colour and background come
#: from the app; everything else (bold, italics, links, alignment) is kept.
_DROPPED_DECLARATIONS = re.compile(
    r"(?:background(?:-color)?|color)\s*:\s*[^;\"'>]+;?", re.IGNORECASE
)

_FRONT_MATTER = re.compile(r"\A\ufeff?---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def strip_front_matter(text: str) -> str:
    """Drop a leading YAML block (``---`` ... ``---``) from a Markdown file.

    Project guides often start with one (title, layout, sidebar order). It is
    metadata for the static-site generator, not words for the reader.
    """
    return _FRONT_MATTER.sub("", str(text or ""), count=1)


def render_markdown_html(
    body: str,
    *,
    text_color: str = "#000000",
    code_background: str = "rgba(127, 127, 127, 0.12)",
    muted_color: str = "#666666",
    border_color: str = "rgba(127, 127, 127, 0.35)",
    allow_images: bool = True,
) -> str:
    """Markdown ``body`` as HTML that follows the app's palette.

    ``allow_images`` is for the places that deliberately keep a reader on the
    words (the release-notes card); a guide keeps its screenshots.
    """
    text = strip_front_matter(str(body or "").strip())
    if not allow_images:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"<img[^>]*>", "", text, flags=re.IGNORECASE)

    doc = QTextDocument()
    doc.setMarkdown(text)
    html = doc.toHtml()

    # Qt's export paints a solid body and writes its own default colours.
    # Both would fight the card the document is drawn on.
    html = _DROPPED_DECLARATIONS.sub("", html)
    html = re.sub(
        r"<body[^>]*>",
        "<body>",
        html,
        count=1,
        flags=re.IGNORECASE,
    )

    css = f"""
    <style type="text/css">
    body, p, span, div, li, b, strong, em, i, a, code, pre, h1, h2, h3, h4, h5, h6 {{
        color: {text_color};
        font-size: 13px;
        line-height: 1.45;
    }}
    body {{ margin: 0; padding: 0; background: transparent; }}
    h1, h2, h3, h4, h5, h6 {{
        color: {text_color};
        font-weight: 700;
        margin-top: 12px;
        margin-bottom: 6px;
    }}
    h1 {{ font-size: 18px; }}
    h2 {{ font-size: 16px; }}
    h3 {{ font-size: 14px; }}
    h4, h5, h6 {{ font-size: 13px; }}
    p {{ margin-top: 4px; margin-bottom: 8px; }}
    ul, ol {{ margin-left: 20px; margin-top: 4px; margin-bottom: 8px; }}
    li {{ margin-bottom: 3px; }}
    a {{ color: {text_color}; font-weight: 700; text-decoration: none; }}
    img {{ margin: 4px 0; }}
    code, pre {{
        font-family: Menlo, Monaco, Consolas, "Cascadia Code", "Courier New", monospace;
        background-color: {code_background};
        color: {text_color};
        padding: 2px 4px;
        border-radius: 3px;
    }}
    pre {{ padding: 8px 10px; }}
    blockquote {{
        margin-left: 12px;
        border-left: 2px solid {border_color};
        padding-left: 10px;
        color: {muted_color};
    }}
    table {{
        border-collapse: collapse;
        margin-top: 6px;
        margin-bottom: 10px;
    }}
    th, td {{
        border: 1px solid {border_color};
        padding: 4px 8px;
        vertical-align: top;
        text-align: left;
    }}
    th {{
        font-weight: 700;
        background-color: {code_background};
    }}
    hr {{
        border: none;
        border-top: 1px solid {border_color};
        margin-top: 10px;
        margin-bottom: 10px;
    }}
    </style>
    """
    if "<head>" in html:
        return html.replace("<head>", f"<head>{css}", 1)
    return f"<html><head>{css}</head><body>{html}</body></html>"
