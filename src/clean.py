"""
clean.py — Lightweight post-processing of raw ASR transcripts.

Goals:
  • Remove very short / empty segments
  • Deduplicate near-identical consecutive segments (common ASR glitch)
  • Merge over-fragmented segments into readable paragraphs
  • Preserve bilingual (zh/en) content as-is

Input:  raw transcript JSON produced by transcribe.py
Output: cleaned plain-text file (.txt) in the clean transcripts folder
"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _is_filler(text: str) -> bool:
    """Return True for common single-word/phrase filler tokens."""
    fillers = {
        "嗯", "啊", "哦", "呃", "唔", "嗯嗯", "对对对", "好好好",
        "um", "uh", "hmm", "yeah", "okay", "ok", "right", "like",
    }
    stripped = text.strip().lower().rstrip("。，,.!?！？")
    return stripped in fillers


# ── Core cleaning pipeline ────────────────────────────────────────────────────

def clean_segments(segments: list[dict], cfg: dict) -> list[str]:
    """
    Takes the list of segment dicts from the raw JSON and returns a list
    of cleaned text lines suitable for feeding to the LLM.
    """
    ccfg = cfg.get("cleaning", {})
    min_chars = int(ccfg.get("min_segment_chars", 3))
    dedup_window = int(ccfg.get("dedup_window", 3))
    sim_threshold = float(ccfg.get("dedup_similarity_threshold", 0.85))

    texts: list[str] = []
    for seg in segments:
        t = seg.get("text", "").strip()
        if not t:
            continue
        if len(t) < min_chars:
            continue
        if _is_filler(t):
            continue
        texts.append(t)

    # Remove near-duplicate consecutive segments
    cleaned: list[str] = []
    for t in texts:
        is_dup = False
        for prev in cleaned[-dedup_window:]:
            if _similarity(t, prev) >= sim_threshold:
                is_dup = True
                break
        if not is_dup:
            cleaned.append(t)

    return cleaned


def merge_into_paragraphs(lines: list[str], target_chars: int = 200) -> list[str]:
    """
    Merge short adjacent lines into ~*target_chars* character paragraphs.
    A new paragraph starts when:
      - accumulated length exceeds target_chars, OR
      - the line ends with sentence-final punctuation AND the next line
        starts with an uppercase letter or Chinese character (topic shift).
    """
    paragraphs: list[str] = []
    buffer: list[str] = []
    buf_len = 0

    sentence_end = re.compile(r"[。！？.!?]$")
    new_topic_start = re.compile(r"^[\u4e00-\u9fffA-Z]")

    for i, line in enumerate(lines):
        buffer.append(line)
        buf_len += len(line)

        next_line = lines[i + 1] if i + 1 < len(lines) else None
        flush = buf_len >= target_chars
        if next_line and sentence_end.search(line) and new_topic_start.match(next_line):
            flush = True

        if flush:
            paragraphs.append(" ".join(buffer))
            buffer = []
            buf_len = 0

    if buffer:
        paragraphs.append(" ".join(buffer))

    return paragraphs


def clean(
    raw_json_path: str | Path,
    cfg: dict,
    output_dir: str | Path,
) -> Path:
    """
    Clean a raw transcript JSON and write the result to *output_dir* as .txt.
    Returns the path to the written file.
    """
    raw_json_path = Path(raw_json_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(raw_json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    source = data.get("source", raw_json_path.stem)
    language = data.get("language", "unknown")

    logger.info("Cleaning transcript for: %s (%d segments)", source, len(segments))

    lines = clean_segments(segments, cfg)
    paragraphs = merge_into_paragraphs(lines)

    header = f"[Source: {source}]  [Language: {language}]\n"
    body = "\n\n".join(paragraphs)
    content = header + "\n" + body

    output_path = output_dir / (raw_json_path.stem + ".txt")
    output_path.write_text(content, encoding="utf-8")
    logger.info(
        "Cleaned transcript saved → %s  (%d paragraphs)", output_path, len(paragraphs)
    )

    return output_path


# ── CLI helper ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Clean a raw transcript JSON.")
    parser.add_argument("raw_json", help="Path to the raw transcript JSON")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = args.output_dir or cfg["paths"]["transcripts_clean"]
    result_path = clean(args.raw_json, cfg, out_dir)
    print(f"Done: {result_path}")
