"""
utils.py — Shared utility functions for the meeting notes pipeline.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Retry helpers ─────────────────────────────────────────────────────────────

def retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (),
    **kwargs: Any,
) -> Any:
    """
    Call fn(*args, **kwargs) with exponential backoff on retryable exceptions.

    Delay schedule (base_delay=2, backoff_factor=2):
        attempt 1 failure → wait 2 s
        attempt 2 failure → wait 4 s
        attempt 3 failure → wait 8 s  (then give up)

    Raises the last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "All %d retries exhausted for %s: %s",
                    max_retries, getattr(fn, "__name__", repr(fn)), exc,
                )
                raise
            delay = base_delay * (backoff_factor ** attempt)
            logger.warning(
                "Retryable error in %s (attempt %d/%d): %s — retrying in %.1f s",
                getattr(fn, "__name__", repr(fn)), attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)
    raise last_exc  # unreachable; satisfies type checkers


def retry_with_backoff_predicate(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    backoff_factor: float = 2.0,
    is_retryable: Callable[[Exception], bool] = lambda _: False,
    **kwargs: Any,
) -> Any:
    """
    Same as retry_with_backoff but uses a callable predicate instead of an
    exception type tuple.  Useful when the same exception class is retryable
    only for specific status codes (e.g. Notion's HTTPResponseError on 429/503
    but not on 400/404).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not is_retryable(exc):
                raise
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "All %d retries exhausted for %s: %s",
                    max_retries, getattr(fn, "__name__", repr(fn)), exc,
                )
                raise
            delay = base_delay * (backoff_factor ** attempt)
            logger.warning(
                "Retryable error in %s (attempt %d/%d): %s — retrying in %.1f s",
                getattr(fn, "__name__", repr(fn)), attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)
    raise last_exc  # unreachable


# ── Lazy retryable exception sets ─────────────────────────────────────────────
# Imported lazily so that missing provider packages don't cause ImportError
# at module load time (e.g. a user who only uses Anthropic hasn't installed openai).

def _openai_retryable() -> tuple[type[Exception], ...]:
    from openai import RateLimitError, APIConnectionError, APITimeoutError
    return (RateLimitError, APIConnectionError, APITimeoutError)


def _anthropic_retryable() -> tuple[type[Exception], ...]:
    from anthropic import RateLimitError, APIConnectionError, APITimeoutError
    return (RateLimitError, APIConnectionError, APITimeoutError)


def _notion_is_retryable(exc: Exception) -> bool:
    """Return True for Notion HTTP errors that are safe to retry (429/500/503)."""
    try:
        from notion_client.errors import HTTPResponseError
        if isinstance(exc, HTTPResponseError):
            return exc.status in (429, 500, 503)
    except ImportError:
        pass
    return False


# ── Config validation ─────────────────────────────────────────────────────────

def validate_config(
    cfg: dict,
    *,
    skip_notion: bool = False,
    provider_override: str | None = None,
) -> None:
    """
    Validate required config keys and environment variables at startup.

    Prints a numbered list of actionable errors and calls sys.exit(1) if any
    are found.  All checks run before exiting so the user sees every problem
    at once rather than fixing them one by one.

    Args:
        cfg:               Loaded config dict.
        skip_notion:       If True, skip Notion-related checks (--no-notion).
        provider_override: CLI --provider value (overrides config for key checks).
    """
    import os

    errors: list[str] = []

    # ── LLM provider ──────────────────────────────────────────────────────────
    provider = (provider_override or cfg.get("llm", {}).get("provider", "openai")).lower()
    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            errors.append(
                "OPENAI_API_KEY is not set (llm.provider=openai). "
                "Add it to your .env file or export it in your shell."
            )
    elif provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            errors.append(
                "ANTHROPIC_API_KEY is not set (llm.provider=anthropic). "
                "Add it to your .env file or export it in your shell."
            )
    else:
        errors.append(
            f"llm.provider '{provider}' is not recognised. "
            "Valid values: openai, anthropic"
        )

    # ── Notion ────────────────────────────────────────────────────────────────
    if not skip_notion:
        if not os.environ.get("NOTION_TOKEN"):
            errors.append(
                "NOTION_TOKEN is not set. "
                "Add it to your .env file or export it in your shell."
            )
        database_id = cfg.get("notion", {}).get("database_id", "")
        if not str(database_id).strip():
            errors.append(
                "notion.database_id is empty in config.yaml. "
                "Copy your Notion database ID from its URL and paste it there."
            )

    # ── Paths ─────────────────────────────────────────────────────────────────
    paths_cfg = cfg.get("paths", {})
    for key in ("inbox", "transcripts_raw", "transcripts_clean", "notes"):
        val = paths_cfg.get(key)
        if not val or not isinstance(val, str):
            errors.append(
                f"paths.{key} is missing or not a string in config.yaml."
            )

    # ── Report ────────────────────────────────────────────────────────────────
    if errors:
        print("\n[CONFIG ERROR] Pipeline cannot start. Fix the following:\n", file=sys.stderr)
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}", file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)
