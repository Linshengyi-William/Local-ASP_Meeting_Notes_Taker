# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.3.0] — 2026-10-21

Open-source readiness: licensing, privacy-safe configuration, tests, and evals.

### Added
- MIT `LICENSE`.
- Private configuration layer: `config/config.local.yaml` (deep-merged over
  `config/config.yaml`) and a `NOTION_DATABASE_ID` environment variable, so
  nothing org-specific has to live in committed files.
- `config/context.example.md` and `config/config.local.example.yaml` templates
  with fictional example data.
- Unit test suite (`tests/`, 80+ tests) covering transcript cleaning,
  deterministic corrections, output budgeting, Notion block building and
  batching, manifest/skip logic, and config loading. No models or network
  needed.
- GitHub Actions CI on Python 3.10–3.13, plus a privacy guard that fails the
  build if `config/context.md`, `config/config.local.yaml` or `.env` is
  committed, or if a Notion database ID is committed in `config.yaml`.
- LLM regression eval (`evals/`): three synthetic code-switched meetings scored
  for name accuracy, misheard-name leaks, action-item recall, invented owners
  and Simplified-Chinese compliance; replay mode for API-free re-scoring; a
  secret-gated GitHub workflow that posts results to the job summary.
- `CONTRIBUTING.md`, issue templates, and this changelog.
- Manual `Release` workflow that tags a commit and publishes the matching
  CHANGELOG section as the GitHub Release notes.

### Changed
- `config/context.md` is no longer tracked (gitignored); the committed
  `config.yaml` ships a generic Whisper vocabulary prompt and an empty
  `notion.database_id`.
- Private config files are excluded from the Docker image and supplied only
  through the `./config` volume mount.
- A missing `config/context.md` now logs a hint pointing to the template.

### Upgrading from 1.2
Repository history was rewritten on 2026-10-21 to remove org-specific data
from old commits, so existing clones can't simply `git pull`. Back up your
private files, then re-clone:

```bash
cp config/context.md ../context.md.bak     # your glossary
git clone https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker.git
cp ../context.md.bak Local-ASP_Meeting_Notes_Taker/config/context.md
```

Don't push from a pre-rewrite clone — it would reintroduce the old history.

Move your Notion database ID to `.env` (`NOTION_DATABASE_ID=...`) or
`config/config.local.yaml`, and your real `transcription.initial_prompt`
to `config/config.local.yaml`.

## [1.2.0] — 2026-08-11

### Added
- Standing project context (`config/context.md`): a user-maintained glossary
  of people, team structure, systems and known transcription errors, injected
  into every run and ranked above the transcript.
- Deterministic "Automatic corrections" table, applied literally to the
  finished notes (including inside quotes) in a single longest-match-first
  pass; ASCII terms match on word boundaries.
- `transcription.initial_prompt` plumbed through to faster-whisper to bias
  decoding toward team vocabulary.
- "Detailed Discussion" section: a chronological topic-by-topic walkthrough.
- Prompt rule: never invent an action-item owner.

### Changed
- Default model is now `gpt-5-mini`; OpenAI parameters are routed by model
  family (`max_completion_tokens`, `verbosity`, `reasoning_effort`).
- Output budget scales with transcript length (16K floor → 64K cap), clamped
  to each model's hard output limit.
- `zh` and `bilingual` output now enforce Simplified Chinese.
- Notion pages over 100 blocks are created in batches; long text is split
  across rich-text items instead of being truncated.

### Fixed
- `--lang` was silently ignored when cached notes existed; it now warns.
- An explicit empty `output_language:` no longer crashes.
- A failed Notion append no longer causes a duplicate page on re-run.

## [1.1.0] — 2026-04-09

### Added
- Processing manifest (`meetings/processed.json`) for idempotent re-runs and
  partial recovery; `--status` to print history.
- `--watch` mode to process new inbox files automatically.
- Exponential-backoff retries for OpenAI, Anthropic and Notion.
- Startup config validation that reports every problem at once.
- Batch summary table; TL;DR callout and length-adaptive summaries.

### Fixed
- Re-runs no longer create duplicate Notion pages.
- One failing file no longer aborts the whole batch.

## [1.0.0] — 2026-04-08

### Added
- MVP pipeline: faster-whisper transcription → transcript cleaning → LLM
  notes → Notion page.
- OpenAI as default provider with `--provider anthropic` option.
- Docker and docker-compose support with optional NVIDIA GPU.

[1.3.0]: https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Linshengyi-William/Local-ASP_Meeting_Notes_Taker/releases/tag/v1.0.0
