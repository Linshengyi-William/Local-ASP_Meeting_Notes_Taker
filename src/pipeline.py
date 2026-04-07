"""
pipeline.py — End-to-end meeting notes pipeline orchestrator.

Usage (process all new audio files in inbox):
    python src/pipeline.py

Usage (process a single file):
    python src/pipeline.py --file meetings/inbox/2026-04-07_project-sync.m4a

Flags:
    --no-notion     Skip the Notion upload step (useful for dry runs / testing)
    --type          Meeting type: Online | Offline  (default: Online)
    --project       Optional project name tag
    --config        Path to config YAML (default: config/config.yaml)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Ensure src/ is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from transcribe import transcribe
from clean import clean
from generate_notes import generate_notes
from notion_writer import write_to_notion

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".ogg", ".flac", ".aac", ".wma"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_date_from_filename(stem: str) -> str:
    """Try to parse YYYY-MM-DD from the start of a filename stem."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", stem)
    if match:
        return match.group(1)
    return date.today().isoformat()


def _resolve_paths(cfg: dict) -> dict[str, Path]:
    base = Path(__file__).parent.parent
    return {
        "inbox":            base / cfg["paths"]["inbox"],
        "transcripts_raw":  base / cfg["paths"]["transcripts_raw"],
        "transcripts_clean": base / cfg["paths"]["transcripts_clean"],
        "notes":            base / cfg["paths"]["notes"],
    }


def _already_processed(audio_path: Path, raw_dir: Path) -> bool:
    """Return True if a raw transcript JSON already exists for this audio file."""
    return (raw_dir / (audio_path.stem + ".json")).exists()


# ── Single-file processor ─────────────────────────────────────────────────────

def process_file(
    audio_path: Path,
    cfg: dict,
    paths: dict[str, Path],
    *,
    skip_notion: bool = False,
    meeting_type: str = "Online",
    project: str | None = None,
    force: bool = False,
) -> dict | None:
    """
    Run the full pipeline for one audio file.
    Returns the notes dict on success, None on skip.
    """
    stem = audio_path.stem
    meeting_date = _infer_date_from_filename(stem)

    logger.info("=" * 60)
    logger.info("Processing: %s  [date=%s, type=%s]", audio_path.name, meeting_date, meeting_type)

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    raw_json_path = paths["transcripts_raw"] / (stem + ".json")
    if raw_json_path.exists() and not force:
        logger.info("  [SKIP] Raw transcript exists: %s", raw_json_path.name)
    else:
        logger.info("  [1/4] Transcribing audio ...")
        raw_json_path = transcribe(audio_path, cfg, paths["transcripts_raw"])

    # ── Step 2: Clean ─────────────────────────────────────────────────────────
    clean_txt_path = paths["transcripts_clean"] / (stem + ".txt")
    if clean_txt_path.exists() and not force:
        logger.info("  [SKIP] Clean transcript exists: %s", clean_txt_path.name)
    else:
        logger.info("  [2/4] Cleaning transcript ...")
        clean_txt_path = clean(raw_json_path, cfg, paths["transcripts_clean"])

    # ── Step 3: Generate notes ────────────────────────────────────────────────
    notes_json_path = paths["notes"] / (stem + "_notes.json")
    if notes_json_path.exists() and not force:
        logger.info("  [SKIP] Notes JSON exists: %s", notes_json_path.name)
        notes = json.loads(notes_json_path.read_text(encoding="utf-8"))
    else:
        logger.info("  [3/4] Generating meeting notes via LLM ...")
        notes = generate_notes(clean_txt_path, cfg, paths["notes"])

    # ── Step 4: Write to Notion ───────────────────────────────────────────────
    if skip_notion:
        logger.info("  [4/4] Notion upload skipped (--no-notion)")
    else:
        logger.info("  [4/4] Writing to Notion ...")
        transcript_text = clean_txt_path.read_text(encoding="utf-8")
        try:
            page_url = write_to_notion(
                notes, cfg,
                transcript_text=transcript_text,
                meeting_date=meeting_date,
                meeting_type=meeting_type,
                project=project,
            )
            logger.info("  Notion page: %s", page_url)
            notes["_notion_url"] = page_url
            # Persist the URL back to the notes JSON
            notes_json_path.write_text(
                json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("  Notion upload failed: %s", exc)

    logger.info("Done: %s", audio_path.name)
    return notes


# ── Batch processor ───────────────────────────────────────────────────────────

def process_inbox(
    cfg: dict,
    paths: dict[str, Path],
    *,
    skip_notion: bool = False,
    meeting_type: str = "Online",
    project: str | None = None,
    force: bool = False,
) -> list[dict]:
    inbox = paths["inbox"]
    audio_files = sorted(
        f for f in inbox.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    )

    if not audio_files:
        logger.info("No audio files found in %s", inbox)
        return []

    logger.info("Found %d audio file(s) in inbox.", len(audio_files))
    results = []
    for audio_path in audio_files:
        if not force and _already_processed(audio_path, paths["transcripts_raw"]):
            # Only skip if all downstream outputs also exist
            notes_json = paths["notes"] / (audio_path.stem + "_notes.json")
            if notes_json.exists():
                logger.info("Already processed (skipping): %s", audio_path.name)
                continue
        result = process_file(
            audio_path, cfg, paths,
            skip_notion=skip_notion,
            meeting_type=meeting_type,
            project=project,
            force=force,
        )
        if result:
            results.append(result)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Meeting notes pipeline: transcribe → clean → LLM notes → Notion"
    )
    parser.add_argument(
        "--file", "-f", default=None,
        help="Process a single audio file instead of the whole inbox."
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to config YAML (default: config/config.yaml)"
    )
    parser.add_argument(
        "--no-notion", action="store_true",
        help="Skip the Notion upload step."
    )
    parser.add_argument(
        "--type", default="Online", choices=["Online", "Offline"],
        help="Meeting type tag (default: Online)"
    )
    parser.add_argument(
        "--project", default=None,
        help="Optional project name tag."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-run all steps even if outputs already exist."
    )
    args = parser.parse_args()

    # Load config relative to project root (one level above src/)
    config_path = Path(__file__).parent.parent / args.config
    if not config_path.exists():
        config_path = Path(args.config)  # allow absolute paths too

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    paths = _resolve_paths(cfg)
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)

    if args.file:
        audio_path = Path(args.file)
        if not audio_path.exists():
            logger.error("File not found: %s", audio_path)
            sys.exit(1)
        process_file(
            audio_path, cfg, paths,
            skip_notion=args.no_notion,
            meeting_type=args.type,
            project=args.project,
            force=args.force,
        )
    else:
        process_inbox(
            cfg, paths,
            skip_notion=args.no_notion,
            meeting_type=args.type,
            project=args.project,
            force=args.force,
        )


if __name__ == "__main__":
    main()
