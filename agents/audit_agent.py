"""
agents/audit_agent.py — Agent 9: Audit Agent (deterministic).
Writes the unbroken audit chain. BLOCKS execution if any link is missing.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agents.types import ActionOutcome, AuditChain, ExecutionResult
from db.models import AuditLog

logger = logging.getLogger(__name__)


class AuditError(Exception):
    """Raised when the audit chain is incomplete — blocks execution."""
    pass


class AuditAgent:
    """
    Agent 9: Writes and verifies the unbroken audit chain.
    Chain: session_id → [transcript_hash] → resolved_entity_ids → policy_tier
           → confirmation_event_id → downstream_call_id
    Missing any required link → action does NOT fire.
    """

    def verify_chain(self, chain: AuditChain, tier: str) -> None:
        """
        Verify that the audit chain is complete for the given tier.
        Raises AuditError if any link is missing.
        """
        complete, missing = chain.is_complete_for_tier(tier)
        if not complete:
            msg = f"Audit chain incomplete for tier {tier}: missing {missing}"
            logger.error("AuditAgent: BLOCKING execution — %s", msg)
            raise AuditError(msg)
        logger.info("AuditAgent: chain verified for tier=%s action=%s", tier, chain.action_id)

    async def write(
        self,
        chain: AuditChain,
        outcome: ActionOutcome,
        session: AsyncSession,
        error_detail: str | None = None,
    ) -> str:
        """Write audit log entry. Returns the audit log row ID."""
        entry = AuditLog(
            id=str(uuid.uuid4()),
            session_id=chain.session_id,
            voice_session_id=chain.voice_session_id,
            transcript_hash=chain.transcript_hash,
            resolved_entity_ids=json.dumps(chain.resolved_entity_ids) if chain.resolved_entity_ids else None,
            policy_tier=chain.policy_tier,
            confirmation_event_id=chain.confirmation_event_id,
            confirmation_template_id=chain.confirmation_template_id,
            confirmation_template_version=chain.confirmation_template_version,
            downstream_call_id=chain.downstream_call_id,
            action_id=chain.action_id,
            user_id=chain.user_id,
            outcome=outcome.value,
            error_detail=error_detail,
            created_at=datetime.utcnow(),
        )
        session.add(entry)
        await session.commit()
        logger.info(
            "AuditAgent: wrote log id=%s action=%s outcome=%s",
            entry.id, chain.action_id, outcome.value,
        )
        return entry.id

    async def write_blocked(
        self,
        chain: AuditChain,
        reason: str,
        session: AsyncSession,
    ) -> str:
        """Write a BLOCKED audit entry (chain incomplete or policy denied)."""
        return await self.write(chain, ActionOutcome.BLOCKED, session, error_detail=reason)
