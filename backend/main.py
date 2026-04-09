"""
Transcript Agent (Managed) — FastAPI application.
Uses Claude Managed Agents (Beta) to analyse meeting transcripts.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from backend.agent_manager import AgentManager
from backend.config import settings
from backend.schemas import AnalysisResponse, ProcessTextRequest
from backend.transcript_parser import detect_and_parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------
manager: AgentManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate config and create AgentManager."""
    global manager
    settings.validate_api_key()
    manager = AgentManager(settings)
    logger.info("AgentManager ready (model=%s)", settings.model)

    # Warm up — create agent eagerly so first request is faster
    try:
        agent_id = await manager.ensure_agent()
        logger.info("Agent warmed up: %s", agent_id)
    except anthropic.APIError as exc:
        logger.warning("Agent warm-up failed (will retry on first request): %s", exc)

    yield


app = FastAPI(
    title="Transcript Agent (Managed)",
    description="Meeting transcript analysis powered by Claude Managed Agents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Local-only app — acceptable
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_manager() -> AgentManager:
    if manager is None:
        raise HTTPException(503, "Server is starting up")
    return manager


def _save_output(response: AnalysisResponse) -> str:
    """Save analysis to outputs/ and return the filename."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"analysis_{ts}.json"
    path = OUTPUTS_DIR / filename
    path.write_text(json.dumps(response.model_dump(), indent=2))
    return filename


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    mgr = _get_manager()
    return {
        "status": "ok",
        "agent_id": mgr.agent_id,
        "model": settings.model,
    }


@app.get("/api/sample")
async def get_sample():
    """Return the bundled sample transcript."""
    sample = PROJECT_ROOT / "sample_data" / "aurora-sprint-retro.txt"
    if sample.exists():
        return {"transcript": sample.read_text()}
    return {"transcript": ""}


@app.post("/api/process-text")
async def process_text(body: ProcessTextRequest):
    """Process raw transcript text (non-streaming)."""
    mgr = _get_manager()
    try:
        response = await mgr.run_analysis(body.transcript)
    except anthropic.APIError as exc:
        logger.error("Managed Agents API error: %s", exc)
        raise HTTPException(502, "Managed Agents API error — check logs")

    filename = _save_output(response)
    response.output_file = filename
    return JSONResponse(content=response.model_dump())


@app.post("/api/process")
async def process_file(file: UploadFile = File(...)):
    """Upload a transcript file and get structured analysis."""
    if not file.filename:
        raise HTTPException(400, "No file provided")

    raw = await file.read()
    text = raw.decode("utf-8", errors="replace")
    transcript = detect_and_parse(file.filename, text)

    if len(transcript.strip()) < 50:
        raise HTTPException(400, "Transcript too short to analyse")

    mgr = _get_manager()
    try:
        response = await mgr.run_analysis(transcript)
    except anthropic.APIError as exc:
        logger.error("Managed Agents API error: %s", exc)
        raise HTTPException(502, "Managed Agents API error — check logs")

    filename = _save_output(response)
    response.output_file = filename
    return JSONResponse(content=response.model_dump())


@app.post("/api/analyze")
async def analyze_stream(body: ProcessTextRequest):
    """SSE streaming endpoint — real-time events as the agent works."""
    mgr = _get_manager()

    async def event_generator():
        try:
            async for event in mgr.stream_analysis(body.transcript):
                yield {"data": json.dumps(event)}
        except anthropic.APIError as exc:
            yield {"data": json.dumps({"type": "error", "data": str(exc)})}

    return EventSourceResponse(event_generator())


@app.post("/api/kill")
async def kill_session():
    """Kill the active session — stops the container and billing."""
    mgr = _get_manager()
    killed = mgr.kill_active_session()
    return {"killed": killed}


# ---------------------------------------------------------------------------
# Serve frontend as static files (must be last — catches all unmatched routes)
# ---------------------------------------------------------------------------
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
