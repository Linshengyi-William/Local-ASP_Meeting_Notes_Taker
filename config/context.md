# Project Context

Standing knowledge about *your* organisation that the transcript alone can never
supply. Everything here is injected into the note-generation prompt on every
run, so a correction you make once is applied to every future meeting.

Keep it factual and terse. Add to it whenever you spot the notes getting
something wrong — that is exactly what this file is for.

---

## People

Correct spellings. Speech-to-text routinely mangles names, and Chinese-language
meetings often render them phonetically (e.g. 克里斯), so list every variant you
have actually seen in a transcript.

| Correct name | Misheard as |
|---|---|
| Kris | Chris, Cris, 克里斯 |
| Richard | 理查德 |
| Kevin | 凯文 |
| Edward | 爱德华 |
| Alice | 爱丽丝 |

## Teams and structure

- **There is exactly one team: the IT team.** It does everything — development,
  testing, deployment, and support.
- There is **no** separate testing team, QA team, dev team, or ops team. Never
  attribute an action item to one; they do not exist. If work is owned by the
  team rather than a named person, the owner is "IT team".

## Systems and terminology

| Term | Meaning |
|---|---|
| WMS | Warehouse Management System |
| Checking system | The order/inventory checking application |
| Role (system role / project role) | Permission roles; a user can hold both |
| Vendor management | Screen where vendor records and notification emails live |
| Makeup request | Request to correct a missed or wrong time-clock punch |
| Operator lead | Role allowing an operator to supervise others without admin rights |
| PTO | Paid time off |

## Known transcription errors

Speech-to-text mistakes specific to this team's vocabulary and accents. Read the
right-hand column as what was actually meant.

| Appears as | Actually means |
|---|---|
| WNS | WMS |
| vinder | vendor |
| roll, 肉 | role |
| assass | access |
| fucking, 打卡 (in clock context) | punching (clocking in/out) |
| checking department | the checking system's department module |

---

## Automatic corrections

Unlike everything above — which is guidance the model may apply imperfectly —
this table is applied **literally and deterministically** to the finished notes,
including inside direct quotes. Use it for corrections that must never slip
through.

Matching is case-sensitive. ASCII terms match whole words only, so `WNS` will
not corrupt `WNSX`; CJK terms match anywhere.

| Replace | With |
|---|---|
| Chris | Kris |
| Cris | Kris |
| 克里斯 | Kris |
| WNS | WMS |
| vinder | vendor |
| Vinder | Vendor |
| assass | access |
| Testing Team | IT team |
| testing team | IT team |

---

## How to extend this file

- Add a row whenever the notes misname a person, team, or system.
- Short entries beat long explanations — this is a lookup table, not prose.
- To bias the *transcriber* itself toward the right vocabulary, also add the
  term to `transcription.initial_prompt` in `config.yaml`. That runs before the
  LLM sees anything and fixes errors at the source.
