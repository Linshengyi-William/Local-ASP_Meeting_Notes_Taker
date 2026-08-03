"""
notion_writer.py — Write structured meeting notes to a Notion database page.

Expects the notes dict produced by generate_notes.py and creates a new page
in the configured Notion database with:
  - Database properties: Title, Date, Project, Meeting Type, Status, Tags
  - Page content blocks: TL;DR, Summary, Detailed Discussion, Key Points,
    Decisions, Action Items, Open Questions, Risks, full Transcript
    (collapsed toggle)

Pages longer than Notion's 100-block request limit are created in batches.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum characters per Notion rich text block
_NOTION_MAX_BLOCK_TEXT = 2000

# Notion rejects requests carrying more than 100 child blocks, so pages longer
# than that must be created with the first batch and extended with the rest.
_NOTION_MAX_BLOCKS_PER_REQUEST = 100


# ── Block builders ────────────────────────────────────────────────────────────

def _heading(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _paragraph(text: str) -> list[dict]:
    """Split long text into multiple paragraph blocks (Notion 2000-char limit)."""
    blocks = []
    for i in range(0, max(len(text), 1), _NOTION_MAX_BLOCK_TEXT):
        chunk = text[i: i + _NOTION_MAX_BLOCK_TEXT]
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            },
        })
    return blocks


def _bullet(text: str) -> dict:
    # Truncate individual bullet items to Notion limit
    text = text[:_NOTION_MAX_BLOCK_TEXT]
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _todo(text: str, checked: bool = False) -> dict:
    text = text[:_NOTION_MAX_BLOCK_TEXT]
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "checked": checked,
        },
    }


def _callout(text: str, emoji: str = "💡") -> dict:
    text = text[:_NOTION_MAX_BLOCK_TEXT]
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _toggle(title: str, children: list[dict]) -> dict:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "children": children,
        },
    }


# ── Section headers (localized by output language) ────────────────────────────
# Each key maps to {en, zh, bilingual} heading text. bilingual is the default
# and keeps the original "English / 中文" style.
_SECTION_HEADERS = {
    "summary":        {"en": "Summary",                "zh": "摘要",        "bilingual": "Summary / 摘要"},
    "discussion":     {"en": "Detailed Discussion",    "zh": "详细讨论记录", "bilingual": "Detailed Discussion / 详细讨论记录"},
    "key_points":     {"en": "Key Discussion Points",  "zh": "主要讨论点",   "bilingual": "Key Discussion Points / 主要讨论点"},
    "decisions":      {"en": "Decisions Made",         "zh": "决策",        "bilingual": "Decisions Made / 决策"},
    "action_items":   {"en": "Action Items",           "zh": "待办事项",     "bilingual": "Action Items / 待办事项"},
    "open_questions": {"en": "Open Questions",         "zh": "待解决问题",   "bilingual": "Open Questions / 待解决问题"},
    "risks":          {"en": "Risks & Follow-ups",     "zh": "风险与跟进",   "bilingual": "Risks & Follow-ups / 风险与跟进"},
    "transcript":     {"en": "Full Transcript (click to expand)",
                       "zh": "完整转录（点击展开）",
                       "bilingual": "Full Transcript / 原始转录 (click to expand)"},
}


def _section_header(key: str, lang: str) -> str:
    variants = _SECTION_HEADERS[key]
    return variants.get(lang, variants["bilingual"])


# ── Notes → Notion blocks ─────────────────────────────────────────────────────

def _build_content_blocks(notes: dict, transcript_text: Optional[str]) -> list[dict]:
    blocks: list[dict] = []
    lang = (notes.get("_language") or "bilingual").lower()

    def header(key: str) -> str:
        return _section_header(key, lang)

    # TL;DR callout — always first if present
    if notes.get("tldr"):
        blocks.append(_callout(notes["tldr"], emoji="⚡"))
        blocks.append(_divider())

    # Summary
    if notes.get("summary"):
        blocks.append(_heading(header("summary"), level=2))
        blocks.extend(_paragraph(notes["summary"]))
        blocks.append(_divider())

    # Detailed Discussion — the chronological walkthrough of the meeting.
    # Each topic becomes an H3 with its detail points beneath it.
    if notes.get("discussion"):
        blocks.append(_heading(header("discussion"), level=2))
        for section in notes["discussion"]:
            if isinstance(section, dict):
                topic = str(section.get("topic", "")).strip()
                details = section.get("details") or []
                if topic:
                    blocks.append(_heading(topic, level=3))
                if isinstance(details, str):
                    details = [details]
                for detail in details:
                    blocks.append(_bullet(str(detail)))
            else:
                # Model returned a flat list of strings instead of objects
                blocks.append(_bullet(str(section)))
        blocks.append(_divider())

    # Key Discussion Points
    if notes.get("key_points"):
        blocks.append(_heading(header("key_points"), level=2))
        for item in notes["key_points"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Decisions
    if notes.get("decisions"):
        blocks.append(_heading(header("decisions"), level=2))
        for item in notes["decisions"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Action Items
    if notes.get("action_items"):
        blocks.append(_heading(header("action_items"), level=2))
        for ai in notes["action_items"]:
            if isinstance(ai, dict):
                owner = ai.get("owner", "—")
                task = ai.get("task", str(ai))
                due = ai.get("due")
                label = f"[{owner}] {task}"
                if due:
                    label += f"  (due: {due})"
            else:
                label = str(ai)
            blocks.append(_todo(label))
        blocks.append(_divider())

    # Open Questions
    if notes.get("open_questions"):
        blocks.append(_heading(header("open_questions"), level=2))
        for item in notes["open_questions"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Risks
    if notes.get("risks"):
        blocks.append(_heading(header("risks"), level=2))
        for item in notes["risks"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Full transcript (collapsed toggle so it doesn't dominate the page)
    if transcript_text:
        flat_chunks = [
            block
            for i in range(0, len(transcript_text), _NOTION_MAX_BLOCK_TEXT)
            for block in _paragraph(transcript_text[i: i + _NOTION_MAX_BLOCK_TEXT])
        ]
        # A toggle's children are also capped at 100, so a very long transcript
        # is split across several toggles rather than being rejected.
        title = header("transcript")
        parts = [
            flat_chunks[i: i + _NOTION_MAX_BLOCKS_PER_REQUEST]
            for i in range(0, len(flat_chunks), _NOTION_MAX_BLOCKS_PER_REQUEST)
        ]
        for idx, part in enumerate(parts, start=1):
            label = title if len(parts) == 1 else f"{title} — {idx}/{len(parts)}"
            blocks.append(_toggle(label, part))

    return blocks


# ── Properties builder ────────────────────────────────────────────────────────

def _build_properties(
    notes: dict,
    meeting_date: str,
    meeting_type: str,
    project: Optional[str],
    prop_names: dict,
) -> dict:
    title_prop = prop_names.get("title", "Title")
    date_prop = prop_names.get("date", "Date")
    project_prop = prop_names.get("project", "Project")
    type_prop = prop_names.get("meeting_type", "Meeting Type")
    status_prop = prop_names.get("status", "Status")
    tags_prop = prop_names.get("tags", "Tags")

    title_text = notes.get("title", "Meeting Notes")

    props: dict = {
        title_prop: {
            "title": [{"type": "text", "text": {"content": title_text[:255]}}]
        },
        date_prop: {
            "date": {"start": meeting_date}
        },
        type_prop: {
            "select": {"name": meeting_type}
        },
        status_prop: {
            "select": {"name": "Done"}
        },
    }

    if project:
        props[project_prop] = {
            "rich_text": [{"type": "text", "text": {"content": project[:255]}}]
        }

    tags = notes.get("tags", [])
    if tags:
        props[tags_prop] = {
            "multi_select": [{"name": str(t)[:100]} for t in tags[:10]]
        }

    return props


# ── Main writer ───────────────────────────────────────────────────────────────

def write_to_notion(
    notes: dict,
    cfg: dict,
    transcript_text: Optional[str] = None,
    meeting_date: Optional[str] = None,
    meeting_type: str = "Online",
    project: Optional[str] = None,
) -> str:
    """
    Create a new Notion page for the meeting and return its URL.

    Args:
        notes:            Structured notes dict from generate_notes.py
        cfg:              Full config dict
        transcript_text:  Optional full cleaned transcript (shown in a toggle block)
        meeting_date:     ISO date string YYYY-MM-DD (defaults to today)
        meeting_type:     "Online" or "Offline"
        project:          Optional project name string
    """
    from notion_client import Client

    notion_token = os.environ.get("NOTION_TOKEN")
    if not notion_token:
        raise EnvironmentError(
            "NOTION_TOKEN is not set. Add it to your .env file."
        )

    from utils import retry_with_backoff_predicate, _notion_is_retryable

    ncfg = cfg.get("notion", {})
    database_id = ncfg.get("database_id", "")
    if not database_id:
        raise ValueError(
            "notion.database_id is empty in config/config.yaml. "
            "Set it to the ID of your Notion meeting notes database."
        )
    max_retries = int(ncfg.get("max_retries", 3))

    prop_names = ncfg.get("properties", {})
    if not meeting_date:
        meeting_date = date.today().isoformat()

    client = Client(auth=notion_token)

    properties = _build_properties(notes, meeting_date, meeting_type, project, prop_names)
    content_blocks = _build_content_blocks(notes, transcript_text)

    logger.info("Creating Notion page: '%s' ...", notes.get("title", "Meeting Notes"))

    # Detailed notes routinely exceed Notion's 100-block request limit, so send
    # the first batch with the page itself and append the remainder.
    first_batch = content_blocks[:_NOTION_MAX_BLOCKS_PER_REQUEST]
    remaining = content_blocks[_NOTION_MAX_BLOCKS_PER_REQUEST:]

    def _do_create():
        return client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
            children=first_batch,
        )

    response = retry_with_backoff_predicate(
        _do_create,
        max_retries=max_retries,
        base_delay=2.0,
        is_retryable=_notion_is_retryable,
    )

    page_id = response.get("id")
    if remaining and page_id:
        total_batches = -(-len(remaining) // _NOTION_MAX_BLOCKS_PER_REQUEST)
        logger.info(
            "Appending %d further block(s) in %d batch(es) ...",
            len(remaining), total_batches,
        )
        for i in range(0, len(remaining), _NOTION_MAX_BLOCKS_PER_REQUEST):
            batch = remaining[i: i + _NOTION_MAX_BLOCKS_PER_REQUEST]

            def _do_append(batch=batch):
                return client.blocks.children.append(block_id=page_id, children=batch)

            retry_with_backoff_predicate(
                _do_append,
                max_retries=max_retries,
                base_delay=2.0,
                is_retryable=_notion_is_retryable,
            )

    page_url = response.get("url", "")
    logger.info("Notion page created: %s", page_url)
    return page_url


# ── CLI helper ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import json
    import yaml
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Push meeting notes JSON to Notion.")
    parser.add_argument("notes_json", help="Path to the _notes.json file")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--transcript", default=None, help="Path to cleaned transcript .txt")
    parser.add_argument("--date", default=None, help="ISO date YYYY-MM-DD")
    parser.add_argument("--type", default="Online", choices=["Online", "Offline"])
    parser.add_argument("--project", default=None)
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    with open(args.notes_json, encoding="utf-8") as f:
        notes = json.load(f)

    transcript_text = None
    if args.transcript:
        transcript_text = Path(args.transcript).read_text(encoding="utf-8")

    url = write_to_notion(
        notes, cfg,
        transcript_text=transcript_text,
        meeting_date=args.date,
        meeting_type=args.type,
        project=args.project,
    )
    print(f"Page URL: {url}")
