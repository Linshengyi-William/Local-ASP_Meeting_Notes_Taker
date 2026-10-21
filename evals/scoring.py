"""
scoring.py — Pure, deterministic scoring of generated meeting notes against a
case's expected.json. No network, no LLM: the same function scores live runs
and replayed outputs, and it is unit-tested in tests/test_eval_scoring.py.

What gets measured (all of these are failure modes seen on real meetings):

  schema            every top-level key the Notion writer expects is present
  name_recall       canonical names from the case glossary appear in the notes
  name_leaks        known mis-hearings of those names survive into the notes
  term_leaks        garbled jargon ("ohms" for OMS) survives into the notes
  action_recall     expected action items are found, with the right owner
  invented_owners   action items assigned to someone/something that doesn't
                    exist (e.g. a "testing team" because the meeting mentioned
                    the testing *environment*)
  traditional_chars Traditional Chinese characters in zh / bilingual output
"""
from __future__ import annotations

import re
from typing import Any, Iterable

REQUIRED_KEYS = (
    "title", "tldr", "summary", "discussion", "key_points",
    "decisions", "action_items", "open_questions", "risks", "tags",
)

# Owner strings that mean "nobody took this on". Matched case-insensitively as
# substrings, so "Unassigned / 待定" in bilingual output also counts.
_UNASSIGNED_MARKERS = (
    "unassigned", "not assigned", "no owner", "tbd",
    "待定", "未分配", "未指定", "未认领", "无人认领", "暂无",
)
_UNASSIGNED_EXACT = {"", "-", "—", "n/a", "na", "none", "null", "无"}

# Fallback list of Traditional-only characters (each verified with OpenCC t2s to
# map to a different Simplified character). Used only when OpenCC isn't
# installed; with OpenCC every character is checked.
_TRADITIONAL_FALLBACK = frozenset(
    "這們會說時間問題測試資權應該對發點東車長門實現體與為關係經過還進運動學國語讓認識"
    "變開從個來後裡嗎錄議單員號準備確負責務項決計劃續統碼線級據庫帳戶給寫讀聽見處際區"
    "標雖謝請隊組織鐘環數錯預費論專檔佈監證書週啟傳圖網頁買賣價錢錶產業報導邊擊幫補"
    "聯絡選擇並鬆"
)

_CJK = re.compile(r"[一-鿿]")


def _t2s_converter():
    try:
        import opencc  # opencc-python-reimplemented
    except ImportError:
        return None
    return opencc.OpenCC("t2s")


# ── Text helpers ──────────────────────────────────────────────────────────────

def iter_strings(value: Any) -> Iterable[str]:
    """Yield every string inside a notes structure, skipping _private keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from iter_strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.startswith("_"):
                continue
            yield from iter_strings(v)


def notes_text(notes: dict) -> str:
    return "\n".join(iter_strings(notes))


def count_term(text: str, term: str) -> int:
    """
    Count occurrences of *term*, mirroring generate_notes._apply_corrections:
    ASCII terms match on word boundaries (case-insensitive here, since a leak
    is a leak whatever its casing); CJK terms match anywhere.
    """
    if re.fullmatch(r"[A-Za-z0-9 .\-]+", term):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
        return len(re.findall(pattern, text, flags=re.IGNORECASE))
    return text.count(term)


def contains_alias(haystack: str, aliases: Iterable[str]) -> bool:
    h = haystack.casefold()
    return any(a.casefold() in h for a in aliases)


def is_unassigned(owner: Any) -> bool:
    if owner is None:
        return True
    o = str(owner).strip().casefold()
    return o in _UNASSIGNED_EXACT or any(m in o for m in _UNASSIGNED_MARKERS)


def traditional_chars(text: str) -> list[str]:
    """Return the distinct Traditional-only characters found in *text*."""
    conv = _t2s_converter()
    found: dict[str, None] = {}
    for ch in text:
        if not _CJK.match(ch):
            continue
        if conv is not None:
            if conv.convert(ch) != ch:
                found[ch] = None
        elif ch in _TRADITIONAL_FALLBACK:
            found[ch] = None
    return list(found)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _owner_aliases(spec: dict) -> list[str] | None:
    owner = spec.get("owner")
    if owner is None:
        return None
    return [owner] if isinstance(owner, str) else list(owner)


def score_action_items(produced: list, expected: list[dict]) -> tuple[list[dict], int]:
    """
    Greedily match each expected item to one produced item whose task mentions
    any of its keywords and whose owner is right. Returns (per-item results,
    matched count).
    """
    items = [ai for ai in produced if isinstance(ai, dict)]
    used: set[int] = set()
    results: list[dict] = []
    matched = 0
    for spec in expected:
        aliases = _owner_aliases(spec)
        hit = None
        near_miss = None
        for idx, ai in enumerate(items):
            if idx in used:
                continue
            task = str(ai.get("task", ""))
            if not contains_alias(task, spec["keywords"]):
                continue
            owner = ai.get("owner")
            owner_ok = is_unassigned(owner) if aliases is None else contains_alias(str(owner or ""), aliases)
            if owner_ok:
                hit = idx
                break
            near_miss = near_miss if near_miss is not None else idx
        if hit is not None:
            used.add(hit)
            matched += 1
            results.append({"id": spec["id"], "ok": True})
        else:
            detail = "not found"
            if near_miss is not None:
                detail = f"found with wrong owner: {items[near_miss].get('owner')!r}"
            results.append({"id": spec["id"], "ok": False, "detail": detail})
    return results, matched


def score_case(notes: dict, expected: dict) -> dict:
    """Score one case. Returns a flat, JSON-serialisable result dict."""
    text = notes_text(notes)
    lang = expected.get("lang", "bilingual")

    missing_keys = [k for k in REQUIRED_KEYS if k not in notes]

    names: dict[str, list[str]] = expected.get("names", {})
    names_found = [n for n in names if count_term(text, n) > 0]
    name_leaks = {
        v: c for variants in names.values() for v in variants
        if (c := count_term(text, v)) > 0
    }
    term_leaks = {
        t: c for t in expected.get("forbidden_text", []) if (c := count_term(text, t)) > 0
    }

    produced = notes.get("action_items") or []
    expected_items = expected.get("action_items", [])
    action_results, matched = score_action_items(produced, expected_items)

    allowed = list(expected.get("allowed_owners", [])) + list(names)
    forbidden_owner_terms = expected.get("forbidden_owners", [])
    invented: list[str] = []
    for ai in produced:
        if not isinstance(ai, dict):
            continue
        owner = ai.get("owner")
        if is_unassigned(owner):
            continue
        owner_s = str(owner)
        if contains_alias(owner_s, forbidden_owner_terms) or not contains_alias(owner_s, allowed):
            invented.append(owner_s)

    trad = traditional_chars(text) if lang in ("zh", "bilingual") else []

    name_recall = len(names_found) / len(names) if names else 1.0
    action_recall = matched / len(expected_items) if expected_items else 1.0
    min_recall = float(expected.get("min_action_recall", 0.75))

    failures: list[str] = []
    if missing_keys:
        failures.append(f"missing keys: {', '.join(missing_keys)}")
    if name_recall < 1.0:
        failures.append(f"names missing: {', '.join(n for n in names if n not in names_found)}")
    if name_leaks:
        failures.append(f"misheard names leaked: {name_leaks}")
    if term_leaks:
        failures.append(f"garbled terms leaked: {term_leaks}")
    if action_recall < min_recall:
        failures.append(f"action recall {action_recall:.0%} < {min_recall:.0%}")
    if invented:
        failures.append(f"invented owners: {invented}")
    if trad:
        failures.append(f"Traditional characters: {''.join(trad)}")

    return {
        "lang": lang,
        "passed": not failures,
        "failures": failures,
        "missing_keys": missing_keys,
        "name_recall": round(name_recall, 3),
        "name_leaks": name_leaks,
        "term_leaks": term_leaks,
        "action_recall": round(action_recall, 3),
        "action_items": action_results,
        "invented_owners": invented,
        "traditional_chars": trad,
        "n_discussion_topics": len(notes.get("discussion") or []),
        "output_chars": len(text),
    }
