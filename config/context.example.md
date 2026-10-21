# Project Context (example)

> **This is a template.** Copy it to `config/context.md` and replace the
> example content with your own organisation's facts:
>
> ```bash
> cp config/context.example.md config/context.md
> ```
>
> `config/context.md` is gitignored, so your colleagues' names and internal
> jargon never end up in version control. Everything below is fictional.

Standing knowledge about *your* organisation that the transcript alone can never
supply. Everything here is injected into the note-generation prompt on every
run, so a correction you make once is applied to every future meeting.

Keep it factual and terse. Add to it whenever you spot the notes getting
something wrong — that is exactly what this file is for.

---

## People

Correct spellings. Speech-to-text routinely mangles names, and Chinese-language
meetings often render them phonetically (e.g. 马可), so list every variant you
have actually seen in a transcript.

| Correct name | Misheard as |
|---|---|
| Yuki | Yucky, 尤琪 |
| Marco | Marko, 马可 |
| Shen Hao | Shen How, 沈好 |
| Priya | Pria, 普丽娅 |

## Teams and structure

- **There is exactly one engineering team: the Platform team.** It does
  everything — development, testing, deployment, and support.
- There is **no** separate QA team, testing team, or ops team. Never attribute
  an action item to one; they do not exist. If work is owned by the team rather
  than a named person, the owner is "Platform team".

## Systems and terminology

| Term | Meaning |
|---|---|
| OMS | Order Management System |
| Staging | The pre-production environment (not a team) |
| Hotfix | An out-of-cycle production patch |
| On-call | The weekly support rotation |

## Known transcription errors

Speech-to-text mistakes specific to this team's vocabulary and accents. Read the
right-hand column as what was actually meant.

| Appears as | Actually means |
|---|---|
| ohms, OMG system | OMS |
| stage in | staging |
| hot fix, 好fix | hotfix |
| 吉拉 | Jira |

---

## Automatic corrections

Unlike everything above — which is guidance the model may apply imperfectly —
this table is applied **literally and deterministically** to the finished notes,
including inside direct quotes. Use it for corrections that must never slip
through.

Matching is case-sensitive. ASCII terms match whole words only, so `ohms` will
not corrupt `ohmsx`; CJK terms match anywhere.

| Replace | With |
|---|---|
| Yucky | Yuki |
| Marko | Marco |
| 马可 | Marco |
| Shen How | Shen Hao |
| ohms | OMS |
| 吉拉 | Jira |
| QA team | Platform team |
| testing team | Platform team |

---

## How to extend this file

- Add a row whenever the notes misname a person, team, or system.
- Short entries beat long explanations — this is a lookup table, not prose.
- To bias the *transcriber* itself toward the right vocabulary, also add the
  term to `transcription.initial_prompt` (put your real list in
  `config/config.local.yaml`). That runs before the LLM sees anything and fixes
  errors at the source.
