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
    --force         Re-run all steps even if outputs already exist
    --provider      LLM provider override: openai | anthropic
    --lang          Output language override: en | zh | bilingual  (zh = Simplified Chinese)
    --status        Print processing history and exit
    --watch         Watch inbox for new audio files and process automatically
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
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
from utils import validate_config

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
    """
    Try to parse YYYY-MM-DD from the start of a filename stem.
    Logs a warning and falls back to today if no date prefix is found.
    """
    match = re.match(r"(\d{4}-\d{2}-\d{2})", stem)
    if match:
        return match.group(1)
    today = date.today().isoformat()
    logger.warning(
        "No date prefix found in filename '%s' — using today's date (%s). "
        "Tip: name files as YYYY-MM-DD_topic.m4a for automatic date tagging.",
        stem, today,
    )
    return today


def _resolve_paths(cfg: dict) -> dict[str, Path]:
    base = Path(__file__).parent.parent
    paths = {
        "inbox":             base / cfg["paths"]["inbox"],
        "transcripts_raw":   base / cfg["paths"]["transcripts_raw"],
        "transcripts_clean": base / cfg["paths"]["transcripts_clean"],
        "notes":             base / cfg["paths"]["notes"],
        "manifest":          base / cfg["paths"].get("manifest", "meetings/processed.json"),
    }
    return paths


# ── Manifest helpers ──────────────────────────────────────────────────────────

def _load_manifest(manifest_path: Path) -> dict:
    """Load the manifest JSON, or return {} if it doesn't exist yet."""
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Could not read manifest %s: %s — starting fresh.", manifest_path, exc
            )
    return {}


def _save_manifest(manifest: dict, manifest_path: Path) -> None:
    """Atomically write the manifest to disk (tmp → replace)."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)


def _manifest_entry(filename: str, stem: str, meeting_date: str) -> dict:
    """Create a blank manifest entry for a new audio file."""
    return {
        "filename": filename,
        "stem": stem,
        "meeting_date": meeting_date,
        "status": "partial",
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "steps_completed": [],
        "notion_url": None,
        "error": None,
    }


# ── Batch summary ─────────────────────────────────────────────────────────────

def print_batch_summary(batch_records: list[dict], skipped: list[str]) -> None:
    """Print a formatted batch result table to stdout."""
    total = len(batch_records) + len(skipped)
    succeeded = sum(1 for r in batch_records if r["status"] == "done")
    failed = sum(1 for r in batch_records if r["status"] == "failed")

    width = 60
    print("\n" + "═" * width)
    print(f" Batch complete  ·  {total} file(s) processed")
    print("─" * width)

    for rec in batch_records:
        stem = rec["stem"][:32]
        if rec["status"] == "done":
            url = rec.get("notion_url") or "(--no-notion)"
            if len(url) > 30:
                url = url[:27] + "..."
            print(f" \u2713  {stem:<33} \u2192 {url}")
        else:
            err = (rec.get("error") or "unknown error")[:30]
            print(f" \u2717  {stem:<33} \u2192 {err}")

    for stem in skipped:
        print(f" \u2500  {stem[:33]:<33} skipped (already done)")

    print("═" * width)
    print(f"   {succeeded} succeeded  |  {failed} failed  |  {len(skipped)} skipped\n")


def print_status(manifest: dict) -> None:
    """Print full manifest history as a summary table and exit."""
    if not manifest:
        print("No processing history found.")
        sys.exit(0)

    records = sorted(manifest.values(), key=lambda x: x.get("last_run", ""))
    batch_records = [
        {
            "stem": e["stem"],
            "status": e["status"],
            "notion_url": e.get("notion_url"),
            "error": e.get("error"),
        }
        for e in records
    ]
    print(f"\nProcessing history: {len(manifest)} total entries\n")
    print_batch_summary(batch_records, skipped=[])
    sys.exit(0)


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
    provider: str | None = None,
    lang: str | None = None,
    manifest: dict | None = None,
    manifest_path: Path | None = None,
) -> dict | None:
    """
    Run the full pipeline for one audio file.
    Returns the notes dict on success, None on skip.
    Raises on unrecoverable errors (caller decides how to handle them).
    """
    stem = audio_path.stem
    meeting_date = _infer_date_from_filename(stem)

    logger.info("=" * 60)
    logger.info("Processing: %s  [date=%s, type=%s]", audio_path.name, meeting_date, meeting_type)

    # ── Manifest init ─────────────────────────────────────────────────────────
    entry: dict | None = None
    if manifest is not None:
        if stem not in manifest:
            manifest[stem] = _manifest_entry(audio_path.name, stem, meeting_date)
        entry = manifest[stem]
        entry["last_run"] = datetime.now().isoformat(timespec="seconds")
        entry["meeting_date"] = meeting_date
        entry["error"] = None

    def _step_done(step_name: str) -> None:
        if entry is None:
            return
        if step_name not in entry["steps_completed"]:
            entry["steps_completed"].append(step_name)
        if manifest_path:
            _save_manifest(manifest, manifest_path)

    def _step_failed(step_name: str, exc: Exception) -> None:
        if entry is None:
            return
        entry["status"] = "failed"
        entry["error"] = f"{step_name}: {exc}"
        if manifest_path:
            _save_manifest(manifest, manifest_path)

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    raw_json_path = paths["transcripts_raw"] / (stem + ".json")
    if raw_json_path.exists() and not force:
        logger.info("  [SKIP] Raw transcript exists: %s", raw_json_path.name)
        _step_done("transcribe")
    else:
        logger.info("  [1/4] Transcribing audio ...")
        try:
            raw_json_path = transcribe(audio_path, cfg, paths["transcripts_raw"])
            _step_done("transcribe")
        except Exception as exc:
            logger.error("  Transcription failed: %s", exc)
            _step_failed("transcribe", exc)
            raise

    # ── Step 2: Clean ─────────────────────────────────────────────────────────
    clean_txt_path = paths["transcripts_clean"] / (stem + ".txt")
    if clean_txt_path.exists() and not force:
        logger.info("  [SKIP] Clean transcript exists: %s", clean_txt_path.name)
        _step_done("clean")
    else:
        logger.info("  [2/4] Cleaning transcript ...")
        try:
            clean_txt_path = clean(raw_json_path, cfg, paths["transcripts_clean"])
            _step_done("clean")
        except Exception as exc:
            logger.error("  Cleaning failed: %s", exc)
            _step_failed("clean", exc)
            raise

    # ── Step 3: Generate notes ────────────────────────────────────────────────
    notes_json_path = paths["notes"] / (stem + "_notes.json")
    if notes_json_path.exists() and not force:
        logger.info("  [SKIP] Notes JSON exists: %s", notes_json_path.name)
        notes = json.loads(notes_json_path.read_text(encoding="utf-8"))
        _step_done("generate_notes")
    else:
        logger.info("  [3/4] Generating meeting notes via LLM ...")
        try:
            notes = generate_notes(clean_txt_path, cfg, paths["notes"], provider=provider, lang=lang)
            _step_done("generate_notes")
        except Exception as exc:
            logger.error("  Note generation failed: %s", exc)
            _step_failed("generate_notes", exc)
            raise

    # ── Step 4: Write to Notion ───────────────────────────────────────────────
    notion_already_done = (
        (entry is not None and "notion" in entry.get("steps_completed", []))
        or bool(notes.get("_notion_url"))
    )
    if skip_notion:
        logger.info("  [4/4] Notion upload skipped (--no-notion)")
        if entry is not None:
            entry["status"] = "done"
            if manifest_path:
                _save_manifest(manifest, manifest_path)
    elif notion_already_done and not force:
        existing_url = (entry or {}).get("notion_url") or notes.get("_notion_url", "")
        logger.info("  [SKIP] Notion page already exists: %s", existing_url)
        _step_done("notion")
        if entry is not None:
            entry["status"] = "done"
            if manifest_path:
                _save_manifest(manifest, manifest_path)
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
            notes_json_path.write_text(
                json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _step_done("notion")
            if entry is not None:
                entry["notion_url"] = page_url
                entry["status"] = "done"
                if manifest_path:
                    _save_manifest(manifest, manifest_path)
        except Exception as exc:
            logger.error("  Notion upload failed: %s", exc)
            _step_failed("notion", exc)
            raise

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
    provider: str | None = None,
    lang: str | None = None,
    manifest: dict | None = None,
    manifest_path: Path | None = None,
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

    results: list[dict] = []
    batch_records: list[dict] = []
    skipped: list[str] = []

    for audio_path in audio_files:
        # Skip check via manifest (preferred) or filesystem fallback
        if not force and manifest is not None:
            entry = manifest.get(audio_path.stem)
            if entry and entry.get("status") == "done":
                logger.info("Already done (skipping): %s", audio_path.name)
                skipped.append(audio_path.stem)
                continue
        elif not force:
            # Fallback: filesystem-based skip (no manifest)
            raw_json = paths["transcripts_raw"] / (audio_path.stem + ".json")
            notes_json = paths["notes"] / (audio_path.stem + "_notes.json")
            if raw_json.exists() and notes_json.exists():
                logger.info("Already processed (skipping): %s", audio_path.name)
                skipped.append(audio_path.stem)
                continue

        try:
            result = process_file(
                audio_path, cfg, paths,
                skip_notion=skip_notion,
                meeting_type=meeting_type,
                project=project,
                force=force,
                provider=provider,
                lang=lang,
                manifest=manifest,
                manifest_path=manifest_path,
            )
            if result:
                results.append(result)
                batch_records.append({
                    "stem": audio_path.stem,
                    "status": "done",
                    "notion_url": result.get("_notion_url"),
                    "error": None,
                })
        except Exception as exc:
            logger.error("Failed to process %s: %s", audio_path.name, exc)
            batch_records.append({
                "stem": audio_path.stem,
                "status": "failed",
                "notion_url": None,
                "error": str(exc),
            })

    print_batch_summary(batch_records, skipped)
    return results


# ── File watcher ──────────────────────────────────────────────────────────────

def _make_watcher_handler(
    cfg: dict,
    paths: dict[str, Path],
    *,
    skip_notion: bool,
    meeting_type: str,
    project: str | None,
    provider: str | None,
    lang: str | None,
    manifest: dict,
    manifest_path: Path,
):
    """
    Factory that returns a watchdog FileSystemEventHandler bound to the current
    pipeline configuration and manifest.  Using a factory avoids importing
    watchdog at module level — the import only happens when --watch is used.
    """
    from watchdog.events import FileSystemEventHandler

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            audio_path = Path(event.src_path)
            if audio_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                return
            logger.info(
                "New file detected: %s — waiting 1 s for write to complete ...",
                audio_path.name,
            )
            time.sleep(1)
            if not audio_path.exists():
                logger.warning("File vanished before processing: %s", audio_path.name)
                return
            try:
                process_file(
                    audio_path, cfg, paths,
                    skip_notion=skip_notion,
                    meeting_type=meeting_type,
                    project=project,
                    force=False,
                    provider=provider,
                    lang=lang,
                    manifest=manifest,
                    manifest_path=manifest_path,
                )
            except Exception as exc:
                logger.error("Failed to process %s: %s", audio_path.name, exc)

    return _Handler()


def run_watcher(
    cfg: dict,
    paths: dict[str, Path],
    *,
    skip_notion: bool = False,
    meeting_type: str = "Online",
    project: str | None = None,
    provider: str | None = None,
    lang: str | None = None,
    manifest: dict,
    manifest_path: Path,
) -> None:
    """Start the watchdog observer and block until Ctrl+C."""
    from watchdog.observers import Observer

    handler = _make_watcher_handler(
        cfg, paths,
        skip_notion=skip_notion,
        meeting_type=meeting_type,
        project=project,
        provider=provider,
        lang=lang,
        manifest=manifest,
        manifest_path=manifest_path,
    )
    observer = Observer()
    observer.schedule(handler, str(paths["inbox"]), recursive=False)
    observer.start()
    logger.info(
        "Watching inbox for new audio files: %s  (Ctrl+C to stop)",
        paths["inbox"],
    )

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Stopping watcher ...")
    finally:
        observer.stop()
        observer.join()
        logger.info("Watcher stopped.")


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
    parser.add_argument(
        "--provider", default=None, choices=["openai", "anthropic"],
        help="LLM provider for note generation (overrides config). Default: openai",
    )
    parser.add_argument(
        "--lang", default=None, choices=["en", "zh", "bilingual"],
        help="Output language for notes (overrides config). zh = Simplified Chinese.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print processing history manifest and exit."
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch the inbox directory for new audio files and process automatically."
    )
    args = parser.parse_args()

    # Load config relative to project root (one level above src/)
    config_path = Path(__file__).parent.parent / args.config
    if not config_path.exists():
        config_path = Path(args.config)  # allow absolute paths too

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Validate config and environment variables before doing any work
    validate_config(cfg, skip_notion=args.no_notion, provider_override=args.provider)

    paths = _resolve_paths(cfg)
    for key, p in paths.items():
        if key != "manifest":
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(paths["manifest"])
    manifest_path = paths["manifest"]

    if args.status:
        print_status(manifest)
        return  # print_status calls sys.exit(0) internally

    if args.watch:
        run_watcher(
            cfg, paths,
            skip_notion=args.no_notion,
            meeting_type=args.type,
            project=args.project,
            provider=args.provider,
            lang=args.lang,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        return

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
            provider=args.provider,
            lang=args.lang,
            manifest=manifest,
            manifest_path=manifest_path,
        )
    else:
        process_inbox(
            cfg, paths,
            skip_notion=args.no_notion,
            meeting_type=args.type,
            project=args.project,
            force=args.force,
            provider=args.provider,
            lang=args.lang,
            manifest=manifest,
            manifest_path=manifest_path,
        )


if __name__ == "__main__":
    main()
