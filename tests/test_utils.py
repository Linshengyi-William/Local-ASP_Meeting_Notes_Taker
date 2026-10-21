from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import utils

ROOT = Path(__file__).resolve().parent.parent


def test_deep_merge_recurses_and_does_not_mutate_inputs():
    base = {"a": {"b": 1, "c": 2}, "d": [1]}
    override = {"a": {"c": 3}, "d": [2], "e": 4}
    merged = utils.deep_merge(base, override)
    assert merged == {"a": {"b": 1, "c": 3}, "d": [2], "e": 4}
    assert base == {"a": {"b": 1, "c": 2}, "d": [1]}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_config_layers_local_file_then_env(tmp_path, monkeypatch):
    main = _write_yaml(tmp_path / "config.yaml", {
        "notion": {"database_id": "", "max_retries": 3},
        "transcription": {"initial_prompt": "generic", "beam_size": 5},
    })
    _write_yaml(tmp_path / "config.local.yaml", {
        "notion": {"database_id": "from-local"},
        "transcription": {"initial_prompt": "Real, Names"},
    })

    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    cfg = utils.load_config(main)
    assert cfg["notion"] == {"database_id": "from-local", "max_retries": 3}
    assert cfg["transcription"] == {"initial_prompt": "Real, Names", "beam_size": 5}

    monkeypatch.setenv("NOTION_DATABASE_ID", "from-env")
    assert utils.load_config(main)["notion"]["database_id"] == "from-env"

    assert utils.load_config(main, use_local=False, use_env=False)["notion"]["database_id"] == ""


def test_load_config_without_local_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    main = _write_yaml(tmp_path / "config.yaml", {"llm": {"model": "gpt-5-mini"}})
    assert utils.load_config(main) == {"llm": {"model": "gpt-5-mini"}}


def test_shipped_config_contains_no_private_values(monkeypatch):
    """Guardrail: the committed config must stay generic."""
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    cfg = utils.load_config(ROOT / "config" / "config.yaml", use_local=False)
    assert cfg["notion"]["database_id"] == ""
    assert cfg["llm"]["context_file"] == "config/context.md"


def test_resolve_config_path_prefers_project_root():
    assert utils.resolve_config_path("config/config.yaml") == utils.PROJECT_ROOT / "config" / "config.yaml"
    assert utils.resolve_config_path("/abs/elsewhere.yaml") == Path("/abs/elsewhere.yaml")


def test_validate_config_reports_every_problem_at_once(base_cfg, monkeypatch, capsys):
    for var in ("OPENAI_API_KEY", "NOTION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    base_cfg["notion"]["database_id"] = ""
    with pytest.raises(SystemExit):
        utils.validate_config(base_cfg)
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "NOTION_TOKEN" in err and "NOTION_DATABASE_ID" in err


def test_validate_config_passes_with_no_notion(base_cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    utils.validate_config(base_cfg, skip_notion=True)  # no SystemExit


def test_validate_config_rejects_unknown_provider(base_cfg, capsys):
    with pytest.raises(SystemExit):
        utils.validate_config(base_cfg, skip_notion=True, provider_override="mystery")
    assert "not recognised" in capsys.readouterr().err


class Flaky(Exception):
    pass


def test_retry_with_backoff_retries_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(utils.time, "sleep", sleeps.append)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Flaky()
        return "ok"

    assert utils.retry_with_backoff(fn, max_retries=3, base_delay=2.0, retryable_exceptions=(Flaky,)) == "ok"
    assert sleeps == [2.0, 4.0]


def test_retry_with_backoff_gives_up(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _: None)

    def fn():
        raise Flaky()

    with pytest.raises(Flaky):
        utils.retry_with_backoff(fn, max_retries=2, retryable_exceptions=(Flaky,))


def test_retry_predicate_does_not_retry_non_retryable(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _: pytest.fail("should not sleep"))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("400")

    with pytest.raises(ValueError):
        utils.retry_with_backoff_predicate(fn, is_retryable=lambda e: False)
    assert calls["n"] == 1
