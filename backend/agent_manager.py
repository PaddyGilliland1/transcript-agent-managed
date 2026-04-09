"""
Managed Agent lifecycle — agent creation, session management, analysis.
Caches agent ID to disk so it survives server restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import anthropic

from backend.config import Settings
from backend.prompts import SYSTEM_PROMPT
from backend.schemas import (
    AnalysisMeta,
    AnalysisResponse,
    TranscriptAnalysis,
)

logger = logging.getLogger(__name__)

CACHE_FILE = Path(".agent_cache.json")


class AgentManager:
    """Manages the Managed Agent lifecycle: create, cache, session, analysis."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._agent_id: str | None = None
        self._lock = asyncio.Lock()
        self._load_cache()

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        """Load cached agent ID from disk."""
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                self._agent_id = data.get("agent_id")
                logger.info("Loaded cached agent ID: %s", self._agent_id)
            except (json.JSONDecodeError, OSError):
                self._agent_id = None

    def _save_cache(self) -> None:
        """Persist agent ID to disk."""
        CACHE_FILE.write_text(json.dumps({"agent_id": self._agent_id}, indent=2))

    def _validate_cached_agent(self) -> bool:
        """Check if the cached agent ID is still valid."""
        if not self._agent_id:
            return False
        try:
            self.client.beta.agents.retrieve(agent_id=self._agent_id)
            return True
        except anthropic.NotFoundError:
            logger.warning("Cached agent %s no longer exists", self._agent_id)
            self._agent_id = None
            return False
        except anthropic.APIError as exc:
            logger.warning("Could not validate agent: %s", exc)
            return False

    async def ensure_agent(self) -> str:
        """Return the agent ID, creating the agent if needed."""
        async with self._lock:
            if self._agent_id and self._validate_cached_agent():
                return self._agent_id

            logger.info("Creating new managed agent (model=%s)...", self.settings.model)
            agent = self.client.beta.agents.create(
                model=self.settings.model,
                system=SYSTEM_PROMPT,
                tools=[
                    {"type": "bash_20250124"},
                    {"type": "text_editor_20250429"},
                ],
            )
            self._agent_id = agent.id
            self._save_cache()
            logger.info("Created managed agent: %s", self._agent_id)
            return self._agent_id

    @property
    def agent_id(self) -> str | None:
        return self._agent_id

    # ------------------------------------------------------------------
    # Analysis (blocking)
    # ------------------------------------------------------------------

    async def run_analysis(self, transcript: str) -> AnalysisResponse:
        """Send transcript to managed agent and return structured analysis."""
        agent_id = await self.ensure_agent()
        start = time.time()

        # Create session
        session = self.client.beta.sessions.create(agent_id=agent_id)
        session_id = session.id
        logger.info("Session created: %s", session_id)

        # Stream response
        full_response = self._stream_response(session_id, transcript)

        # Fallback if streaming yielded nothing
        if not full_response:
            full_response = self._fallback_response(session_id)

        # Parse and validate
        analysis = self._parse_response(full_response)
        duration = time.time() - start

        meta = AnalysisMeta(
            agent_id=agent_id,
            session_id=session_id,
            model=self.settings.model,
            duration_seconds=round(duration, 2),
        )

        return AnalysisResponse(analysis=analysis, meta=meta)

    # ------------------------------------------------------------------
    # Analysis (streaming SSE)
    # ------------------------------------------------------------------

    async def stream_analysis(self, transcript: str) -> AsyncGenerator[dict[str, Any], None]:
        """Yield SSE-compatible events as the agent processes the transcript."""
        agent_id = await self.ensure_agent()
        start = time.time()

        yield {"type": "status", "data": "Creating session..."}

        session = self.client.beta.sessions.create(agent_id=agent_id)
        session_id = session.id
        yield {"type": "status", "data": f"Session {session_id[:20]}... active"}

        full_response = ""
        try:
            with self.client.beta.sessions.stream(
                session_id=session_id,
                event={
                    "type": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyse this meeting transcript and extract all "
                                "actions, decisions, risks, and speaker stats:\n\n"
                                + transcript
                            ),
                        }
                    ],
                },
            ) as stream:
                for event in stream:
                    if not hasattr(event, "type"):
                        continue
                    if event.type == "content_block_delta" and hasattr(event, "delta"):
                        if hasattr(event.delta, "text"):
                            chunk = event.delta.text
                            full_response += chunk
                            yield {"type": "text", "data": chunk}

        except anthropic.APIError as exc:
            yield {"type": "error", "data": f"API error: {exc.message}"}
            return

        # Fallback
        if not full_response:
            full_response = self._fallback_response(session_id)
            if full_response:
                yield {"type": "text", "data": full_response}

        # Parse and return complete result
        analysis = self._parse_response(full_response)
        duration = time.time() - start
        meta = AnalysisMeta(
            agent_id=agent_id,
            session_id=session_id,
            model=self.settings.model,
            duration_seconds=round(duration, 2),
        )
        response = AnalysisResponse(analysis=analysis, meta=meta)
        yield {"type": "complete", "data": response.model_dump()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stream_response(self, session_id: str, transcript: str) -> str:
        """Stream agent response and collect full text."""
        full_response = ""
        with self.client.beta.sessions.stream(
            session_id=session_id,
            event={
                "type": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Analyse this meeting transcript and extract all "
                            "actions, decisions, risks, and speaker stats:\n\n"
                            + transcript
                        ),
                    }
                ],
            },
        ) as stream:
            for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta" and hasattr(event, "delta"):
                        if hasattr(event.delta, "text"):
                            full_response += event.delta.text
        return full_response

    def _fallback_response(self, session_id: str) -> str:
        """Retrieve the last assistant message if streaming yielded nothing."""
        text = ""
        try:
            messages = self.client.beta.sessions.list_events(session_id=session_id)
            for msg in reversed(list(messages)):
                if hasattr(msg, "role") and msg.role == "assistant":
                    for block in msg.content:
                        if hasattr(block, "text"):
                            text += block.text
                    break
        except anthropic.APIError as exc:
            logger.warning("Fallback retrieval failed: %s", exc)
        return text

    def _parse_response(self, text: str) -> TranscriptAnalysis:
        """Multi-strategy JSON extraction and Pydantic validation."""
        if not text.strip():
            logger.warning("Empty response from agent")
            return TranscriptAnalysis()

        # Strategy 1: direct parse
        data = self._try_json_parse(text.strip())

        # Strategy 2: extract from markdown fences
        if data is None:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if match:
                data = self._try_json_parse(match.group(1).strip())

        # Strategy 3: first { to last }
        if data is None:
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last > first:
                data = self._try_json_parse(text[first : last + 1])

        if data is None:
            logger.error("Could not parse agent response as JSON")
            return TranscriptAnalysis()

        # Normalise: support both flat (skeleton) and nested (new) formats
        normalised = self._normalise_output(data)

        try:
            return TranscriptAnalysis.model_validate(normalised)
        except Exception as exc:
            logger.warning("Pydantic validation failed: %s", exc)
            # Best-effort: return what we can
            return TranscriptAnalysis.model_construct(**normalised)

    @staticmethod
    def _try_json_parse(text: str) -> dict | None:
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _normalise_output(data: dict) -> dict:
        """
        Accept both the old flat format (skeleton) and the new nested format.
        Converts flat format to nested.
        """
        # Already in nested format
        if "meeting" in data:
            return data

        # Flat format from skeleton — convert
        meeting = {
            "title": data.get("meeting_title", ""),
            "date": data.get("meeting_date"),
            "attendees": data.get("attendees", []),
            "summary": data.get("summary", ""),
        }

        # Decisions: may be strings or objects
        raw_decisions = data.get("decisions", [])
        decisions = []
        for d in raw_decisions:
            if isinstance(d, str):
                decisions.append({"summary": d, "context": "", "decided_by": [], "confidence": 0.8})
            elif isinstance(d, dict):
                decisions.append(d)

        # Risks: may be strings or objects
        raw_risks = data.get("open_risks", data.get("risks", []))
        risks = []
        for r in raw_risks:
            if isinstance(r, str):
                risks.append({"description": r, "severity": "medium"})
            elif isinstance(r, dict):
                risks.append(r)

        return {
            "meeting": meeting,
            "actions": data.get("actions", []),
            "decisions": decisions,
            "risks": risks,
            "speakers": data.get("speakers", []),
        }
