"""Rendering (design §10.2). Deterministic: the same notes, template version and assets give the
same bytes, except the generation time in the footer, which the caller passes in."""

import base64
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import nh3
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

TEMPLATE_VERSION = "1"
HERE = Path(__file__).parent
KATEX = HERE / "assets" / "katex"

# What notes Markdown may become (nh3 allow-list). No images, iframes, styles or scripts: the file
# must open offline and nothing in a note may run.
TAGS = {
    "p", "br", "hr", "h3", "h4", "h5", "h6", "ul", "ol", "li", "pre", "code", "span", "div",
    "blockquote", "em", "strong", "del", "a", "table", "thead", "tbody", "tr", "th", "td", "input",
}  # fmt: skip
ATTRIBUTES = {
    "a": {"href", "title"},
    "span": {"class"},
    "div": {"class"},
    "code": {"class"},
    "pre": {"class"},
    "ul": {"class"},
    "li": {"class"},
    "input": {"class", "type", "checked", "disabled"},
}


# `src:url(fonts/X.woff2) format("woff2"),url(...) format("woff"),url(...) format("truetype")`
FONT_SOURCES = re.compile(
    r'src:url\(fonts/([\w-]+\.woff2)\) format\("woff2"\)(?:,url\([^)]*\) format\("[a-z]+"\))*'
)


@dataclass(frozen=True)
class ExportNode:
    title: str
    depth: int
    summary: str | None
    notes: dict[str, Any] | None  # NotesContent as stored; None when the node has none yet
    version: int


def render_study_guide(
    *,
    topic_title: str,
    goal: str | None,
    nodes: list[ExportNode],
    sources: dict[str, int],
    generated_at: datetime,
) -> str:
    template = _env().get_template("study_guide.html.j2")
    total = sum(sources.values()) or 1
    return template.render(
        topic_title=topic_title,
        goal=goal,
        nodes=[(f"n{i}", n) for i, n in enumerate(nodes)],
        sources=[(k, round(100 * v / total)) for k, v in sorted(sources.items()) if v],
        generated_at=generated_at.isoformat(timespec="seconds"),
        template_version=TEMPLATE_VERSION,
        katex_version=_katex_version(),
        assets=_assets(),
    )


def markdown(text: str) -> str:
    """Notes Markdown to safe HTML: raw HTML is escaped by the parser, then nh3 removes anything
    outside the allow-list (links only to http, https, mailto)."""
    html = _md().render(text or "")
    return nh3.clean(
        html, tags=TAGS, attributes=ATTRIBUTES, url_schemes={"http", "https", "mailto"}
    )


@cache
def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "highlight": _highlight})
    return md.enable("table").enable("strikethrough").use(dollarmath_plugin).use(tasklists_plugin)


def _highlight(code: str, lang: str, _attrs: str) -> str:
    """Pygments spans for a known language; "" lets markdown-it escape the code as plain text."""
    if not lang:
        return ""
    try:
        return str(highlight(code, get_lexer_by_name(lang), HtmlFormatter(nowrap=True)))
    except ClassNotFound:
        return ""


@cache
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["markdown"] = markdown
    return env


@cache
def _assets() -> dict[str, str]:
    return {
        "katex_css": _inline_fonts((KATEX / "katex.min.css").read_text()),
        # A literal "</script" inside the library would end the inline <script> early.
        "katex_js": (KATEX / "katex.min.js").read_text().replace("</script", "<\\/script"),
        # types-pygments leaves get_style_defs untyped.
        "code_light": HtmlFormatter(style="friendly").get_style_defs("pre code"),  # type: ignore[no-untyped-call]
        "code_dark": HtmlFormatter(style="monokai").get_style_defs("pre code"),  # type: ignore[no-untyped-call]
    }


def _inline_fonts(css: str) -> str:
    """woff2 fonts as data: URIs; the woff and ttf fallbacks are dropped (every browser that
    runs the export reads woff2), so the CSS references nothing outside the file."""

    def font(m: re.Match[str]) -> str:
        data = base64.b64encode((KATEX / "fonts" / m.group(1)).read_bytes()).decode()
        return f'src:url(data:font/woff2;base64,{data}) format("woff2")'

    return FONT_SOURCES.sub(font, css)


@cache
def _katex_version() -> str:
    return (KATEX / "VERSION").read_text().splitlines()[0]
