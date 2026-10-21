from __future__ import annotations

import pytest

import notion_writer as nw


def _text_of(block: dict) -> str:
    body = block[block["type"]]
    return "".join(item["text"]["content"] for item in body["rich_text"])


def test_rich_text_splits_long_text_without_losing_content():
    text = "x" * 4500
    items = nw._rich_text(text)
    assert [len(i["text"]["content"]) for i in items] == [2000, 2000, 500]
    assert "".join(i["text"]["content"] for i in items) == text


def test_rich_text_truncates_beyond_item_cap():
    items = nw._rich_text("y" * (2000 * 101))
    assert len(items) == nw._NOTION_MAX_RICH_TEXT_ITEMS


def test_paragraph_splits_into_multiple_blocks():
    blocks = nw._paragraph("z" * 2500)
    assert len(blocks) == 2 and all(b["type"] == "paragraph" for b in blocks)


@pytest.mark.parametrize(
    "lang,expected",
    [("en", "Summary"), ("zh", "摘要"), ("bilingual", "Summary / 摘要"), ("unknown", "Summary / 摘要")],
)
def test_section_headers_follow_output_language(lang, expected):
    assert nw._section_header("summary", lang) == expected


SAMPLE_NOTES = {
    "_language": "en",
    "tldr": "Ship Friday.",
    "summary": "We agreed to ship.",
    "discussion": [
        {"topic": "Release", "details": ["Marco owns it", "Staging is green"]},
        "flat string section",
    ],
    "key_points": ["Ship Friday"],
    "decisions": ["Go"],
    "action_items": [
        {"owner": "Marco", "task": "Tag the release", "due": "2026-10-30"},
        {"owner": "Unassigned", "task": "Update docs", "due": None},
    ],
    "open_questions": [],
    "risks": ["Rollback untested"],
}


def test_build_content_blocks_structure():
    blocks = nw._build_content_blocks(SAMPLE_NOTES, transcript_text=None)
    types = [b["type"] for b in blocks]
    assert types[0] == "callout" and _text_of(blocks[0]) == "Ship Friday."
    headings = [_text_of(b) for b in blocks if b["type"] == "heading_2"]
    assert headings == ["Summary", "Detailed Discussion", "Key Discussion Points",
                        "Decisions Made", "Action Items", "Risks & Follow-ups"]  # empty open_questions omitted
    assert "heading_3" in types
    todos = [_text_of(b) for b in blocks if b["type"] == "to_do"]
    assert todos == ["[Marco] Tag the release  (due: 2026-10-30)", "[Unassigned] Update docs"]
    bullets = [_text_of(b) for b in blocks if b["type"] == "bulleted_list_item"]
    assert "flat string section" in bullets


def test_long_transcript_is_split_across_toggles():
    transcript = "t" * (2000 * 150)  # 150 paragraph blocks > 100 per toggle
    blocks = nw._build_content_blocks({"_language": "zh"}, transcript_text=transcript)
    toggles = [b for b in blocks if b["type"] == "toggle"]
    assert len(toggles) == 2
    assert _text_of(toggles[0]).endswith("1/2")
    assert [len(t["toggle"]["children"]) for t in toggles] == [100, 50]


def test_build_properties():
    notes = {"title": "T" * 300, "tags": [f"tag{i}" for i in range(12)]}
    props = nw._build_properties(notes, "2026-10-21", "Offline", "Proj", {})
    assert len(props["Title"]["title"][0]["text"]["content"]) == 255
    assert props["Date"]["date"]["start"] == "2026-10-21"
    assert props["Meeting Type"]["select"]["name"] == "Offline"
    assert props["Project"]["rich_text"][0]["text"]["content"] == "Proj"
    assert len(props["Tags"]["multi_select"]) == 10


def test_build_properties_honours_custom_property_names():
    props = nw._build_properties({"title": "x"}, "2026-10-21", "Online", None,
                                 {"title": "Name", "date": "When"})
    assert "Name" in props and "When" in props and "Project" not in props


# ── write_to_notion batching with a fake client ───────────────────────────────

class _FakePages:
    def __init__(self, log):
        self.log = log

    def create(self, **kwargs):
        self.log.append(("create", len(kwargs["children"]), kwargs["parent"]))
        return {"id": "page-1", "url": "https://notion.so/page-1"}


class _FakeChildren:
    def __init__(self, log):
        self.log = log

    def append(self, block_id, children):
        self.log.append(("append", len(children), block_id))


class _FakeBlocks:
    def __init__(self, log):
        self.children = _FakeChildren(log)


def test_write_to_notion_batches_beyond_100_blocks(monkeypatch, base_cfg):
    import notion_client

    log: list = []

    class FakeClient:
        def __init__(self, auth):
            assert auth == "secret_test"
            self.pages = _FakePages(log)
            self.blocks = _FakeBlocks(log)

    monkeypatch.setattr(notion_client, "Client", FakeClient)
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")

    notes = {"title": "Big", "_language": "en", "key_points": [f"p{i}" for i in range(250)]}
    url = nw.write_to_notion(notes, base_cfg, meeting_date="2026-10-21")

    assert url == "https://notion.so/page-1"
    assert log[0] == ("create", 100, {"database_id": "test-db"})
    # 250 bullets + heading + divider = 252 blocks → 100 + 100 + 52
    assert log[1:] == [("append", 100, "page-1"), ("append", 52, "page-1")]


def test_write_to_notion_requires_database_id(monkeypatch, base_cfg):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    base_cfg["notion"]["database_id"] = ""
    with pytest.raises(ValueError, match="database_id"):
        nw.write_to_notion({"title": "x"}, base_cfg)
