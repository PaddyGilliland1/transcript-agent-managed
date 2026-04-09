"""
Pydantic v2 schemas for transcript analysis input/output.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Output schemas — the JSON structure the agent produces
# ---------------------------------------------------------------------------

class ActionItem(BaseModel):
    """An action item extracted from a meeting transcript."""
    action: str
    owner: str
    deadline: Optional[str] = None
    priority: str = "medium"
    category: str = "follow_up"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_timestamp: Optional[str] = None


class KeyDecision(BaseModel):
    """A key decision made during the meeting."""
    summary: str
    context: str = ""
    decided_by: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Risk(BaseModel):
    """A risk or open question identified in the meeting."""
    description: str
    severity: str = "medium"
    mitigation: Optional[str] = None
    owner: Optional[str] = None


class SpeakerStats(BaseModel):
    """Participation statistics for a single speaker."""
    name: str
    word_count: int = 0
    speaking_time_pct: float = 0.0
    turn_count: int = 0


class MeetingSummary(BaseModel):
    """High-level meeting metadata and summary."""
    title: str = ""
    date: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    summary: str = ""


class TranscriptAnalysis(BaseModel):
    """The complete analysis produced by the managed agent."""
    meeting: MeetingSummary = Field(default_factory=MeetingSummary)
    actions: List[ActionItem] = Field(default_factory=list)
    decisions: List[KeyDecision] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    speakers: List[SpeakerStats] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response metadata
# ---------------------------------------------------------------------------

class UsageInfo(BaseModel):
    """Token usage and estimated cost for a single analysis run."""
    input_tokens: int = 0
    output_tokens: int = 0
    session_seconds: int = 0
    estimated_cost_usd: float = 0.0


class AnalysisMeta(BaseModel):
    agent_id: str = ""
    session_id: str = ""
    processed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str = ""
    duration_seconds: Optional[float] = None
    usage: UsageInfo = Field(default_factory=UsageInfo)


class AnalysisResponse(BaseModel):
    """Top-level API response wrapping analysis + metadata."""
    status: str = "ok"
    output_file: Optional[str] = None
    analysis: TranscriptAnalysis = Field(default_factory=TranscriptAnalysis)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ProcessTextRequest(BaseModel):
    transcript: str = Field(..., min_length=50)
