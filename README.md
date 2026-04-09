# Managed Agents Framework

**A reusable framework for building with [Claude Managed Agents](https://www.anthropic.com/news/managed-agents) (Public Beta, April 2026)**

## What This Is

This is a **framework and reference implementation**, not a finished product. It provides the complete plumbing for Anthropic's Managed Agents API — agent lifecycle, environment management, session handling, SSE streaming, cost tracking, and a web UI — so you can swap in your own use case and go.

The included transcript analyser is a simple demonstration. Managed agents are designed for **big, long-running tasks** where the agent needs tools and autonomy — not quick prompt-in/JSON-out jobs. The framework is the point, not the demo.

## When to Use Managed Agents

Managed agents spin up an isolated cloud container per session. That container has tools (bash, file system, web search, code editor). There's a ~30-60s cold start. This makes sense when:

- **The task is large** — processing hours of meeting transcripts, not minutes
- **The agent needs tools** — running code, reading/writing files, searching the web
- **Autonomy matters** — fire-and-forget jobs that run for minutes or hours
- **Isolation is required** — each session gets its own sandboxed workspace

Examples of good fit:
- "Here's a 200-page contract PDF — extract all obligations, deadlines, and liabilities"
- "Clone this GitHub repo, run the tests, fix failures, push a PR"
- "Research 50 companies from this list — scrape pricing pages, build a comparison"
- "Process a 4-hour all-hands transcript — produce RACI matrix, action tracker, risk register"

For simple single-turn tasks (analyse short text, answer a question), use the regular Messages API instead — it's 10x faster and cheaper.

## What You Get

Fork this repo and you have:

- **Agent lifecycle management** — create once, cache to disk, validate on restart
- **Environment management** — container configuration via the Environments API
- **Session handling** — create per-request, stream events, clean up
- **SSE streaming** — real-time status updates to the browser (async queue bridge for the sync SDK)
- **Cost tracking** — token counts + session time + USD estimate per request
- **Kill button** — abort request and delete session to stop billing immediately
- **Pydantic v2 schemas** — structured, validated output
- **Multi-strategy JSON parsing** — handles markdown fences, partial output, format variations
- **File format support** — VTT, SRT, TXT transcript parsing (extend for your formats)
- **Web UI** — dark-theme SPA with streaming, file upload, download JSON

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
                                                    ├─ environments.create()
                                                    ├─ sessions.create()
                                                    ├─ sessions.events.send()
                                                    ├─ sessions.events.stream()
                                                    └─ sessions.delete()
                                                                    │
                                                           ┌────────▼─────────┐
                                                           │ Claude Managed    │
                                                           │ Agents (Anthropic │
                                                           │ cloud containers) │
                                                           └──────────────────┘
```

**Key concepts:**
- **Agent** — persistent, versioned config (model + system prompt + tools). Created once, reused.
- **Environment** — container configuration (packages, networking). Created once, reused.
- **Session** — a running agent instance with its own sandboxed container. One per request.
- **Events** — SSE stream of status updates, thinking, and agent responses.

## Quick Start

```bash
git clone https://github.com/PaddyGilliland1/transcript-agent-managed.git
cd transcript-agent-managed

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — paste your Anthropic API key

uvicorn backend.main:app --reload --port 8888
```

Open **http://localhost:8888**. Load the sample transcript and click Process.

## Adapting for Your Use Case

1. **Change the prompt** — edit `backend/prompts.py` with your task instructions and output schema
2. **Change the schemas** — edit `backend/schemas.py` to match your output structure
3. **Change the UI** — edit `frontend/index.html` to render your output format
4. **Everything else stays the same** — agent lifecycle, streaming, cost tracking, kill button all work unchanged

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/analyze` | SSE streaming analysis |
| `POST` | `/api/process-text` | Non-streaming text analysis |
| `POST` | `/api/process` | File upload analysis |
| `POST` | `/api/kill` | Kill active session (stops billing) |
| `GET` | `/api/health` | Health check + agent status |
| `GET` | `/api/sample` | Sample input data |

## Project Structure

```
transcript-agent-managed/
├── .env.example              # API key placeholder (copy to .env)
├── backend/
│   ├── main.py               # FastAPI app + endpoints
│   ├── agent_manager.py      # Agent/environment/session lifecycle (the core)
│   ├── config.py             # Settings from .env
│   ├── schemas.py            # Pydantic v2 output models
│   ├── prompts.py            # System prompt (swap this for your use case)
│   └── transcript_parser.py  # Input format parsing
├── frontend/
│   └── index.html            # Web UI with SSE streaming
├── sample_data/              # Sample input files
└── outputs/                  # Saved results (gitignored)
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `MODEL` | `claude-sonnet-4-6` | Claude model |
| `PORT` | `8000` | Server port |

Agent and environment IDs are cached to `.agent_cache.json` (gitignored) so they survive restarts.

## Cost

- **Session runtime**: $0.08 per agent-hour (container time)
- **Token costs**: Standard Claude pricing
- **Kill button**: Deletes the session immediately — stops billing
- Cost per request is shown in the UI footer and API response

## Notes

- **Managed Agents is in public beta** (launched 8 April 2026). The API may evolve.
- Beta header `managed-agents-2026-04-01` is set automatically by the SDK (v0.92.0+).
- Each request creates a new session (isolated container). Expect 30-60s cold start.
- Your API key never leaves your machine.

## Licence

MIT

## Author

**Paddy Gilliland** — Built on launch day, April 2026.
