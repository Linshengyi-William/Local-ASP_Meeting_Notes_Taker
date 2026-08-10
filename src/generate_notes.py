"""
generate_notes.py — Generate structured meeting notes from a cleaned transcript.

Default provider: OpenAI (gpt-5-mini).
Override at runtime with --provider anthropic (uses claude-sonnet-4-6).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Prompt templates ──────────────────────────────────────────────────────────

_EXHAUSTIVE_RULES_EN = """\
Your single overriding priority is EXHAUSTIVE COMPLETENESS. These notes are
meant to REPLACE the transcript. A reader who was not in the meeting must be
able to reconstruct everything that happened, what was decided, and why,
without ever opening the original.

Follow these rules strictly:

1. COVER THE ENTIRE MEETING, START TO FINISH. Work through the transcript in
   order and account for every segment of it. Never let the middle or the end
   of a long meeting collapse into a sentence or two — this is the most common
   failure mode and it is unacceptable.
2. PRESERVE ALL SPECIFICS VERBATIM: numbers, prices, dates, deadlines, metrics,
   percentages, names of people, companies, products, tools, technologies, file
   names, URLs, and any concrete example someone gave. Never round, generalise,
   or replace a specific with a vague phrase ("some issues", "a few options").
3. CAPTURE THE REASONING, not just the outcome. Record why something was
   proposed, what the alternatives were, what objections were raised, who
   disagreed and on what grounds, and how it was ultimately resolved.
4. KEEP MINORITY AND DISSENTING VIEWS, tangents, side discussions, and points
   that were raised and dropped. Note explicitly when something was left
   unresolved or deferred.
5. ATTRIBUTE STATEMENTS to whoever made them whenever the speaker is
   identifiable from the transcript.
6. NEVER COMPRESS FOR BREVITY. There is no length limit and no reward for being
   concise. Being too short is a failure; being long and thorough is correct.
   Longer meetings must produce proportionally longer notes.
7. BE FAITHFUL. Never invent, infer, or embellish anything that was not
   actually said. If something was genuinely unclear or inaudible in the
   transcript, say so rather than guessing.
8. NEVER INVENT AN OWNER. For action items, use a person's name only if the
   transcript actually identifies who took the task on. If nobody was named,
   write "Unassigned" — do not fabricate a plausible-sounding team or role
   (a transcript that mentions "testing" is discussing a test environment,
   not a testing team). Inventing owners is worse than leaving them blank.
"""

_SYSTEM_EN = f"""\
You are an expert meeting notes writer. Given a meeting transcript (which may
contain Chinese, English, or a mix of both), produce structured notes in English.

{_EXHAUSTIVE_RULES_EN}"""

_EXHAUSTIVE_RULES_ZH = """\
你的最高优先级是**极致的完整性**。这份记录的目标是**取代**转录原文——没有参会的读者
必须能够仅凭这份记录还原会议的全部内容、所有决定及其原因，而无需再打开原始转录。

请严格遵守以下规则：

1. **完整覆盖整场会议，从头到尾。** 按时间顺序梳理转录文本，确保每一段都有对应的记录。
   绝不允许长会议的中段或结尾被压缩成一两句话——这是最常见也最不可接受的错误。
2. **完整保留所有具体信息：** 数字、金额、日期、截止时间、指标、百分比、人名、公司名、
   产品名、工具、技术、文件名、网址，以及任何人举过的具体例子。不要取整、不要泛化，
   不要用"一些问题""几个方案"这类模糊说法替代具体内容。
3. **记录推理过程，而不只是结论。** 说明某个方案为什么被提出、有哪些备选方案、
   有人提出了什么反对意见、谁不同意以及理由是什么、最终如何解决。
4. **保留少数意见和反对意见**，以及跑题内容、附带讨论、提出后又被放下的话题。
   如果某件事没有结论或被推迟，要明确写出来。
5. **注明发言人。** 只要能从转录中识别出说话的人，就标明这段话是谁说的。
6. **绝不为了简洁而压缩。** 没有篇幅限制，简洁不会得到任何加分。
   太短就是失败；详尽冗长才是正确的。会议越长，记录就必须越长。
7. **忠实于原文。** 不要编造、推测或美化任何没有真正说过的内容。
   如果转录中某处确实含糊不清或听不清楚，就如实说明，不要猜测。
8. **绝不编造负责人。** 待办事项的负责人只能填写转录中明确认领该任务的人。
   如果没有人被点名，就写"待定"——不要编造一个听起来合理的团队或职位
   （转录里提到"testing"通常指测试环境，不是某个测试团队）。
   编造负责人比留空更糟糕。
"""

_SYSTEM_ZH = f"""\
你是一位专业的会议记录整理助手。根据提供的会议转录文本（可能包含中文、英文或混合内容），
生成结构化的会议记录，并且**全部使用简体中文输出**。即使转录原文是英文或其他语言，
也要翻译成简体中文；不要使用繁体字。

{_EXHAUSTIVE_RULES_ZH}"""

_SYSTEM_BILINGUAL = f"""\
You are an expert meeting notes writer. Given a meeting transcript (which may
contain Chinese, English, or a mix of both), produce structured notes with
each section written first in English then in Chinese (简体中文).

{_EXHAUSTIVE_RULES_EN}
Both language versions must be equally detailed — never write a full English
section and then a short Chinese gloss of it, or vice versa.
"""

_SYSTEM_MAP = {
    "en": _SYSTEM_EN,
    "zh": _SYSTEM_ZH,
    "bilingual": _SYSTEM_BILINGUAL,
}

_USER_TEMPLATE = """\
Below is the cleaned transcript of a meeting.

---TRANSCRIPT START---
{transcript}
---TRANSCRIPT END---
{length_directive}
Please extract and return a JSON object with the following keys:

  "title"           : A short descriptive meeting title (inferred from context)

  "tldr"            : 1-2 sentences maximum. The absolute quickest recap: what
                      this meeting was about and the single most critical outcome
                      or decision. Someone reading only this should know whether
                      the meeting is relevant to them. This is the ONLY field
                      that should be short.

  "summary"         : A comprehensive narrative summary covering the purpose and
                      context of the meeting, every major topic discussed, the
                      conclusions reached, and any important background or
                      constraints. Scale the length to the meeting: a brief
                      check-in may need 4-6 sentences; an hour-long discussion
                      needs several full paragraphs. Never cut content for
                      brevity.

  "discussion"      : THE MOST IMPORTANT FIELD — a chronological, section-by-
                      section walkthrough of the entire meeting. This is what
                      guarantees nothing is lost, so give it the bulk of your
                      effort. A list of objects, each with:
                        "topic"   : short heading naming this segment of the
                                    discussion
                        "details" : list of strings — the substance of that
                                    segment in depth. Each string is one
                                    specific point, argument, example, number,
                                    concern, or exchange. Include who said what
                                    where identifiable, the reasoning behind
                                    positions, alternatives considered, and
                                    objections raised.
                      Create one entry for EVERY distinct topic the meeting
                      moved through, in the order it was discussed — including
                      brief topics, tangents, and small talk that carried real
                      information. A substantial meeting should produce many
                      entries with many details each. Do NOT merge unrelated
                      topics together to shorten this list, and do NOT let
                      coverage thin out toward the end of the transcript.

  "key_points"      : list of strings — the headline takeaways, each
                      self-contained (what was discussed AND the conclusion or
                      status). This is a digest of "discussion", not a
                      replacement for it.

  "decisions"       : list of strings — every decision that was made, including
                      minor ones; include the reasoning and who made the call
                      when stated

  "action_items"    : list of objects with keys "owner", "task", "due"
                      (due may be null); capture every commitment made, however
                      small, including tentative ones. Make "task" specific
                      enough to act on without further context.

  "open_questions"  : list of strings — every unresolved question or topic that
                      needs follow-up, including things that were tabled or
                      deferred

  "risks"           : list of strings — risks, concerns, blockers, or objections
                      raised (may be empty list)

  "tags"            : list of 1-5 short topic tags (e.g. ["product", "roadmap", "Q2"])

Return ONLY valid JSON with these keys and nothing else.
"""


# ── Project context ───────────────────────────────────────────────────────────

_CONTEXT_PREAMBLE = """\

---STANDING PROJECT CONTEXT START---
The following is verified background about this organisation, supplied by the
user. It OUTRANKS the transcript wherever the two conflict, because
speech-to-text mishears names and jargon.

Apply it silently: correct misspelled names and garbled terms to their proper
forms, and never contradict the organisational facts stated here (for example,
do not invent a team that this context says does not exist). Do not mention
this context, quote it, or add a note explaining that you corrected something —
just produce notes that are already correct.

{context}
---STANDING PROJECT CONTEXT END---
"""


def _load_project_context(cfg: dict, base_dir: Optional[Path] = None) -> str:
    """
    Read the user-maintained glossary (config/context.md by default).

    Returns "" when the file is absent or empty, in which case the prompt is
    built exactly as it was before this feature existed.
    """
    rel = cfg.get("llm", {}).get("context_file", "config/context.md")
    if not rel:
        return ""

    path = Path(rel)
    if not path.is_absolute():
        base_dir = base_dir or Path(__file__).parent.parent
        path = base_dir / rel

    if not path.exists():
        logger.debug("No project context file at %s — skipping.", path)
        return ""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""

    logger.info("Loaded project context from %s (%d chars).", path, len(text))
    return _CONTEXT_PREAMBLE.format(context=text)


# ── Output sizing ─────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """
    Rough token count for mixed Chinese/English text. CJK characters are roughly
    one token each; Latin text averages ~4 characters per token.
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return int(cjk + (len(text) - cjk) / 4)


def _length_directive(transcript_text: str) -> str:
    """
    Tell the model how substantial the transcript is and how much output is
    expected. Without this, models compress long meetings into roughly the same
    amount of output as short ones.
    """
    tokens = _estimate_tokens(transcript_text)
    if tokens < 2000:
        expectation = (
            "This is a SHORT meeting. Still cover every topic it touched, but the "
            "notes will naturally be modest in length."
        )
    elif tokens < 8000:
        expectation = (
            "This is a MEDIUM-LENGTH meeting. Expect roughly 5-10 entries in "
            "\"discussion\", each with several detailed points."
        )
    elif tokens < 20000:
        expectation = (
            "This is a LONG meeting. Expect roughly 10-20 entries in "
            "\"discussion\", each with many detailed points. Do not compress it."
        )
    else:
        expectation = (
            "This is a VERY LONG meeting. Expect 20+ entries in \"discussion\", "
            "each with many detailed points. The single biggest risk here is that "
            "you summarise the opening thoroughly and then rush the rest — do not "
            "do that. Give the later parts of the transcript the same depth as "
            "the beginning."
        )
    return (
        f"\nThe transcript above is approximately {tokens:,} tokens long. "
        f"{expectation}\n"
    )


def _adaptive_max_tokens(transcript_text: str, floor: int, cap: int) -> int:
    """
    Size the output budget to the transcript.

    Exhaustive notes approach the length of the transcript itself, so a fixed
    budget silently truncates long meetings — exactly the detail loss we're
    trying to avoid. This is only a ceiling: the API bills for tokens actually
    generated, so a generous cap costs nothing on short meetings.
    """
    needed = int(_estimate_tokens(transcript_text) * 1.5) + 4000
    return max(floor, min(needed, cap))


# ── Provider backends ─────────────────────────────────────────────────────────

# OpenAI reasoning models (gpt-5 family, o-series) have a different parameter
# surface from the older chat models:
#   - "max_tokens" is rejected; they use "max_completion_tokens"
#   - "temperature" only accepts the default (1); any other value is a 400
#   - they support "reasoning_effort" / "verbosity", which chat models don't
# Older models (gpt-4o, gpt-4o-mini) keep the classic parameters.
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_openai_reasoning_model(model: str) -> bool:
    return model.startswith(_OPENAI_REASONING_PREFIXES)


def _call_openai(
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
) -> str:
    from openai import OpenAI
    from utils import retry_with_backoff, _openai_retryable

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set. Add it to your .env file."
        )
    client = OpenAI(api_key=api_key)

    params: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }

    if _is_openai_reasoning_model(model):
        # Reasoning tokens are billed against this budget too, so it needs to be
        # comfortably larger than the notes themselves.
        params["max_completion_tokens"] = max_tokens
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
        if verbosity:
            params["verbosity"] = verbosity
        if temperature != 1.0:
            logger.debug(
                "Ignoring llm.temperature=%s — %s only supports the default value.",
                temperature, model,
            )
    else:
        params["max_tokens"] = max_tokens
        params["temperature"] = temperature

    logger.info("Calling OpenAI (%s) to generate meeting notes ...", model)

    def _do_call():
        response = client.chat.completions.create(**params)
        content = response.choices[0].message.content
        if not content:
            finish_reason = response.choices[0].finish_reason
            raise ValueError(
                f"{model} returned an empty response (finish_reason={finish_reason}). "
                "If finish_reason is 'length', raise llm.max_tokens in config.yaml — "
                "reasoning models spend part of that budget before writing output."
            )
        return content.strip()

    return retry_with_backoff(
        _do_call,
        max_retries=max_retries,
        base_delay=2.0,
        retryable_exceptions=_openai_retryable(),
    )


def _call_anthropic(system: str, user: str, model: str, max_tokens: int, temperature: float, max_retries: int = 3) -> str:
    import anthropic
    from utils import retry_with_backoff, _anthropic_retryable

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    client = anthropic.Anthropic(api_key=api_key)
    logger.info("Calling Anthropic (%s) to generate meeting notes ...", model)

    def _do_call():
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text.strip()

    return retry_with_backoff(
        _do_call,
        max_retries=max_retries,
        base_delay=2.0,
        retryable_exceptions=_anthropic_retryable(),
    )


_PROVIDERS = {
    "openai":    _call_openai,
    "anthropic": _call_anthropic,
}

# Default models per provider if the config model looks like it belongs to the
# other provider (e.g. user switches --provider without updating config.yaml)
_DEFAULT_MODELS = {
    "openai":    "gpt-5-mini",
    "anthropic": "claude-sonnet-4-6",
}


def _resolve_model(provider: str, model_from_cfg: str) -> str:
    """
    If the config model is clearly for the *other* provider, swap in the
    default for the requested provider instead of sending a bad model name.
    """
    openai_prefixes    = ("gpt-", "o1", "o3", "o4")
    anthropic_prefixes = ("claude-",)

    if provider == "openai" and model_from_cfg.startswith(anthropic_prefixes):
        return _DEFAULT_MODELS["openai"]
    if provider == "anthropic" and model_from_cfg.startswith(openai_prefixes):
        return _DEFAULT_MODELS["anthropic"]
    return model_from_cfg


# ── Main function ─────────────────────────────────────────────────────────────

def generate_notes(
    transcript_path: str | Path,
    cfg: dict,
    output_dir: Optional[str | Path] = None,
    provider: Optional[str] = None,
    lang: Optional[str] = None,
) -> dict:
    """
    Read the cleaned transcript at *transcript_path*, call the LLM, and return
    a structured dict of meeting notes.

    Args:
        transcript_path: Path to the cleaned .txt transcript.
        cfg:             Full config dict.
        output_dir:      If given, saves the result as JSON here.
        provider:        "openai" or "anthropic". Overrides config when set.
        lang:            Output language: "en" | "zh" | "bilingual".
                         Overrides llm.output_language in config when set.
    """
    transcript_path = Path(transcript_path)
    transcript_text = transcript_path.read_text(encoding="utf-8")

    lcfg = cfg.get("llm", {})
    provider    = (provider or lcfg.get("provider", "openai")).lower()
    model       = _resolve_model(provider, lcfg.get("model", _DEFAULT_MODELS[provider]))
    temperature = float(lcfg.get("temperature", 0.3))
    output_lang = (lang or lcfg.get("output_language", "bilingual")).lower()
    max_retries = int(lcfg.get("max_retries", 3))

    max_tokens = _adaptive_max_tokens(
        transcript_text,
        floor=int(lcfg.get("max_tokens", 16000)),
        cap=int(lcfg.get("max_tokens_cap", 64000)),
    )

    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose: {list(_PROVIDERS)}")

    system_prompt = _SYSTEM_MAP.get(output_lang, _SYSTEM_BILINGUAL) + _load_project_context(cfg)
    user_message  = _USER_TEMPLATE.format(
        transcript=transcript_text,
        length_directive=_length_directive(transcript_text),
    )

    logger.info(
        "Transcript ~%d tokens; allowing up to %d output tokens.",
        _estimate_tokens(transcript_text), max_tokens,
    )

    # Options only understood by the OpenAI reasoning models; left out entirely
    # when unset so we never send them to a model that would reject them.
    extra: dict = {}
    if provider == "openai":
        if lcfg.get("reasoning_effort"):
            extra["reasoning_effort"] = str(lcfg["reasoning_effort"]).lower()
        # Default to "high" — this directly controls how much the gpt-5 family
        # writes, and detailed notes are the whole point here.
        extra["verbosity"] = str(lcfg.get("verbosity", "high")).lower()

    raw_response = _PROVIDERS[provider](
        system_prompt, user_message, model, max_tokens, temperature, max_retries, **extra
    )

    # Strip markdown code fences if the model wrapped the JSON
    if raw_response.startswith("```"):
        raw_response = "\n".join(
            line for line in raw_response.splitlines()
            if not line.startswith("```")
        ).strip()

    try:
        notes = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned non-JSON response:\n%s", raw_response)
        raise ValueError("LLM did not return valid JSON.") from exc

    notes["_source"]   = transcript_path.name
    notes["_provider"] = provider
    notes["_model"]    = model
    notes["_language"] = output_lang

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / (transcript_path.stem + "_notes.json")
        out_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Meeting notes saved → %s", out_path)

    return notes


# ── CLI helper ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import yaml
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Generate meeting notes from a cleaned transcript.")
    parser.add_argument("transcript", help="Path to cleaned transcript (.txt)")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--provider", default=None, choices=["openai", "anthropic"],
        help="LLM provider to use (overrides config). Default: openai",
    )
    parser.add_argument(
        "--lang", default=None, choices=["en", "zh", "bilingual"],
        help="Output language (overrides config). zh = Simplified Chinese.",
    )
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = args.output_dir or cfg["paths"]["notes"]
    notes = generate_notes(args.transcript, cfg, out_dir, provider=args.provider, lang=args.lang)
    print(json.dumps(notes, ensure_ascii=False, indent=2))
