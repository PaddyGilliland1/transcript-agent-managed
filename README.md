# Transcript Agent (Managed)

**Meeting transcript analysis powered by [Claude Managed Agents](https://www.anthropic.com/news/managed-agents) (Beta, April 2026)**

Upload a meeting transcript and receive structured JSON output containing actions, decisions, risks, speaker participation, and a concise summary. Built on launch day as an early-adopter demonstration of the Managed Agents API.

## Architecture

```
┌──────────────────┐        POST /api/analyze (SSE)       ┌──────────────────┐
│                  │ ────────────────────────────────────► │                  │
│   Web UI         │ ◄──── streaming events ────────────── │  FastAPI Backend │
│   (index.html)   │                                       │                  │
└──────────────────┘                                       └────────┬─────────┘
                                                                    │
                                                   Anthropic Python SDK (beta)
                                                    ├─ agents.create()
                                                    ├─ sessions.create()
                                                    └─ sessions.stream()
                                                                    │
                                                           ┌────────▼─────────┐
                                                           │ Claude Managed    │
                                                           │ Agents (Anthropic │
                                                           │ cloud containers) │
                                                           └──────────────────┘
```

**Key concepts:**
- **Agent** — persistent, versioned config (model + system prompt + tools). Created once, reused across sessions.
- **Session** — a running agent instance with its own sandboxed container. One per transcript.
- **Events** — SSE stream of user messages and agent responses.
- Beta header `managed-agents-2026-04-01` is set automatically by the SDK.

## Prerequisites

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com) with Managed Agents access (enabled by default in public beta)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/pgilliland1/transcript-agent-managed.git
cd transcript-agent-managed

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
cp .env.example .env
# Edit .env and replace the placeholder with your Anthropic API key

# 5. Run
uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000** in your browser. The frontend is served automatically by FastAPI.

## Using It

1. **Paste** a meeting transcript into the text area, **or** upload a `.txt` / `.vtt` / `.srt` file.
2. Click **Process Transcript**.
3. Watch real-time streaming as the managed agent analyses the transcript.
4. View structured results: actions, decisions, risks, speaker stats.
5. Click **Download JSON** to save the output.

A sample transcript (D365 go-live readiness review) is included — click **Load Sample** to try it.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/analyze` | SSE streaming analysis (recommended) |
| `POST` | `/api/process-text` | Non-streaming text analysis |
| `POST` | `/api/process` | Upload a transcript file |
| `GET` | `/api/health` | Health check + agent status |
| `GET` | `/api/sample` | Returns the bundled sample transcript |

### curl examples

```bash
# Health check
curl http://localhost:8000/api/health

# Process text (non-streaming)
curl -X POST http://localhost:8000/api/process-text \
  -H "Content-Type: application/json" \
  -d '{"transcript": "... your transcript text (50+ chars) ..."}'

# Upload file
curl -X POST http://localhost:8000/api/process \
  -F "file=@sample_data/meridian-golive-review.txt"
```

## Output Format

```json
{
  "status": "ok",
  "analysis": {
    "meeting": {
      "title": "Meridian Manufacturing - D365 Go-Live Readiness Review",
      "date": "2026-04-07",
      "attendees": ["Alex Carter", "Rachel Torres", "Marcus Webb", "Lisa Ng"],
      "summary": "The team reviewed go-live readiness..."
    },
    "actions": [
      {
        "action": "Extract UOM mismatch report from staging database",
        "owner": "Alex Carter",
        "deadline": "2026-04-07",
        "priority": "high",
        "category": "data_request",
        "confidence": 0.95,
        "source_timestamp": "00:04:00"
      }
    ],
    "decisions": [
      {
        "summary": "Thursday set as hard deadline for UOM corrections",
        "context": "Delay past Thursday pushes into weekend with cost implications",
        "decided_by": ["Marcus Webb"],
        "confidence": 0.9
      }
    ],
    "risks": [
      {
        "description": "60% training attendance may cause high post-go-live support volume",
        "severity": "high",
        "mitigation": "Mandatory catch-up session + video walkthroughs",
        "owner": "Alex Carter"
      }
    ],
    "speakers": [
      { "name": "Alex Carter", "word_count": 320, "speaking_time_pct": 38.5, "turn_count": 8 }
    ]
  },
  "meta": {
    "agent_id": "agent_...",
    "session_id": "session_...",
    "processed_at": "2026-04-09T10:30:00+00:00",
    "model": "claude-sonnet-4-6",
    "duration_seconds": 12.4
  }
}
```

## Project Structure

```
transcript-agent-managed/
├── .env.example              # API key placeholder (copy to .env)
├── .gitignore
├── README.md
├── requirements.txt
├── backend/
│   ├── __init__.py
│   ├── main.py               # FastAPI application + endpoints
│   ├── config.py             # Settings from .env
│   ├── schemas.py            # Pydantic v2 models
│   ├── prompts.py            # System prompt for the agent
│   ├── agent_manager.py      # Agent/session lifecycle
│   └── transcript_parser.py  # VTT/SRT/TXT parsing
├── frontend/
│   └── index.html            # Single-page web UI
├── sample_data/
│   └── meridian-golive-review.txt
└── outputs/                  # Generated analysis files (gitignored)
```

## Configuration

All configuration is via `.env` (or environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `PORT` | `8000` | Server port |

The agent ID is cached to `.agent_cache.json` (gitignored) so it survives server restarts.

## Cost

Managed Agents billing:
- **Session runtime**: $0.08 per agent-hour (container time)
- **Token costs**: Standard Claude pricing for input/output tokens
- A typical transcript analysis takes 10-30 seconds, costing < $0.01 in runtime

## Notes

- **Managed Agents is in public beta** (launched 8 April 2026). The API may evolve.
- The beta header `managed-agents-2026-04-01` is set automatically by the SDK.
- Each transcript creates a new session with an isolated container.
- Agent configs are persistent and versioned. The backend creates one on first request and reuses it.
- Your API key stays on your machine — it is never committed or transmitted anywhere except to the Anthropic API.

## Licence

MIT

## Author

**Paddy Gilliland** — Early adopter build using Claude Managed Agents on launch day.
