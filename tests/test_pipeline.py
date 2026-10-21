from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import pipeline


def test_infer_date_from_filename():
    assert pipeline._infer_date_from_filename("2026-04-07_product-sync") == "2026-04-07"
    assert pipeline._infer_date_from_filename("standup") == date.today().isoformat()


def test_manifest_roundtrip_is_atomic(tmp_path):
    path = tmp_path / "sub" / "processed.json"
    pipeline._save_manifest({"a": {"status": "done"}}, path)
    assert pipeline._load_manifest(path) == {"a": {"status": "done"}}
    assert not path.with_suffix(".tmp").exists()


def test_corrupt_manifest_starts_fresh(tmp_path):
    path = tmp_path / "processed.json"
    path.write_text("{not json", encoding="utf-8")
    assert pipeline._load_manifest(path) == {}


@pytest.fixture
def stubbed_pipeline(tmp_path, monkeypatch):
    """Replace the four heavy steps with fast fakes that record their calls."""
    calls: list[str] = []
    paths = {
        "inbox": tmp_path / "inbox",
        "transcripts_raw": tmp_path / "raw",
        "transcripts_clean": tmp_path / "clean",
        "notes": tmp_path / "notes",
        "manifest": tmp_path / "processed.json",
    }
    for key, p in paths.items():
        (p.parent if key == "manifest" else p).mkdir(parents=True, exist_ok=True)

    def fake_transcribe(audio, cfg, out_dir):
        calls.append("transcribe")
        out = Path(out_dir) / (Path(audio).stem + ".json")
        out.write_text(json.dumps({"segments": []}), encoding="utf-8")
        return out

    def fake_clean(raw, cfg, out_dir):
        calls.append("clean")
        out = Path(out_dir) / (Path(raw).stem + ".txt")
        out.write_text("[Source: x]\n\nhello", encoding="utf-8")
        return out

    def fake_generate(clean_path, cfg, out_dir, provider=None, lang=None):
        calls.append("generate_notes")
        notes = {"title": "t", "_language": lang or "bilingual"}
        (Path(out_dir) / (Path(clean_path).stem + "_notes.json")).write_text(json.dumps(notes), encoding="utf-8")
        return notes

    def fake_notion(notes, cfg, **kwargs):
        calls.append("notion")
        return "https://notion.so/page"

    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline, "clean", fake_clean)
    monkeypatch.setattr(pipeline, "generate_notes", fake_generate)
    monkeypatch.setattr(pipeline, "write_to_notion", fake_notion)
    return paths, calls


def test_process_file_runs_all_steps_and_records_manifest(stubbed_pipeline, base_cfg):
    paths, calls = stubbed_pipeline
    audio = paths["inbox"] / "2026-10-01_sync.m4a"
    audio.write_bytes(b"\x00")
    manifest: dict = {}

    notes = pipeline.process_file(audio, base_cfg, paths, manifest=manifest, manifest_path=paths["manifest"])

    assert calls == ["transcribe", "clean", "generate_notes", "notion"]
    assert notes["_notion_url"] == "https://notion.so/page"
    entry = json.loads(paths["manifest"].read_text())["2026-10-01_sync"]
    assert entry["status"] == "done"
    assert entry["meeting_date"] == "2026-10-01"
    assert entry["steps_completed"] == ["transcribe", "clean", "generate_notes", "notion"]


def test_rerun_skips_completed_steps_including_notion(stubbed_pipeline, base_cfg):
    paths, calls = stubbed_pipeline
    audio = paths["inbox"] / "2026-10-01_sync.m4a"
    audio.write_bytes(b"\x00")
    manifest: dict = {}
    pipeline.process_file(audio, base_cfg, paths, manifest=manifest, manifest_path=paths["manifest"])
    calls.clear()

    pipeline.process_file(audio, base_cfg, paths, manifest=manifest, manifest_path=paths["manifest"])
    assert calls == []  # nothing re-run, and crucially no duplicate Notion page


def test_failed_step_is_recorded_and_reraised(stubbed_pipeline, base_cfg, monkeypatch):
    paths, _ = stubbed_pipeline

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(pipeline, "generate_notes", boom)
    audio = paths["inbox"] / "2026-10-02_x.m4a"
    audio.write_bytes(b"\x00")
    manifest: dict = {}
    with pytest.raises(RuntimeError):
        pipeline.process_file(audio, base_cfg, paths, manifest=manifest, manifest_path=paths["manifest"])
    entry = manifest["2026-10-02_x"]
    assert entry["status"] == "failed"
    assert entry["error"] == "generate_notes: LLM down"
    assert entry["steps_completed"] == ["transcribe", "clean"]


def test_process_inbox_skips_done_and_ignores_non_audio(stubbed_pipeline, base_cfg, capsys):
    paths, calls = stubbed_pipeline
    (paths["inbox"] / "2026-10-01_a.m4a").write_bytes(b"\x00")
    (paths["inbox"] / "2026-10-02_b.mp3").write_bytes(b"\x00")
    (paths["inbox"] / "notes.txt").write_text("not audio")
    manifest = {"2026-10-01_a": {"status": "done"}}

    results = pipeline.process_inbox(base_cfg, paths, skip_notion=True,
                                     manifest=manifest, manifest_path=paths["manifest"])
    assert len(results) == 1
    assert calls == ["transcribe", "clean", "generate_notes"]  # only b, and no Notion
    assert "1 succeeded  |  0 failed  |  1 skipped" in capsys.readouterr().out
