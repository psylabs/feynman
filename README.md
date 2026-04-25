# Feynman

Personal voice-first cognitive trainer. Mac-local. See [docs/](docs/) for the full PRD, MVP spec, and architecture.

## Setup

Requires Python 3.11+, [uv](https://github.com/astral-sh/uv), and an OpenAI API key (used for STT and short tutor-style feedback):

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
cp .env.example .env  # then put your OPENAI_API_KEY in .env
```

## Run

```bash
python -m server.main
```

Then open http://127.0.0.1:8765 in a browser.

## How a session works

Click **Start session**. Each question:

1. The system speaks a problem.
2. When the audio finishes, the **push-to-talk** button activates.
3. Press and hold (or hold spacebar), say your answer, release.
4. The result is shown briefly, then the next question starts.

After ~12 questions the session ends and a review is shown.

The right pane streams every event happening inside the system in real time — TTS, STT, scheduler decisions, grader verdicts. The same events are written to `logs/YYYY-MM-DD.jsonl` for later inspection.

## Files of interest

- `skills.yaml` — skill definitions (id, tolerance, target latency, templates).
- `schema.sql` — SQLite schema.
- `data/feynman.db` — local database (gitignored).
- `logs/` — daily JSONL event logs (gitignored).
