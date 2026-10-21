from __future__ import annotations

import json

from clean import _is_filler, clean, clean_segments, merge_into_paragraphs


def _segs(*texts: str) -> list[dict]:
    return [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(texts)]


def test_filler_detection_handles_both_languages_and_punctuation():
    assert _is_filler("嗯。")
    assert _is_filler("对对对")
    assert _is_filler("Okay.")
    assert _is_filler("  uh  ")
    assert not _is_filler("okay let's start")
    assert not _is_filler("我们开始吧")


def test_clean_segments_drops_empty_short_and_filler(base_cfg):
    lines = clean_segments(_segs("", "好的", "嗯嗯", "um", "这个需求下周上线。"), base_cfg)
    assert lines == ["这个需求下周上线。"]


def test_clean_segments_removes_near_duplicate_asr_repeats(base_cfg):
    lines = clean_segments(
        _segs(
            "We need to fix the login bug before Friday.",
            "We need to fix the login bug before Friday",  # Whisper repeat
            "Marco will take it.",
        ),
        base_cfg,
    )
    assert lines == ["We need to fix the login bug before Friday.", "Marco will take it."]


def test_clean_segments_keeps_repeats_outside_dedup_window(base_cfg):
    base_cfg["cleaning"]["dedup_window"] = 1
    lines = clean_segments(_segs("Sounds good to me.", "Next topic is staging.", "Sounds good to me."), base_cfg)
    assert len(lines) == 3


def test_clean_segments_preserves_code_switched_text(base_cfg):
    mixed = "这个 PR 我们先 merge 到 staging，然后再看 QA 的 feedback。"
    assert clean_segments(_segs(mixed), base_cfg) == [mixed]


def test_merge_into_paragraphs_flushes_on_length():
    lines = ["a" * 120, "b" * 120, "c" * 10]
    paras = merge_into_paragraphs(lines, target_chars=200)
    assert paras == ["a" * 120 + " " + "b" * 120, "c" * 10]


def test_merge_into_paragraphs_flushes_on_topic_shift():
    paras = merge_into_paragraphs(["First point.", "Second topic here", "and more"], target_chars=1000)
    assert paras == ["First point.", "Second topic here and more"]


def test_merge_into_paragraphs_treats_chinese_start_as_topic_shift():
    paras = merge_into_paragraphs(["第一件事说完了。", "第二件事是预算"], target_chars=1000)
    assert paras == ["第一件事说完了。", "第二件事是预算"]


def test_clean_writes_header_and_paragraphs(tmp_path, base_cfg):
    raw = tmp_path / "2026-10-01_sync.json"
    raw.write_text(
        json.dumps(
            {"source": "2026-10-01_sync.m4a", "language": "zh", "segments": _segs("大家好，我们开始。", "嗯", "Agenda 有三项。")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = clean(raw, base_cfg, tmp_path / "clean")
    text = out.read_text(encoding="utf-8")
    assert out.name == "2026-10-01_sync.txt"
    assert text.startswith("[Source: 2026-10-01_sync.m4a]  [Language: zh]\n")
    body = text.split("\n", 2)[2]
    assert body == "大家好，我们开始。\n\nAgenda 有三项。"  # filler "嗯" dropped
