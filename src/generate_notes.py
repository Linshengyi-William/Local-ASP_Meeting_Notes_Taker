"""
generate_notes.py — Generate structured meeting notes from a cleaned transcript.

Default provider: OpenAI (gpt-4o).
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

_SYSTEM_EN = """\
You are an expert meeting notes writer. Given a meeting transcript (which may
contain Chinese, English, or a mix of both), produce concise, structured notes
in English. Be faithful to the content — do not fabricate details.
"""

_SYSTEM_ZH = """\
你是一位专业的会议记录整理助手。根据提供的会议转录文本（可能包含中文、英文或混合内容），
生成简洁、结构化的会议记录（中文输出）。保持内容忠实，不要添加转录中没有的信息。
"""

_SYSTEM_BILINGUAL = """\
You are an expert meeting notes writer. Given a meeting transcript (which may
contain Chinese, English, or a mix of both), produce structured notes with
each section written first in English then in Chinese (简体中文).
Be faithful to the content — do not fabricate details.
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

Please extract and return a JSON object with the following keys:
  "title"           : A short descriptive meeting title (inferred from context)
  "summary"         : 2-4 sentence executive summary
  "key_points"      : list of strings — major discussion topics
  "decisions"       : list of strings — decisions that were made
  "action_items"    : list of objects with keys "owner", "task", "due" (due may be null)
  "open_questions"  : list of strings — unresolved questions or topics requiring follow-up
  "risks"           : list of strings — risks or concerns raised (may be empty list)
  "tags"            : list of 1-5 short topic tags (e.g. ["product", "roadmap", "Q2"])

Return ONLY valid JSON with these keys and nothing else.
"""


# ── Provider backends ─────────────────────────────────────────────────────────

def _call_openai(system: str, user: str, model: str, max_tokens: int, temperature: float, max_retries: int = 3) -> str:
    from openai import OpenAI
    from utils import retry_with_backoff, _openai_retryable

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set. Add it to your .env file."
        )
    client = OpenAI(api_key=api_key)
    logger.info("Calling OpenAI (%s) to generate meeting notes ...", model)

    def _do_call():
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        return response.choices[0].message.content.strip()

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
    "openai":    "gpt-4o",
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
) -> dict:
    """
    Read the cleaned transcript at *transcript_path*, call the LLM, and return
    a structured dict of meeting notes.

    Args:
        transcript_path: Path to the cleaned .txt transcript.
        cfg:             Full config dict.
        output_dir:      If given, saves the result as JSON here.
        provider:        "openai" or "anthropic". Overrides config when set.
    """
    transcript_path = Path(transcript_path)
    transcript_text = transcript_path.read_text(encoding="utf-8")

    lcfg = cfg.get("llm", {})
    provider    = (provider or lcfg.get("provider", "openai")).lower()
    model       = _resolve_model(provider, lcfg.get("model", _DEFAULT_MODELS[provider]))
    max_tokens  = int(lcfg.get("max_tokens", 4096))
    temperature = float(lcfg.get("temperature", 0.3))
    output_lang = lcfg.get("output_language", "bilingual")
    max_retries = int(lcfg.get("max_retries", 3))

    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose: {list(_PROVIDERS)}")

    system_prompt = _SYSTEM_MAP.get(output_lang, _SYSTEM_BILINGUAL)
    user_message  = _USER_TEMPLATE.format(transcript=transcript_text)

    raw_response = _PROVIDERS[provider](system_prompt, user_message, model, max_tokens, temperature, max_retries)

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
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = args.output_dir or cfg["paths"]["notes"]
    notes = generate_notes(args.transcript, cfg, out_dir, provider=args.provider)
    print(json.dumps(notes, ensure_ascii=False, indent=2))
