"""Code decides what survives a notes update (design §10.1), not the model."""

from banyan.services.notes import Example, Highlight, NotesContent, merge

BEFORE = NotesContent(
    summary="Old summary",
    key_points=["Pods share a network namespace"],
    examples=[Example(title="curl a pod", body_md="old")],
    pitfalls=["Pod IPs change"],
    branch_highlights=[Highlight(branch_id="b1", question="Q1", answer_md="old answer")],
)


def test_nothing_already_noted_is_dropped_and_repeats_are_not_added() -> None:
    proposed = NotesContent(
        summary="New summary",
        key_points=["pods share a network namespace ", "Services give a stable IP"],
        examples=[
            Example(title="curl a pod", body_md="rewritten"),
            Example(title="NodePort", body_md="n"),
        ],
        pitfalls=[],
        branch_highlights=[],
    )
    merged = merge(BEFORE, proposed)
    assert merged.summary == "New summary"
    assert merged.key_points == ["Pods share a network namespace", "Services give a stable IP"]
    assert [(e.title, e.body_md) for e in merged.examples] == [
        ("curl a pod", "old"),
        ("NodePort", "n"),
    ]
    assert merged.pitfalls == ["Pod IPs change"]
    assert [h.branch_id for h in merged.branch_highlights] == ["b1"]


def test_a_newer_highlight_replaces_the_same_branchs_and_an_empty_summary_keeps_the_old() -> None:
    proposed = NotesContent(
        summary="",
        branch_highlights=[
            Highlight(branch_id="b1", question="Q1", answer_md="new answer"),
            Highlight(branch_id="b2", question="Q2", answer_md="a2"),
        ],
    )
    merged = merge(BEFORE, proposed)
    assert merged.summary == "Old summary"
    assert [(h.branch_id, h.answer_md) for h in merged.branch_highlights] == [
        ("b1", "new answer"), ("b2", "a2"),
    ]  # fmt: skip
