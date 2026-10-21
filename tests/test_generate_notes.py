from __future__ import annotations

import json
from pathlib import Path

import pytest

import generate_notes as gn

ROOT = Path(__file__).resolve().parent.parent


# ── Deterministic corrections ─────────────────────────────────────────────────

CONTEXT_WITH_TABLE = """\
# Context

## People
| Correct name | Misheard as |
|---|---|
| Marco | Marko |

## Automatic corrections

| Replace | With |
|---|---|
| Marko | Marco |
| 马可 | Marco |
| QA team | Platform team |

## How to extend this file
| Not | Parsed |
|---|---|
| ignored | row |
"""


def test_parse_corrections_reads_only_the_corrections_table():
    pairs = gn._parse_corrections(CONTEXT_WITH_TABLE)
    assert pairs == [("Marko", "Marco"), ("马可", "Marco"), ("QA team", "Platform team")]


def test_parse_corrections_without_section_is_empty():
    assert gn._parse_corrections("# nothing here\n| a | b |\n") == []


def test_shipped_example_context_parses():
    """The template users copy must stay machine-readable."""
    text = (ROOT / "config" / "context.example.md").read_text(encoding="utf-8")
    pairs = gn._parse_corrections(text)
    assert ("Yucky", "Yuki") in pairs
    assert ("吉拉", "Jira") in pairs
    assert all(wrong != "Replace" for wrong, _ in pairs)


def test_apply_corrections_respects_ascii_word_boundaries():
    notes = {"summary": "Marko said Markov chains are fine; ask Marko."}
    count = gn._apply_corrections(notes, [("Marko", "Marco")])
    assert notes["summary"] == "Marco said Markov chains are fine; ask Marco."
    assert count == 2


def test_apply_corrections_matches_cjk_anywhere_and_recurses():
    notes = {
        "discussion": [{"topic": "上线", "details": ["马可说周五上线"]}],
        "action_items": [{"owner": "马可", "task": "修复登录", "due": None}],
    }
    gn._apply_corrections(notes, [("马可", "Marco")])
    assert notes["discussion"][0]["details"] == ["Marco说周五上线"]
    assert notes["action_items"][0]["owner"] == "Marco"
    assert notes["action_items"][0]["due"] is None


def test_apply_corrections_is_single_pass_so_rules_do_not_chain():
    notes = {"summary": "A then B"}
    gn._apply_corrections(notes, [("A", "B"), ("B", "C")])
    assert notes["summary"] == "B then C"


def test_apply_corrections_prefers_longest_match():
    notes = {"summary": "the QA team and QA"}
    gn._apply_corrections(notes, [("QA", "Quality"), ("QA team", "Platform team")])
    assert notes["summary"] == "the Platform team and Quality"


def test_apply_corrections_skips_private_metadata_keys():
    notes = {"_source": "Marko.txt", "title": "Marko sync"}
    gn._apply_corrections(notes, [("Marko", "Marco")])
    assert notes == {"_source": "Marko.txt", "title": "Marco sync"}


# ── Output sizing ─────────────────────────────────────────────────────────────

def test_estimate_tokens_counts_cjk_as_one_token_each():
    assert gn._estimate_tokens("中文") == 2
    assert gn._estimate_tokens("abcd" * 10) == 10
    assert gn._estimate_tokens("中文abcd") == 3


def test_adaptive_max_tokens_is_clamped_between_floor_and_cap():
    assert gn._adaptive_max_tokens("short", floor=16000, cap=64000) == 16000
    huge = "字" * 100_000
    assert gn._adaptive_max_tokens(huge, floor=16000, cap=64000) == 64000
    mid = "字" * 20_000  # 20k * 1.5 + 4000 = 34000
    assert gn._adaptive_max_tokens(mid, floor=16000, cap=64000) == 34000


@pytest.mark.parametrize(
    "tokens,expected",
    [(100, "SHORT"), (5000, "MEDIUM-LENGTH"), (10000, "LONG meeting"), (30000, "VERY LONG")],
)
def test_length_directive_scales_with_transcript(tokens, expected):
    assert expected in gn._length_directive("字" * tokens)


def test_model_output_limits_and_reasoning_detection():
    assert gn._model_output_limit("gpt-4o-mini") == 16384
    assert gn._model_output_limit("gpt-5-mini") is None
    assert gn._is_openai_reasoning_model("gpt-5")
    assert gn._is_openai_reasoning_model("o3-mini")
    assert not gn._is_openai_reasoning_model("gpt-4o")


def test_resolve_model_swaps_cross_provider_names():
    assert gn._resolve_model("openai", "claude-sonnet-4-6") == "gpt-5-mini"
    assert gn._resolve_model("anthropic", "gpt-5") == "claude-sonnet-4-6"
    assert gn._resolve_model("openai", "gpt-5") == "gpt-5"


# ── generate_notes end-to-end with a fake provider ────────────────────────────

class FakeProvider:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, system, user, model, max_tokens, temperature, max_retries, **extra):
        self.calls.append(
            dict(system=system, user=user, model=model, max_tokens=max_tokens,
                 temperature=temperature, max_retries=max_retries, extra=extra)
        )
        return self.response


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_generate_notes_end_to_end(tmp_path, base_cfg, monkeypatch):
    ctx = _write(tmp_path, "context.md", CONTEXT_WITH_TABLE)
    transcript = _write(tmp_path, "2026-10-01_sync.txt", "[Source: x]\n\nMarko 说 login bug 周五修好。")
    base_cfg["llm"]["context_file"] = str(ctx)

    payload = {
        "title": "Sync",
        "tldr": "Marko fixes login.",
        "action_items": [{"owner": "马可", "task": "Fix login bug", "due": "Friday"}],
        "tags": ["bug"],
    }
    fake = FakeProvider("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    monkeypatch.setitem(gn._PROVIDERS, "openai", fake)

    notes = gn.generate_notes(transcript, base_cfg, output_dir=tmp_path / "notes", lang="zh")

    # fenced JSON parsed and deterministic corrections applied
    assert notes["tldr"] == "Marco fixes login."
    assert notes["action_items"][0]["owner"] == "Marco"
    assert notes["_language"] == "zh" and notes["_model"] == "gpt-5-mini"
    assert notes["_source"] == "2026-10-01_sync.txt"

    call = fake.calls[0]
    assert "全部使用简体中文输出" in call["system"]          # zh system prompt chosen
    assert "STANDING PROJECT CONTEXT START" in call["system"]  # context injected
    assert "Marko 说 login bug" in call["user"]
    assert call["extra"] == {"verbosity": "high"}
    assert call["max_tokens"] == 16000

    saved = json.loads((tmp_path / "notes" / "2026-10-01_sync_notes.json").read_text(encoding="utf-8"))
    assert saved["action_items"][0]["owner"] == "Marco"


def test_generate_notes_clamps_budget_to_model_limit(tmp_path, base_cfg, monkeypatch):
    transcript = _write(tmp_path, "t.txt", "hello")
    base_cfg["llm"]["model"] = "gpt-4o"
    base_cfg["llm"]["max_tokens"] = 20000  # floor above gpt-4o's hard 16384 limit
    fake = FakeProvider('{"title": "x"}')
    monkeypatch.setitem(gn._PROVIDERS, "openai", fake)
    gn.generate_notes(transcript, base_cfg)
    # Asking for more than the model allows is a non-retryable 400, so the
    # floor itself must be clamped down to the model limit.
    assert fake.calls[0]["max_tokens"] == 16384
    assert fake.calls[0]["extra"] == {"verbosity": "high"}


def test_generate_notes_rejects_non_json(tmp_path, base_cfg, monkeypatch):
    transcript = _write(tmp_path, "t.txt", "hello")
    monkeypatch.setitem(gn._PROVIDERS, "openai", FakeProvider("Sorry, I can't do that."))
    with pytest.raises(ValueError, match="valid JSON"):
        gn.generate_notes(transcript, base_cfg)


def test_missing_context_file_is_not_an_error(tmp_path, base_cfg):
    base_cfg["llm"]["context_file"] = str(tmp_path / "does-not-exist.md")
    assert gn._load_project_context(base_cfg) == ""
