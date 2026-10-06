"""
agents/execution_agent.py — Agent 7: Execution Agent (deterministic).
Executes per mode: API_DIRECT, API_WITH_CONFIRMATION, DEEP_LINK, DEEP_LINK_THEN_PIN.
Tier 0 read-only only for API_DIRECT.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.types import (
    ActionOutcome,
    ConfirmationCard,
    EntityResolutionResult,
    ExtractedSlots,
    ExecutionMode,
    ExecutionResult,
    PolicyDecision,
)
from db.models import Mandate, Payee, Transaction, User
from registry.loader import ActionEntry

logger = logging.getLogger(__name__)


class ExecutionAgent:
    """
    Agent 7: Executes the confirmed action.
    API_DIRECT: backend call, result rendered in chat.
    API_WITH_CONFIRMATION: backend call after confirmation (Tier 1).
    DEEP_LINK: returns deep link for app navigation (Tier 2).
    DEEP_LINK_THEN_PIN: deep link ending in PIN screen (Tier 2M).
    """
    
    def __init__(self):
        self._llm = None

    async def execute(
        self,
        action_entry: ActionEntry,
        policy: PolicyDecision,
        entity_result: EntityResolutionResult,
        confirmation: ConfirmationCard | None,
        session: AsyncSession,
        user_id: str,
        channel: str = "text",
        slots: ExtractedSlots = None,
    ) -> ExecutionResult:
        mode = policy.execution_mode
        action_id = action_entry.action_id
        call_id = str(uuid.uuid4())

        logger.info(
            "ExecutionAgent: action=%s mode=%s user=%s",
            action_id, mode.value, user_id,
        )

        if mode == ExecutionMode.API_DIRECT:
            result = await self._api_direct(action_entry, entity_result, session, user_id, call_id, slots)
        elif mode == ExecutionMode.API_WITH_CONFIRMATION:
            result = await self._api_with_confirmation(action_entry, entity_result, session, user_id, call_id)
        elif mode == ExecutionMode.DEEP_LINK:
            result = self._deep_link(action_entry, entity_result, call_id)
        elif mode == ExecutionMode.DEEP_LINK_THEN_PIN:
            result = self._deep_link_then_pin(action_entry, entity_result, call_id, channel)
        else:
            result = ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id=action_id,
                response_text="Unknown execution mode.",
                downstream_call_id=call_id,
            )
            
        if (self._llm
                and result.outcome == ActionOutcome.SUCCESS
                and result.response_text
                and mode in (ExecutionMode.API_DIRECT, ExecutionMode.API_WITH_CONFIRMATION)):
            # Overwrite the hardcoded template with a conversational LLM summary
            prompt = (
                f"You are the MyUPI Assistant — a helpful, friendly UPI banking assistant.\n"
                f"The user requested an action which was just executed successfully.\n"
                f"Action: {result.action_id}\n"
                f"Technical summary: {result.response_text}\n"
                f"Data context: {result.response_data}\n\n"
                f"Write a short, friendly 1-2 sentence confirmation for the user. "
                f"Be conversational and natural. Do not mention IDs, JSON, or technical details. "
                f"The text may also be spoken aloud, so avoid special characters."
            )
            try:
                conversational_text = await self._llm.complete_text(
                    "You are a helpful, conversational banking assistant.", prompt
                )
                if conversational_text and conversational_text.strip():
                    result.response_text = conversational_text.strip()
            except Exception as e:
                logger.error(f"Failed to generate conversational summary: {e}")
                # Keep the hardcoded text as fallback — do not crash

        return result

    # ── API_DIRECT (Tier 0 read-only) ─────────────────────────────────────────

    async def _api_direct(
        self,
        action_entry: ActionEntry,
        entity_result: EntityResolutionResult,
        session: AsyncSession,
        user_id: str,
        call_id: str,
        slots: ExtractedSlots = None,
    ) -> ExecutionResult:
        action_id = action_entry.action_id

        if action_id == "txn_view":
            return await self._read_transactions(session, user_id, entity_result, call_id, slots)
        elif action_id == "mandate_view":
            return await self._read_mandates(session, user_id, entity_result, call_id)
        elif action_id == "payee_context":
            return await self._read_payee(session, user_id, entity_result, call_id)
        elif action_id == "faq_support":
            return ExecutionResult(
                outcome=ActionOutcome.SUCCESS,
                action_id=action_id,
                response_text="__FAQ__",  # orchestrator routes to FAQ agent
                downstream_call_id=call_id,
            )
        else:
            return ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id=action_id,
                response_text=f"API_DIRECT not implemented for {action_id}",
                downstream_call_id=call_id,
            )

    async def _read_transactions(self, session, user_id, entity_result, call_id, slots: ExtractedSlots = None) -> ExecutionResult:
        limit_val = 10
        if slots and slots.count:
            import re
            m = re.search(r'\d+', slots.count)
            if m:
                limit_val = int(m.group(0))
        
        stmt = select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.created_at.desc()).limit(limit_val)
        result = await session.execute(stmt)
        txns = result.scalars().all()
        data = [
            {
                "ref": t.txn_ref,
                "payee": t.payee_name,
                "amount": float(t.amount),
                "type": t.txn_type,
                "status": t.status,
                "date": t.created_at.isoformat(),
                "bank": t.bank_name,
                "eligible_chargeback": t.eligible_chargeback,
            }
            for t in txns
        ]
        return ExecutionResult(
            outcome=ActionOutcome.SUCCESS,
            action_id="txn_view",
            response_text=f"Here are your last {len(data)} transactions.",
            response_data={"transactions": data},
            downstream_call_id=call_id,
        )

    async def _read_mandates(self, session, user_id, entity_result, call_id) -> ExecutionResult:
        stmt = select(Mandate).where(Mandate.user_id == user_id).order_by(Mandate.start_date.desc())
        result = await session.execute(stmt)
        mandates = result.scalars().all()
        data = [
            {
                "id": m.id,
                "merchant": m.merchant_name,
                "bank": m.bank_name,
                "amount": float(m.amount),
                "frequency": m.frequency,
                "status": m.status,
                "next_debit": m.next_debit_date.isoformat() if m.next_debit_date else None,
            }
            for m in mandates
        ]
        return ExecutionResult(
            outcome=ActionOutcome.SUCCESS,
            action_id="mandate_view",
            response_text=f"You have {len(data)} AutoPay mandates.",
            response_data={"mandates": data},
            downstream_call_id=call_id,
        )

    async def _read_payee(self, session, user_id, entity_result, call_id) -> ExecutionResult:
        resolved = entity_result.resolved.get("payee")
        if not resolved:
            return ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id="payee_context",
                response_text="Could not find payee in your saved contacts.",
                downstream_call_id=call_id,
            )
        stmt = select(Payee).where(Payee.id == resolved.resolved_id)
        result = await session.execute(stmt)
        payee = result.scalar_one_or_none()
        if not payee:
            return ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id="payee_context",
                response_text="Payee not found.",
                downstream_call_id=call_id,
            )
        data = {"name": payee.display_name, "vpa": payee.vpa, "phone": payee.phone, "is_merchant": payee.is_merchant}
        return ExecutionResult(
            outcome=ActionOutcome.SUCCESS,
            action_id="payee_context",
            response_text=f"Here are details for {payee.display_name}.",
            response_data=data,
            downstream_call_id=call_id,
        )

    # ── API_WITH_CONFIRMATION (Tier 1) ────────────────────────────────────────

    async def _api_with_confirmation(
        self,
        action_entry: ActionEntry,
        entity_result: EntityResolutionResult,
        session: AsyncSession,
        user_id: str,
        call_id: str,
    ) -> ExecutionResult:
        action_id = action_entry.action_id
        resolved = entity_result.resolved.get("mandate_id")
        if not resolved:
            return ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id=action_id,
                response_text="Mandate not found.",
                downstream_call_id=call_id,
            )
        mandate_id = resolved.resolved_id

        # Verify ownership (critical — never trust URI params alone)
        stmt = select(Mandate).where(Mandate.id == mandate_id, Mandate.user_id == user_id)
        result = await session.execute(stmt)
        mandate = result.scalar_one_or_none()
        if not mandate:
            logger.error("ExecutionAgent: ownership check FAILED mandate=%s user=%s", mandate_id, user_id)
            return ExecutionResult(
                outcome=ActionOutcome.BLOCKED,
                action_id=action_id,
                response_text="This mandate does not belong to your account.",
                downstream_call_id=call_id,
            )

        if action_id == "mandate_pause":
            new_status = "PAUSED"
            msg = f"AutoPay for {mandate.merchant_name} has been paused."
        elif action_id == "mandate_resume":
            new_status = "ACTIVE"
            msg = f"AutoPay for {mandate.merchant_name} has been resumed."
        elif action_id == "mandate_revoke":
            new_status = "REVOKED"
            msg = f"AutoPay for {mandate.merchant_name} has been revoked."
        else:
            return ExecutionResult(
                outcome=ActionOutcome.ERROR,
                action_id=action_id,
                response_text=f"API_WITH_CONFIRMATION not mapped for {action_id}",
                downstream_call_id=call_id,
            )

        # Update DB directly since it's an API action
        from sqlalchemy import update
        await session.execute(update(Mandate).where(Mandate.id == mandate_id, Mandate.user_id == user_id).values(status=new_status))
        await session.commit()
        
        return ExecutionResult(
            outcome=ActionOutcome.SUCCESS,
            action_id=action_id,
            response_text=msg,
            downstream_call_id=call_id,
        )

    # ── DEEP_LINK (Tier 2) ────────────────────────────────────────────────────

    def _deep_link(
        self,
        action_entry: ActionEntry,
        entity_result: EntityResolutionResult,
        call_id: str,
    ) -> ExecutionResult:
        template = action_entry.deep_link_template or ""
        resolved_ids = {k: v.resolved_id for k, v in entity_result.resolved.items()}

        # Substitute resolved IDs into template (never raw text)
        link = template
        for slot, entity_id in resolved_ids.items():
            link = link.replace(f"{{{slot}}}", entity_id)

        logger.info("ExecutionAgent: DEEP_LINK action=%s link=%s", action_entry.action_id, link)
        return ExecutionResult(
            outcome=ActionOutcome.SUCCESS,
            action_id=action_entry.action_id,
            deep_link=link,
            response_text=f"Tap the button below to complete in the app.",
            downstream_call_id=call_id,
        )

    # ── DEEP_LINK_THEN_PIN (Tier 2M) ──────────────────────────────────────────

    def _deep_link_then_pin(
        self,
        action_entry: ActionEntry,
        entity_result: EntityResolutionResult,
        call_id: str,
        channel: str,
    ) -> ExecutionResult:
        result = self._deep_link(action_entry, entity_result, call_id)
        # Signal to app shell: disable mic on this screen
        if result.response_data is None:
            result.response_data = {}
        result.response_data["mic_disabled"] = True
        result.response_data["requires_pin"] = True
        result.response_text = "This action requires your UPI PIN. Mic is disabled on the next screen."
        logger.info("ExecutionAgent: DEEP_LINK_THEN_PIN — mic_disabled=True")
        return result
