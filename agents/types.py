"""
agents/types.py — Shared typed I/O models for all agents.
Every agent input and output is defined here. No agent imports from another agent.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class Language(str, Enum):
    EN = "en"
    HI = "hi"
    HI_LATN = "hi-Latn"   # Hinglish (Hindi in Latin script)


class RiskTier(str, Enum):
    ZERO = "0"
    ONE = "1"
    TWO = "2"
    TWO_M = "2M"


class ExecutionMode(str, Enum):
    API_DIRECT = "API_DIRECT"
    API_WITH_CONFIRMATION = "API_WITH_CONFIRMATION"
    DEEP_LINK = "DEEP_LINK"
    DEEP_LINK_THEN_PIN = "DEEP_LINK_THEN_PIN"


class ActionOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    DISAMBIGUATION_NEEDED = "DISAMBIGUATION_NEEDED"
    SLOT_FILL_NEEDED = "SLOT_FILL_NEEDED"


# ── Agent 1: Intent Agent output ──────────────────────────────────────────────

class ExtractedSlots(BaseModel):
    """Raw text slots as extracted by the LLM — never resolved IDs."""
    payee: Optional[str] = None
    merchant_name: Optional[str] = None
    bank_name: Optional[str] = None
    amount: Optional[str] = None
    date_range: Optional[str] = None
    status: Optional[str] = None
    number: Optional[str] = None
    txn_ref: Optional[str] = None
    mandate_id: Optional[str] = None   # raw text reference, not a DB ID
    txn_id: Optional[str] = None       # raw text reference, not a DB ID
    topic: Optional[str] = None
    reason: Optional[str] = None
    count: Optional[str] = None

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class IntentOutput(BaseModel):
    """Output of Intent Agent (Agent 1). Schema-constrained — never contains IDs."""
    intent_label: str
    extracted_slots: ExtractedSlots
    language: Language
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: str  # the input text, stored for audit

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 4)


# ── Agent 3: Slot-Filling output ──────────────────────────────────────────────

class SlotFillRequest(BaseModel):
    """Returned when a required slot is missing."""
    missing_slot: str
    prompt_text: str           # templated clarification prompt
    attempt_number: int = 1    # 1-3; after 3 → direct link


class SlotFillResult(BaseModel):
    slots_complete: bool
    filled_slots: ExtractedSlots
    pending_request: Optional[SlotFillRequest] = None
    fallback_deep_link: Optional[str] = None  # after 3 failed attempts


# ── Agent 4: Entity Resolver output ──────────────────────────────────────────

class ResolvedEntity(BaseModel):
    """A single resolved entity."""
    slot_name: str
    raw_text: str
    resolved_id: str
    resolved_label: str          # human-readable confirmation label
    confidence: float
    disambiguation_candidates: list["ResolvedEntity"] = Field(default_factory=list)


class EntityResolutionResult(BaseModel):
    resolved: dict[str, ResolvedEntity] = Field(default_factory=dict)
    needs_disambiguation: list[str] = Field(default_factory=list)   # slot names
    disambiguation_options: dict[str, list[ResolvedEntity]] = Field(default_factory=dict)
    unresolvable: list[str] = Field(default_factory=list)  # slot names


# ── Agent 5: Policy/Risk output ───────────────────────────────────────────────

class PolicyDecision(BaseModel):
    allowed: bool
    tier: RiskTier
    execution_mode: ExecutionMode
    voice_safe: bool
    block_reason: Optional[str] = None  # set only when allowed=False


# ── Agent 6: Confirmation output ──────────────────────────────────────────────

class ConfirmationCard(BaseModel):
    action_id: str
    template_id: str
    template_version: str
    language: Language
    card_text: str              # rendered from template + backend data (never from transcript)
    spoken_text: Optional[str] = None  # TTS-ready version (None if voice=off or non-voice)
    event_id: str               # unique ID for this confirmation instance → audit chain
    resolved_entity_ids: dict[str, str]  # slot_name → resolved DB ID


# ── Agent 7: Execution output ─────────────────────────────────────────────────

class ExecutionResult(BaseModel):
    outcome: ActionOutcome
    action_id: str
    deep_link: Optional[str] = None
    response_text: str
    response_data: Optional[dict[str, Any]] = None  # for API_DIRECT results
    downstream_call_id: str = Field(default_factory=lambda: str(__import__("uuid").uuid4()))


# ── Agent 9: Audit ────────────────────────────────────────────────────────────

class AuditChain(BaseModel):
    """All links in the audit chain. Every link must be present before execution."""
    session_id: str
    user_id: str
    action_id: str
    policy_tier: str
    voice_session_id: Optional[str] = None
    transcript_hash: Optional[str] = None
    resolved_entity_ids: Optional[dict[str, str]] = None
    confirmation_event_id: Optional[str] = None
    confirmation_template_id: Optional[str] = None
    confirmation_template_version: Optional[str] = None
    downstream_call_id: Optional[str] = None

    def compute_transcript_hash(self, transcript: str) -> str:
        return hashlib.sha256(transcript.encode()).hexdigest()

    def is_complete_for_tier(self, tier: str) -> tuple[bool, list[str]]:
        """
        Returns (True, []) if chain is complete for the given tier,
        or (False, [missing_links]) if not.
        Tier 0: only session_id + policy_tier required.
        Tier 1+: also confirmation_event_id.
        Tier 2+: also confirmed by auth factor (tracked via confirmation_event_id).
        Voice: also transcript_hash.
        """
        missing = []
        if not self.session_id:
            missing.append("session_id")
        if not self.policy_tier:
            missing.append("policy_tier")
        if tier in ("1", "2", "2M"):
            if not self.confirmation_event_id:
                missing.append("confirmation_event_id")
        if self.voice_session_id and not self.transcript_hash:
            missing.append("transcript_hash")
        return (len(missing) == 0, missing)


# ── Orchestrator state ────────────────────────────────────────────────────────

class PipelineState(BaseModel):
    """State threaded through the orchestrator. Immutable between agents (replaced, not mutated)."""
    # Input
    user_id: str
    session_id: str
    conversation_id: str
    channel: str = "text"  # text|voice
    raw_input: str

    # Agent outputs
    intent: Optional[IntentOutput] = None
    action_entry: Optional[Any] = None  # registry.loader.ActionEntry
    slot_fill: Optional[SlotFillResult] = None
    entity_resolution: Optional[EntityResolutionResult] = None
    policy: Optional[PolicyDecision] = None
    confirmation: Optional[ConfirmationCard] = None
    execution: Optional[ExecutionResult] = None

    # Audit chain (grows through the pipeline)
    audit: AuditChain = Field(default_factory=lambda: AuditChain(
        session_id="", user_id="", action_id="", policy_tier=""
    ))

    # Flow control
    needs_user_input: bool = False   # waiting for slot or disambiguation
    user_prompt: Optional[str] = None
    error: Optional[str] = None
    done: bool = False

    # Slot fill attempt tracking
    slot_fill_attempts: dict[str, int] = Field(default_factory=dict)

    # Voice context
    voice_session_id: Optional[str] = None
    word_confidences: Optional[list[dict]] = None

    class Config:
        arbitrary_types_allowed = True
