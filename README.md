# Local ASP — Meeting Notes Taker

Low-cost, fully local meeting processing pipeline for Chinese/English mixed meetings.

```
Audio file  →  faster-whisper  →  clean transcript  →  Claude LLM  →  Notion page
```

---

## Requirements

- Python 3.10+
- CUDA GPU recommended (works on CPU too, just slower)
- An [Anthropic API key](https://console.anthropic.com/)
- A [Notion integration token](https://www.notion.so/my-integrations)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> `faster-whisper` requires `ffmpeg` to be available on your PATH.
> Install via: `winget install ffmpeg` (Windows) or `brew install ffmpeg` (Mac).

### 2. Configure API keys

```bash
cp .env.example .env
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
NOTION_TOKEN=secret_...
```

### 3. Set up Notion

1. Create a database in Notion with these properties:

   | Property     | Type          |
   |--------------|---------------|
   | Title        | Title         |
   | Date         | Date          |
   | Project      | Text          |
   | Meeting Type | Select        |
   | Status       | Select        |
   | Tags         | Multi-select  |

2. Share the database with your integration (open database → ··· → Connections → add your integration).

3. Copy the database ID from its URL:
   `https://notion.so/workspace/**<DATABASE_ID>**?v=...`

4. Paste it into `config/config.yaml`:
   ```yaml
   notion:
     database_id: "your-database-id-here"
   ```

---

## Usage

### Process all audio files in the inbox

Drop audio files (`.m4a`, `.mp3`, `.wav`, `.mp4`, etc.) into `meetings/inbox/`.

**Recommended filename format:** `YYYY-MM-DD_topic.m4a`
Example: `2026-04-07_product-sync.m4a`

Then run:

```bash
python src/pipeline.py
```

### Process a single file

```bash
python src/pipeline.py --file meetings/inbox/2026-04-07_project-sync.m4a
```

### Common flags

| Flag           | Default  | Description                                     |
|----------------|----------|-------------------------------------------------|
| `--no-notion`  | off      | Skip Notion upload (useful for testing)         |
| `--type`       | Online   | Meeting type: `Online` or `Offline`             |
| `--project`    | —        | Project name tag written to Notion              |
| `--force`      | off      | Re-run all steps even if outputs already exist  |
| `--config`     | config/config.yaml | Path to config file                  |

### Example — offline meeting, specific project

```bash
python src/pipeline.py \
  --file meetings/inbox/2026-04-07_design-review.m4a \
  --type Offline \
  --project "Product Redesign Q2"
```

### Test without Notion

```bash
python src/pipeline.py --no-notion
```

---

## Pipeline steps

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 1. Transcribe | `src/transcribe.py` | audio file | `meetings/transcripts/raw/<stem>.json` |
| 2. Clean | `src/clean.py` | raw JSON | `meetings/transcripts/clean/<stem>.txt` |
| 3. Generate notes | `src/generate_notes.py` | clean .txt | `meetings/notes/<stem>_notes.json` |
| 4. Write to Notion | `src/notion_writer.py` | notes JSON | Notion page (URL logged) |

Each step can also be run independently:

```bash
python src/transcribe.py meetings/inbox/2026-04-07_sync.m4a
python src/clean.py meetings/transcripts/raw/2026-04-07_sync.json
python src/generate_notes.py meetings/transcripts/clean/2026-04-07_sync.txt
python src/notion_writer.py meetings/notes/2026-04-07_sync_notes.json \
  --transcript meetings/transcripts/clean/2026-04-07_sync.txt \
  --date 2026-04-07 --type Online
```

---

## Configuration

All settings live in `config/config.yaml`. Key options:

```yaml
transcription:
  model: large-v3-turbo   # change to "medium" or "base" to reduce VRAM/time
  device: auto            # auto-detects CUDA; falls back to CPU
  language: null          # null = auto-detect; "zh" or "en" to force

llm:
  model: claude-sonnet-4-6
  output_language: bilingual   # "en" | "zh" | "bilingual"

notion:
  database_id: ""         # required — paste your Notion DB ID here
```

---

## Project structure

```
.
├── meetings/
│   ├── inbox/                 ← drop audio files here
│   ├── transcripts/
│   │   ├── raw/               ← whisper JSON output
│   │   └── clean/             ← cleaned .txt files
│   └── notes/                 ← LLM-generated notes JSON (local backup)
├── src/
│   ├── pipeline.py            ← run this for the full flow
│   ├── transcribe.py
│   ├── clean.py
│   ├── generate_notes.py
│   └── notion_writer.py
├── config/
│   └── config.yaml
├── .env.example
├── requirements.txt
└── README.md
```

---

## Roadmap (post-MVP)

- [ ] Auto-watch inbox folder (`watchdog`)
- [ ] Speaker diarisation (`pyannote.audio`)
- [ ] Bilingual summary output (ZH + EN side-by-side)
- [ ] Auto-project classification from content
- [ ] Calendar event linking (Google Calendar API)
- [ ] Follow-up email / Slack draft generation
