"""The study guide renderer (design §10.2): safe, offline, deterministic."""

import re
from datetime import UTC, datetime
from html.parser import HTMLParser

from banyan.export import ExportNode, render_study_guide
from banyan.export.render import markdown

AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
NOTES = {
    "summary": "Pods share a network namespace; $e^{i\\pi}+1=0$.",
    "key_points": ["One IP per pod", "Use `kubectl get pods`"],
    "examples": [{"title": "Probe", "body_md": "```python\nprint('hi')\n```"}],
    "pitfalls": ["Pod IPs change"],
    "branch_highlights": [{"branch_id": "b", "question": "Why?", "answer_md": "Because."}],
}


def guide(**changes: object) -> str:
    args: dict[str, object] = {
        "topic_title": "Kubernetes", "goal": "CKA",
        "nodes": [ExportNode("Pods", 0, "units", NOTES, 3),
                  ExportNode("Pod IPs", 1, None, None, 0)],
        "sources": {"llm": 3, "kb": 1}, "generated_at": AT,
    }  # fmt: skip
    return render_study_guide(**(args | changes))  # type: ignore[arg-type]


FETCHING_TAGS = frozenset(
    {
        "link",
        "img",
        "iframe",
        "object",
        "embed",
        "video",
        "audio",
        "source",
        "frame",
    }
)


class _Refs(HTMLParser):
    """Anything that would make the browser fetch something."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in FETCHING_TAGS:
            self.problems.append(f"<{tag}>")
        for name, value in attrs:
            if name in {"src", "srcset", "poster", "data"} and not (value or "").startswith(
                "data:"
            ):
                self.problems.append(f"{tag} {name}={value}")


def external_references(html: str) -> list[str]:
    refs = _Refs()
    refs.feed(html)
    css_urls = [
        u for u in re.findall(r"url\(([^)]*)\)", html) if not u.strip("'\"").startswith("data:")
    ]
    return refs.problems + css_urls + (["@import"] if "@import" in html else [])


def test_the_guide_needs_no_network() -> None:
    html = guide()
    assert external_references(html) == []
    assert len(re.findall(r"data:font/woff2;base64,", html)) == 20  # every KaTeX font inlined
    assert "fonts/KaTeX" not in html


def test_the_checker_catches_what_it_should() -> None:
    bad = '<img src="https://x/y.png"><link rel="stylesheet" href="https://x"><style>a{b:url(x.woff)}</style>'
    found = external_references(bad)
    assert "<img>" in found and "<link>" in found and "x.woff" in found


def test_light_and_dark() -> None:
    html = guide()
    assert html.count("@media (prefers-color-scheme: dark)") >= 2  # page colours and code colours


def test_it_is_deterministic_apart_from_the_generation_time() -> None:
    assert guide() == guide()
    later = guide(generated_at=datetime(2027, 1, 1, tzinfo=UTC))
    diff = [(a, b) for a, b in zip(guide().splitlines(), later.splitlines(), strict=True) if a != b]
    assert len(diff) == 1 and "Generated" in diff[0][0]


def test_structure_follows_the_outline_and_marks_nodes_without_notes() -> None:
    html = guide()
    assert re.findall(r"<h([234])>([^<]+)</h\1>", html) == [("2", "Pods"), ("3", "Pod IPs")]
    assert html.index('id="n0"') < html.index('id="n1"')
    assert "No notes yet." in html.split('id="n1"')[1]
    assert '<details class="highlight"><summary>Why?</summary>' in html
    assert "Notes versions: Pods v3, Pod IPs v0" in html
    assert "Answers by source: kb 25%, llm 75%" in html


def test_math_code_and_tasks_render() -> None:
    html = guide()
    assert '<span class="math inline">e^{i\\pi}+1=0</span>' in html
    assert '<span class="nb">print</span>' in html  # Pygments highlighted the example
    assert "katex.render(" in html


def test_nothing_in_a_note_can_run() -> None:
    hostile = (
        "<script>alert(1)</script> <img src=x onerror=alert(2)> "
        '[click](javascript:alert(3)) <a href="https://ok.example">ok</a>'
    )
    out = markdown(hostile)
    assert "<script" not in out and "<img" not in out and 'href="javascript' not in out
    assert '<a href="https://ok.example"' not in out  # raw HTML links are text too
    assert "&lt;script&gt;" in out  # shown as text, not dropped silently
    html = guide(topic_title="<script>x()</script>", nodes=[ExportNode("T", 0, None, {
        "summary": hostile, "key_points": [hostile], "examples": [], "pitfalls": [],
        "branch_highlights": []}, 1)])  # fmt: skip
    assert "<script>x()</script>" not in html and "<script>alert" not in html
    assert html.count("<script>") == 2  # KaTeX and the math loop, nothing else
