# Contributing

Thanks for helping improve Local ASP. Bug reports, eval cases, and pull
requests are all welcome — especially from people who sit in code-switched
meetings and can describe how the notes go wrong.

## Ground rule: no real meeting data

Never commit real recordings, transcripts, notes, colleagues' names, or
internal jargon — not in code, tests, eval cases, issues, or commit messages.
Use fictional examples. CI blocks the obvious cases (`config/context.md`,
`config/config.local.yaml`, `.env`, a committed Notion database ID), but the
rest is on all of us.

If you're reporting a bug that only shows up on a real meeting, describe the
pattern ("a name said in Chinese came out in pinyin") or write a small
synthetic transcript that reproduces it.

## Setup

```bash
git clone https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker.git
cd Local-ASP_Meeting_Notes_Taker
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                 # tests + evals
pip install -r requirements.txt                     # full pipeline (faster-whisper)
```

## Before opening a PR

```bash
python -m pytest
```

Tests must not need a GPU, a speech model, network access, or API keys —
stub the boundary instead (see `tests/test_pipeline.py` and the fake Notion
client in `tests/test_notion_writer.py`).

If you change prompts or anything in `src/generate_notes.py`, also run the
eval and paste the summary table into your PR:

```bash
python evals/run_eval.py --model gpt-5-mini
```

## Adding an eval case

The most useful contribution for this project is a new failure mode captured
as a synthetic case. See [evals/README.md](evals/README.md#adding-a-case).

## Style

- Python 3.10+, type hints, `from __future__ import annotations`.
- Keep modules runnable on their own (`python src/<step>.py ...`).
- Explain *why* in comments when behaviour exists to work around a model or
  API quirk — most of this codebase is exactly that.
- Small, focused PRs with a clear description of the before/after.
