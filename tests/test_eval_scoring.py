"""Tests for the eval scorer, plus structural checks on every shipped case."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import scoring  # noqa: E402
from generate_notes import _parse_corrections  # noqa: E402

CASES = sorted(p for p in (ROOT / "evals" / "cases").iterdir() if p.is_dir())


# ── Case files are well-formed ────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_case_is_well_formed(case: Path):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    transcript = (case / "transcript.txt").read_text(encoding="utf-8")
    context = (case / "context.md").read_text(encoding="utf-8")

    assert expected["lang"] in ("en", "zh", "bilingual")
    assert transcript.startswith("[Source: ")
    assert expected["names"] and expected["action_items"]
    ids = [a["id"] for a in expected["action_items"]]
    assert len(ids) == len(set(ids))
    for item in expected["action_items"]:
        assert item["keywords"], item["id"]
    # each case's glossary must be parseable by the real pipeline
    assert _parse_corrections(context)
    # every misheard variant must really appear in the transcript, otherwise
    # the case isn't testing what it claims to
    for variants in expected["names"].values():
        assert any(v in transcript for v in variants), variants


# ── Scorer behaviour ──────────────────────────────────────────────────────────

def _load(case_name: str) -> dict:
    return json.loads((ROOT / "evals" / "cases" / case_name / "expected.json").read_text(encoding="utf-8"))


GOOD_ZH_NOTES = {
    "title": "Sprint 规划",
    "tldr": "本次 sprint 专注修复 bug，不加新功能。",
    "summary": "Marco 负责修复登录问题，Yuki 负责发布说明，Shen Hao 确认 staging 预算。",
    "discussion": [{"topic": "登录 bug", "details": ["Marco 发现 SameSite 设置有问题"]}],
    "key_points": ["回归测试由 Platform team 负责"],
    "decisions": ["本 sprint 不加新 feature"],
    "action_items": [
        {"owner": "Marco", "task": "周五前修复 SSO 登录 bug 并在 staging 验证", "due": "周五"},
        {"owner": "Yuki", "task": "周三前完成 2.4 版本 release notes 并发到 Jira", "due": "周三"},
        {"owner": "Shen Hao", "task": "与财务确认 staging 数据库重建预算", "due": "下周一"},
        {"owner": "Platform team", "task": "编写回归测试自动化脚本", "due": None},
        {"owner": "待定", "task": "更新 API 文档（2.4 新增三个接口）", "due": None},
    ],
    "open_questions": ["release 是否从周四改到周二"],
    "risks": ["login bug 未修复可能导致 2.4 推迟"],
    "tags": ["sprint"],
    "_source": "Marko.txt",  # private keys are ignored
}


def test_good_notes_pass():
    result = scoring.score_case(GOOD_ZH_NOTES, _load("sprint-planning-zh"))
    assert result["passed"], result["failures"]
    assert result["action_recall"] == 1.0
    assert result["name_recall"] == 1.0


def test_detects_every_failure_mode():
    bad = json.loads(json.dumps(GOOD_ZH_NOTES, ensure_ascii=False))
    bad["summary"] = "Marko 和 Yucky 在吉拉上讨論這個問題。"  # leaks + Traditional chars
    bad["action_items"][3]["owner"] = "QA team"             # invented owner
    bad["action_items"][4]["owner"] = "Yuki"               # unowned task given an owner
    del bad["risks"]

    r = scoring.score_case(bad, _load("sprint-planning-zh"))
    assert not r["passed"]
    assert r["missing_keys"] == ["risks"]
    assert r["name_leaks"] == {"Marko": 1, "Yucky": 1}
    assert r["term_leaks"] == {"吉拉": 1}
    assert "QA team" in r["invented_owners"]
    assert set("論這個問題") <= set(r["traditional_chars"])
    statuses = {a["id"]: a["ok"] for a in r["action_items"]}
    assert statuses["regression-automation"] is False
    assert statuses["api-docs"] is False
    assert r["action_recall"] == pytest.approx(0.6)


@pytest.mark.parametrize("owner", [None, "", "—", "Unassigned", "Unassigned / 待定", "待定", "TBD", "未分配"])
def test_unassigned_forms(owner):
    assert scoring.is_unassigned(owner)


@pytest.mark.parametrize("owner", ["Marco", "Ops team", "Unas"])
def test_assigned_forms(owner):
    assert not scoring.is_unassigned(owner)


def test_count_term_uses_word_boundaries_for_ascii_only():
    assert scoring.count_term("Pria and Priya", "Pria") == 1
    assert scoring.count_term("ohms OHMS ohmsx", "ohms") == 2
    assert scoring.count_term("马可说马可", "马可") == 2


def test_traditional_detection_has_no_false_positives_on_simplified():
    simplified = "这个问题我们下周再讨论，测试环境的数据库需要重建，发布之后我会盯一个小时的监控。"
    assert scoring.traditional_chars(simplified) == []
    assert scoring.traditional_chars("發佈之後我會盯監控") != []


def test_traditional_fallback_list_is_traditional_only():
    opencc = pytest.importorskip("opencc")
    t2s = opencc.OpenCC("t2s")
    assert [c for c in scoring._TRADITIONAL_FALLBACK if t2s.convert(c) == c] == []


def test_english_output_is_not_checked_for_script():
    notes = dict(GOOD_ZH_NOTES, summary="這個")
    expected = dict(_load("sprint-planning-zh"), lang="en")
    assert scoring.score_case(notes, expected)["traditional_chars"] == []


# ── run_eval.py replay mode (no API) ──────────────────────────────────────────

def test_run_eval_replay_end_to_end(tmp_path):
    (tmp_path / "sprint-planning-zh.json").write_text(json.dumps(GOOD_ZH_NOTES, ensure_ascii=False), encoding="utf-8")
    summary = tmp_path / "summary.md"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "evals" / "run_eval.py"),
         "--replay", str(tmp_path), "--case", "sprint-planning-zh",
         "--markdown", str(summary), "--strict"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    md = summary.read_text(encoding="utf-8")
    assert "| sprint-planning-zh | zh | PASS |" in md
    assert "1/1 cases passed" in md


def test_run_eval_replay_strict_fails_on_bad_output(tmp_path):
    bad = dict(GOOD_ZH_NOTES, summary="Marko 這個")
    (tmp_path / "sprint-planning-zh.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "evals" / "run_eval.py"),
         "--replay", str(tmp_path), "--case", "sprint-planning-zh", "--strict"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 1
    assert "FAIL" in proc.stdout
