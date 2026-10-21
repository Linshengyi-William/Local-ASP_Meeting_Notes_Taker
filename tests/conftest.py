"""Shared pytest setup: make src/ importable the same way the CLI scripts do."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def base_cfg(tmp_path: Path) -> dict:
    """A minimal, self-contained config that never touches the real config/ dir."""
    return {
        "paths": {
            "inbox": str(tmp_path / "inbox"),
            "transcripts_raw": str(tmp_path / "raw"),
            "transcripts_clean": str(tmp_path / "clean"),
            "notes": str(tmp_path / "notes"),
            "manifest": str(tmp_path / "processed.json"),
        },
        "cleaning": {
            "min_segment_chars": 3,
            "dedup_window": 3,
            "dedup_similarity_threshold": 0.85,
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "context_file": "",
            "max_tokens": 16000,
            "max_tokens_cap": 64000,
            "temperature": 0.3,
            "verbosity": "high",
            "output_language": "bilingual",
            "max_retries": 0,
        },
        "notion": {"database_id": "test-db", "max_retries": 0, "properties": {}},
    }
