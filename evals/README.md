# Evals

A small regression suite for the part of the pipeline that is hardest to get
right: turning a messy, code-switched Chinese/English transcript into notes
that use the **right names**, assign action items to the **right people**
(and to nobody when nobody volunteered), and stay in **Simplified Chinese**.

Every case is **synthetic and fictional**. Real meeting data never goes in
this directory.

## Running

```bash
pip install -r requirements-dev.txt

# live run against the OpenAI API (3 short calls per model)
python evals/run_eval.py --model gpt-5-mini

# compare models side by side
python evals/run_eval.py --model gpt-5-mini --model gpt-5

# re-score saved outputs without any API calls
python evals/run_eval.py --replay evals/results/<timestamp>/gpt-5-mini
```

Outputs and scores are written to `evals/results/<timestamp>/<model>/`
(gitignored). `--strict` exits non-zero if any case fails; `--markdown FILE`
appends a summary table (CI uses this for the job summary).

The eval always uses the committed `config/config.yaml` defaults plus the
case's own `context.md` — never your private `config.local.yaml` or
`config/context.md`.

## What is scored

| Metric | Fails when |
|---|---|
| Schema | any key the Notion writer expects is missing |
| Name recall | a canonical name from the case glossary never appears |
| Name / term leaks | a known mis-hearing ("Marko", "吉拉", "ohms") survives |
| Action recall | fewer expected action items found (with the correct owner) than `min_action_recall` |
| Invented owners | a task is given to someone not in the meeting, or to a team that doesn't exist (e.g. a "testing team" because the meeting mentioned the testing *environment*) |
| Traditional characters | any Traditional character in `zh` / `bilingual` output (checked with OpenCC when installed, otherwise a verified fallback list) |

Scoring is deterministic and lives in `scoring.py`; it is unit-tested in
`tests/test_eval_scoring.py`.

## Cases

| Case | Output | What it stresses |
|---|---|---|
| `sprint-planning-zh` | `zh` | Chinese-dominant speech with English jargon; Traditional characters from ASR; two misheard names that are **not** in the auto-correction table, so the model must fix them from context; a tempting non-existent "QA team" |
| `vendor-review-en` | `en` | English-dominant with a Chinese aside; exact figures; a misheard vendor; one task nobody claimed |
| `ops-standup-bilingual` | `bilingual` | "testing" used repeatedly as an environment next to two unowned tasks — the classic trigger for invented owners |

## Adding a case

Create `evals/cases/<name>/` with:

- `transcript.txt` — in the cleaned-transcript format (`[Source: …]  [Language: …]` header, blank-line-separated paragraphs). Write it the way Whisper actually fails: misheard names, garbled jargon, mixed scripts.
- `context.md` — the glossary for this fictional org, including an `## Automatic corrections` table.
- `expected.json`:

```json
{
  "lang": "zh",
  "names": {"Marco": ["Marko", "马可"]},
  "forbidden_text": ["吉拉"],
  "forbidden_owners": ["QA"],
  "allowed_owners": ["Platform team"],
  "action_items": [
    {"id": "fix-login", "owner": "Marco", "keywords": ["login", "登录"]},
    {"id": "docs", "owner": null, "keywords": ["文档"]}
  ],
  "min_action_recall": 0.8
}
```

`owner` may be a string, a list of acceptable spellings, or `null` for
"nobody took it on". `tests/test_eval_scoring.py` checks every case is
well-formed — including that each misheard variant really appears in the
transcript.
