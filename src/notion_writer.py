"""
notion_writer.py — Write structured meeting notes to a Notion database page.

Expects the notes dict produced by generate_notes.py and creates a new page
in the configured Notion database with:
  - Database properties: Title, Date, Project, Meeting Type, Status, Tags
  - Page content blocks: Summary, Key Points, Decisions, Action Items,
    Open Questions, Risks, full Transcript (collapsed toggle)
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


# ── Notes → Notion blocks ─────────────────────────────────────────────────────

def _build_content_blocks(notes: dict, transcript_text: Optional[str]) -> list[dict]:
    blocks: list[dict] = []

    # Summary
    if notes.get("summary"):
        blocks.append(_heading("Summary / 摘要", level=2))
        blocks.extend(_paragraph(notes["summary"]))
        blocks.append(_divider())

    # Key Discussion Points
    if notes.get("key_points"):
        blocks.append(_heading("Key Discussion Points / 主要讨论点", level=2))
        for item in notes["key_points"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Decisions
    if notes.get("decisions"):
        blocks.append(_heading("Decisions Made / 决策", level=2))
        for item in notes["decisions"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Action Items
    if notes.get("action_items"):
        blocks.append(_heading("Action Items / 待办事项", level=2))
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
        blocks.append(_heading("Open Questions / 待解决问题", level=2))
        for item in notes["open_questions"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Risks
    if notes.get("risks"):
        blocks.append(_heading("Risks & Follow-ups / 风险与跟进", level=2))
        for item in notes["risks"]:
            blocks.append(_bullet(str(item)))
        blocks.append(_divider())

    # Full transcript (collapsed toggle so it doesn't dominate the page)
    if transcript_text:
        transcript_chunks = [
            _paragraph(transcript_text[i: i + _NOTION_MAX_BLOCK_TEXT])
            for i in range(0, len(transcript_text), _NOTION_MAX_BLOCK_TEXT)
        ]
        flat_chunks = [block for sublist in transcript_chunks for block in sublist]
        blocks.append(
            _toggle("Full Transcript / 原始转录 (click to expand)", flat_chunks)
        )

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

    ncfg = cfg.get("notion", {})
    database_id = ncfg.get("database_id", "")
    if not database_id:
        raise ValueError(
            "notion.database_id is empty in config/config.yaml. "
            "Set it to the ID of your Notion meeting notes database."
        )

    prop_names = ncfg.get("properties", {})
    if not meeting_date:
        meeting_date = date.today().isoformat()

    client = Client(auth=notion_token)

    properties = _build_properties(notes, meeting_date, meeting_type, project, prop_names)
    content_blocks = _build_content_blocks(notes, transcript_text)

    logger.info("Creating Notion page: '%s' ...", notes.get("title", "Meeting Notes"))
    response = client.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=content_blocks,
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
