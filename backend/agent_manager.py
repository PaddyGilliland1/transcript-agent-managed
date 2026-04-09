"""
Managed Agent lifecycle — agent creation, environment, session management, analysis.
Caches agent and environment IDs to disk so they survive server restarts.
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
    UsageInfo,
)

logger = logging.getLogger(__name__)

CACHE_FILE = Path(".agent_cache.json")

USER_MESSAGE = (
    "Analyse this meeting transcript and extract all "
    "actions, decisions, risks, and speaker stats:\n\n"
)


class AgentManager:
    """Manages the Managed Agent lifecycle: create, cache, session, analysis."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._agent_id: str | None = None
        self._environment_id: str | None = None
        self._lock = asyncio.Lock()
        self._load_cache()

    # ------------------------------------------------------------------
    # Agent & environment lifecycle
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                self._agent_id = data.get("agent_id")
                self._environment_id = data.get("environment_id")
                logger.info("Loaded cache: agent=%s env=%s", self._agent_id, self._environment_id)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_cache(self) -> None:
        CACHE_FILE.write_text(json.dumps({
            "agent_id": self._agent_id,
            "environment_id": self._environment_id,
        }, indent=2))

    def _validate_cached_agent(self) -> bool:
        if not self._agent_id:
            return False
        try:
            self.client.beta.agents.retrieve(agent_id=self._agent_id)
            return True
        except (anthropic.NotFoundError, anthropic.APIError) as exc:
            logger.warning("Cached agent invalid: %s", exc)
            self._agent_id = None
            return False

    def _validate_cached_environment(self) -> bool:
        if not self._environment_id:
            return False
        try:
            self.client.beta.environments.retrieve(environment_id=self._environment_id)
            return True
        except (anthropic.NotFoundError, anthropic.APIError) as exc:
            logger.warning("Cached environment invalid: %s", exc)
            self._environment_id = None
            return False

    async def ensure_environment(self) -> str:
        """Return the environment ID, creating one if needed."""
        if self._environment_id and self._validate_cached_environment():
            return self._environment_id

        logger.info("Creating new environment...")
        env = self.client.beta.environments.create(name="transcript-analysis")
        self._environment_id = env.id
        self._save_cache()
        logger.info("Created environment: %s", self._environment_id)
        return self._environment_id

    async def ensure_agent(self) -> str:
        """Return the agent ID, creating one if needed."""
        async with self._lock:
            # Ensure environment first
            await self.ensure_environment()

            if self._agent_id and self._validate_cached_agent():
                return self._agent_id

            logger.info("Creating new managed agent (model=%s)...", self.settings.model)
            agent = self.client.beta.agents.create(
                name="Transcript Analyst",
                model=self.settings.model,
                system=SYSTEM_PROMPT,
                tools=[{"type": "agent_toolset_20260401"}],
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
        agent_id = await self.ensure_agent()
        start = time.time()

        # Create session
        session = self.client.beta.sessions.create(
            agent=agent_id,
            environment_id=self._environment_id,
        )
        session_id = session.id
        logger.info("Session created: %s", session_id)

        # Send transcript and collect response
        full_response = self._send_and_collect(session_id, transcript)

        # Fallback if nothing collected
        if not full_response:
            full_response = self._fallback_response(session_id)

        analysis = self._parse_response(full_response)
        duration = time.time() - start
        usage = self._get_usage(session_id, duration)

        meta = AnalysisMeta(
            agent_id=agent_id,
            session_id=session_id,
            model=self.settings.model,
            duration_seconds=round(duration, 2),
            usage=usage,
        )
        return AnalysisResponse(analysis=analysis, meta=meta)

    # ------------------------------------------------------------------
    # Analysis (streaming SSE)
    # ------------------------------------------------------------------

    async def stream_analysis(self, transcript: str) -> AsyncGenerator[dict[str, Any], None]:
        agent_id = await self.ensure_agent()
        start = time.time()

        yield {"type": "status", "data": "Creating session..."}

        session = self.client.beta.sessions.create(
            agent=agent_id,
            environment_id=self._environment_id,
        )
        session_id = session.id
        yield {"type": "status", "data": f"Session {session_id[:20]}... active"}

        # Send user message
        self.client.beta.sessions.events.send(
            session_id=session_id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": USER_MESSAGE + transcript}],
            }],
        )
        yield {"type": "status", "data": "Agent processing..."}

        # Stream response events
        full_response = ""
        try:
            with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")

                    if etype == "session.status_running":
                        yield {"type": "status", "data": "Container running..."}
                    elif etype == "span.model_request_start":
                        yield {"type": "status", "data": "Model processing transcript..."}
                    elif etype == "agent.thinking":
                        yield {"type": "status", "data": "Agent thinking..."}
                    elif etype == "span.model_request_end":
                        yield {"type": "status", "data": "Formatting response..."}
                    elif etype == "agent.message":
                        if hasattr(event, "content"):
                            for block in event.content:
                                if hasattr(block, "text"):
                                    full_response += block.text
                                    yield {"type": "text", "data": block.text}
                    elif etype == "content_block_delta":
                        if hasattr(event, "delta") and hasattr(event.delta, "text"):
                            full_response += event.delta.text
                            yield {"type": "text", "data": event.delta.text}
                    elif etype == "session.status_idle":
                        break

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
        usage = self._get_usage(session_id, duration)
        meta = AnalysisMeta(
            agent_id=agent_id,
            session_id=session_id,
            model=self.settings.model,
            duration_seconds=round(duration, 2),
            usage=usage,
        )
        response = AnalysisResponse(analysis=analysis, meta=meta)
        yield {"type": "complete", "data": response.model_dump()}

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    _PRICING = {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
        "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    }
    _SESSION_RATE_PER_HOUR = 0.08

    def _get_usage(self, session_id: str, duration_secs: float) -> UsageInfo:
        input_tokens = 0
        output_tokens = 0
        try:
            session = self.client.beta.sessions.retrieve(session_id=session_id)
            if hasattr(session, "usage") and session.usage:
                input_tokens = getattr(session.usage, "input_tokens", 0) or 0
                output_tokens = getattr(session.usage, "output_tokens", 0) or 0
        except Exception as exc:
            logger.warning("Could not retrieve session usage: %s", exc)

        pricing = self._PRICING.get(self.settings.model, self._PRICING["claude-sonnet-4-6"])
        token_cost = (input_tokens / 1_000_000 * pricing["input"]) + (
            output_tokens / 1_000_000 * pricing["output"]
        )
        session_cost = (duration_secs / 3600) * self._SESSION_RATE_PER_HOUR
        total = round(token_cost + session_cost, 6)

        return UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_seconds=int(duration_secs),
            estimated_cost_usd=total,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_and_collect(self, session_id: str, transcript: str) -> str:
        """Send user message and collect full agent response via streaming."""
        # Send the transcript
        self.client.beta.sessions.events.send(
            session_id=session_id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": USER_MESSAGE + transcript}],
            }],
        )

        # Stream and collect response
        full_response = ""
        with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "agent.message":
                    if hasattr(event, "content"):
                        for block in event.content:
                            if hasattr(block, "text"):
                                full_response += block.text
                elif etype == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        full_response += event.delta.text
                elif etype == "session.status_idle":
                    break
        return full_response

    def _fallback_response(self, session_id: str) -> str:
        """Retrieve the last assistant message if streaming yielded nothing."""
        text = ""
        try:
            events = self.client.beta.sessions.events.list(session_id=session_id)
            for evt in reversed(list(events)):
                if hasattr(evt, "role") and evt.role == "assistant":
                    for block in evt.content:
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

        normalised = self._normalise_output(data)

        try:
            return TranscriptAnalysis.model_validate(normalised)
        except Exception as exc:
            logger.warning("Pydantic validation failed: %s", exc)
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
        """Accept both flat (skeleton) and nested (new) formats."""
        if "meeting" in data:
            return data

        meeting = {
            "title": data.get("meeting_title", ""),
            "date": data.get("meeting_date"),
            "attendees": data.get("attendees", []),
            "summary": data.get("summary", ""),
        }

        raw_decisions = data.get("decisions", [])
        decisions = []
        for d in raw_decisions:
            if isinstance(d, str):
                decisions.append({"summary": d, "context": "", "decided_by": [], "confidence": 0.8})
            elif isinstance(d, dict):
                decisions.append(d)

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
