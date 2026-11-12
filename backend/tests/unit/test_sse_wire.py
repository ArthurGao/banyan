"""The SSE wire format (design §9): one encoder per event."""

from banyan.api.sse import _wire
from banyan.pipeline.events import CitationEvent, ToolEvent
from banyan.pipeline.types import Citation


def test_a_citation_goes_out_as_a_citation_event() -> None:
    name, data = _wire(
        CitationEvent(Citation(kind="kb", entry_id="e1", item_id="e1", retrieval_id="r", title="T"))
    )
    assert name == "citation"
    assert data.model_dump(exclude_none=True) == {"kind": "kb", "entry_id": "e1", "title": "T"}


def test_a_tool_call_goes_out_as_a_tool_event() -> None:
    name, data = _wire(ToolEvent("web_fetch", url="https://example.org/a"))
    assert (name, data.model_dump()) == (
        "tool",
        {"name": "web_fetch", "query": None, "url": "https://example.org/a"},
    )
