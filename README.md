# Local ASP — Meeting Notes Taker

Low-cost, fully local meeting processing pipeline for Chinese/English mixed meetings.

```
Audio file  →  faster-whisper  →  clean transcript  →  LLM (gpt-5-mini)  →  Notion page
```

---

## Requirements

- Python 3.10+
- CUDA GPU recommended (works on CPU too, just slower)
- An [OpenAI API key](https://platform.openai.com/api-keys) — default provider
- An [Anthropic API key](https://console.anthropic.com/) — only for `--provider anthropic`
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
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # optional — only for --provider anthropic
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
| `--provider`   | openai   | LLM provider: `openai` or `anthropic`           |
| `--lang`       | config   | Output language: `en`, `zh`, or `bilingual` (`zh` = Simplified Chinese) |
| `--config`     | config/config.yaml | Path to config file                  |

### Example — offline meeting, specific project

```bash
python src/pipeline.py \
  --file meetings/inbox/2026-04-07_design-review.m4a \
  --type Offline \
  --project "Product Redesign Q2"
```

### Example — output notes in Simplified Chinese

```bash
python src/pipeline.py \
  --file meetings/inbox/2026-04-07_project-sync.m4a \
  --lang zh
```

The transcript can be in English, Chinese, or mixed — `--lang zh` makes the LLM
produce the notes (and Notion section headers) entirely in Simplified Chinese.

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
  provider: openai             # openai | anthropic; override per-run with --provider
  model: gpt-5-mini            # openai: gpt-5-mini / gpt-5 / gpt-4o | anthropic: claude-sonnet-4-6
  max_tokens: 16000            # floor for the output budget (see "Note detail" below)
  max_tokens_cap: 64000        # ceiling for very long meetings
  temperature: 0.3             # ignored by gpt-5 / o-series (they only accept the default)
  verbosity: high              # gpt-5 only — the main lever on how much detail you get
  output_language: bilingual   # "en" | "zh" (Simplified Chinese) | "bilingual"; override per-run with --lang

notion:
  database_id: ""         # required — paste your Notion DB ID here
```

### Note detail

Notes are written to be **exhaustive** — the goal is that you never need to
re-read the transcript. Every Notion page includes a **Detailed Discussion**
section: a chronological, topic-by-topic walkthrough of the meeting, so the
later parts of a long recording get the same depth as the opening.

Three things control this, and they scale automatically with meeting length:

| Setting | Effect |
|---|---|
| `max_tokens` / `max_tokens_cap` | Output budget grows with transcript size (floor → cap). It's only a ceiling — you're billed for what's generated, so a high cap costs nothing on short meetings. |
| `verbosity: high` | gpt-5's main length lever. Drop to `medium` if notes are more verbose than you want. |
| `reasoning_effort: high` | Optional, commented out by default. Slower and pricier, but improves structure on complex discussions. |

If a meeting still feels under-covered, raise `max_tokens_cap` first.

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

## Running with Docker

> New to Docker? See the [Docker concepts explainer](#docker-concepts-for-beginners) section below first.

### Prerequisites

| Requirement | Purpose |
|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Runs the container (includes `docker compose`) |
| NVIDIA GPU *(optional)* | Faster transcription — see GPU setup below |

### Quick start

```bash
# 1. Copy and fill in your API keys
cp .env.example .env

# 2. Fill in your Notion database ID in config/config.yaml
#    notion:
#      database_id: "your-id-here"

# 3. Build the image (first time only — takes a few minutes)
docker compose build

# 4. Drop audio files into meetings/inbox/, then run the pipeline
docker compose run --rm pipeline

# 5. Results appear on your host machine under meetings/
```

### Common commands

```bash
# Process a single file
docker compose run --rm pipeline --file meetings/inbox/2026-04-07_sync.m4a

# Specify meeting type and project
docker compose run --rm pipeline \
  --file meetings/inbox/2026-04-07_design-review.m4a \
  --type Offline \
  --project "Q2 Roadmap"

# Dry run — skip Notion upload (good for testing)
docker compose run --rm pipeline --no-notion

# Force re-run all steps (even if outputs exist)
docker compose run --rm pipeline --force

# Open a shell inside the container for debugging
docker compose run --rm --entrypoint bash pipeline
```

> `--rm` automatically removes the stopped container after it finishes.
> Your files in `meetings/` are never deleted — they live on your host machine.

### GPU setup (Windows, optional but recommended)

Without GPU the pipeline still works, just slower. To enable GPU:

1. Make sure WSL2 is enabled in Windows Features
2. Install an NVIDIA driver ≥ 520 on Windows (not inside WSL)
3. Inside WSL2, install the NVIDIA Container Toolkit:
   ```bash
   # Run this inside your WSL2 terminal
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
     | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
     | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```
4. In Docker Desktop → Settings → General → enable **"Use the WSL 2 based engine"**

To verify GPU is visible inside the container:
```bash
docker compose run --rm --entrypoint nvidia-smi pipeline
```

**No GPU?** Comment out the `deploy:` block in `docker-compose.yml` and the pipeline runs on CPU.

### Rebuild after code changes

```bash
# Rebuild only (no run)
docker compose build

# Rebuild and run in one step
docker compose run --rm pipeline
```

You only need to rebuild when you change:
- `Dockerfile`
- `requirements.txt`
- files in `src/` or `config/`

You do **not** need to rebuild to:
- Change API keys (`.env`)
- Drop new audio files into `meetings/inbox/`

---

## Docker concepts for beginners

### Image vs Container

| Term | Analogy | What it is |
|------|---------|------------|
| **Image** | A recipe / blueprint | A read-only snapshot: Python 3.11 + ffmpeg + all packages + your code, frozen together |
| **Container** | A dish cooked from the recipe | A live, running process started from an image. Isolated from your OS |

You build an image once (`docker compose build`). Every time you run the pipeline you create a fresh container from it, and `--rm` discards it when done.

### Why Docker for this project?

| Problem | Without Docker | With Docker |
|---------|---------------|-------------|
| Python version conflicts | "Works on my machine" | Same Python everywhere |
| ffmpeg installation | Manual, OS-specific | Baked into the image |
| CUDA library versions | Very painful to match | Pre-matched in base image |
| Sharing with teammates | Send a README and hope | `docker compose build` just works |

### How volumes work here

```
Your laptop                        Container
──────────────────────────────────────────────────────
meetings/inbox/sync.m4a   ←──┐
meetings/transcripts/     ←──┼── ./meetings:/app/meetings
meetings/notes/           ←──┘   (live two-way bridge)

~/.docker/volumes/        ←────── whisper_cache volume
  whisper_cache/                  (model downloaded once,
  └── huggingface/                 persists forever)
```

Files you put in `meetings/inbox/` on your laptop are immediately readable inside the container. Transcripts and notes written by the container appear immediately on your laptop. No copying needed.

### The `--rm` flag explained

```bash
docker compose run --rm pipeline
#                  ^^^
#                  Remove the container when it exits.
```

Without `--rm`, finished containers pile up and waste disk space. Since our pipeline is a one-shot job (not a long-running server), always use `--rm`.

---

## Roadmap (post-MVP)

- [ ] Auto-watch inbox folder (`watchdog`)
- [ ] Speaker diarisation (`pyannote.audio`)
- [ ] Bilingual summary output (ZH + EN side-by-side)
- [ ] Auto-project classification from content
- [ ] Calendar event linking (Google Calendar API)
- [ ] Follow-up email / Slack draft generation
