"""
transcribe.py — Local speech-to-text using faster-whisper.

Reads an audio file and writes a raw transcript to the configured output folder.
Supports .m4a, .mp3, .wav, .mp4, .ogg, .flac, and any format ffmpeg can handle.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_device_and_compute(device: str, compute_type: str) -> tuple[str, str]:
    """Pick sensible defaults when config says 'auto'."""
    import torch

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if compute_type == "auto":
        if device == "cuda":
            compute_type = "float16"
        else:
            compute_type = "int8"

    return device, compute_type


def transcribe(
    audio_path: str | Path,
    cfg: dict,
    output_dir: str | Path,
) -> Path:
    """
    Transcribe *audio_path* and save the result to *output_dir*.

    Returns the path to the written JSON transcript file.

    The JSON schema is:
    {
        "source": "<original audio filename>",
        "language": "<detected or forced language>",
        "segments": [
            {
                "start": 0.0,
                "end":   3.4,
                "text":  "Hello everyone."
            },
            ...
        ],
        "full_text": "<all segments joined>"
    }
    """
    from faster_whisper import WhisperModel

    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tcfg = cfg.get("transcription", {})
    model_size = tcfg.get("model", "large-v3-turbo")
    device = tcfg.get("device", "auto")
    compute_type = tcfg.get("compute_type", "auto")
    language = tcfg.get("language") or None        # None = auto-detect
    beam_size = int(tcfg.get("beam_size", 5))
    vad_filter = bool(tcfg.get("vad_filter", True))
    word_timestamps = bool(tcfg.get("word_timestamps", False))

    device, compute_type = _resolve_device_and_compute(device, compute_type)

    logger.info(
        "Loading faster-whisper model '%s' on %s (%s) ...",
        model_size, device, compute_type,
    )
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    logger.info("Transcribing: %s", audio_path.name)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
    )

    detected_lang = info.language
    logger.info("Detected language: %s (confidence %.2f)", detected_lang, info.language_probability)

    segments = []
    for seg in segments_iter:
        entry: dict = {
            "start": round(seg.start, 3),
            "end":   round(seg.end,   3),
            "text":  seg.text.strip(),
        }
        if word_timestamps and seg.words:
            entry["words"] = [
                {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
                for w in seg.words
            ]
        segments.append(entry)
        logger.debug("[%.1f → %.1f] %s", seg.start, seg.end, seg.text.strip())

    full_text = " ".join(s["text"] for s in segments if s["text"])

    result = {
        "source": audio_path.name,
        "language": detected_lang,
        "segments": segments,
        "full_text": full_text,
    }

    output_path = output_dir / (audio_path.stem + ".json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Raw transcript saved → %s", output_path)

    return output_path


# ── CLI helper ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Transcribe a single audio file.")
    parser.add_argument("audio", help="Path to the audio file")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = args.output_dir or cfg["paths"]["transcripts_raw"]
    result_path = transcribe(args.audio, cfg, out_dir)
    print(f"Done: {result_path}")
