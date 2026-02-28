from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SubmissionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCall(BaseModel):
    tool_name: str
    arguments: str = ""
    result: Optional[str] = None


class ReasoningBlock(BaseModel):
    content: str


class Message(BaseModel):
    role: MessageRole
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    reasoning: list[ReasoningBlock] = Field(default_factory=list)
    order: int


class SubmissionListItem(BaseModel):
    """Row from the submissions list page — used to drive detail page visits."""

    submission_id: str
    chat_id: str
    detail_url: str
    model_name: str
    behavior_snippet: str
    timestamp_raw: str
    status: SubmissionStatus
    arena: str


class Submission(BaseModel):
    """Full submission data after visiting the detail page."""

    submission_id: str
    chat_id: str
    arena: str
    model_name: str
    behavior_name: str
    behavior_criteria: str = ""
    behavior_type: str = ""  # e.g. "Data Exfiltration" (safeguards), "Chat", "Image" (proving-ground)
    attack_type: str = ""  # e.g. "Direct"
    wave: str
    timestamp_raw: str
    status: SubmissionStatus
    conversation: list[Message] = Field(default_factory=list)
    detail_url: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class ScrapeProgress(BaseModel):
    """Tracks resumability per arena."""

    arena: str
    total_submissions: Optional[int] = None
    scraped_ids: list[str] = Field(default_factory=list)
    last_scraped_at: Optional[datetime] = None
