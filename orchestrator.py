"""
orchestrator.py — The Orchestrator.
Passes state between agents. Contains ZERO feature-specific logic.
Adding a new action = adding a registry entry only.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agents.audit_agent import AuditAgent, AuditError
from agents.confirmation_agent import ConfirmationAgent
from agents.entity_resolver import EntityResolverAgent
from agents.execution_agent import ExecutionAgent
from agents.intent_agent import IntentAgent
from agents.policy_agent import PolicyRiskAgent
from agents.registry_lookup import RegistryLookupAgent
from agents.slot_filling_agent import SlotFillingAgent
from agents.support_agent import SupportFAQAgent
from agents.types import (
    ActionOutcome,
    AuditChain,
    EntityResolutionResult,
    Language,
    PipelineState,
)
from adapters.llm import LLMError

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Stateless orchestrator. All state is in PipelineState.
    Zero feature logic — reads registry and passes state between agents.
    """

    def __init__(self):
        self.intent_agent = IntentAgent()
        self.registry_lookup = RegistryLookupAgent()
        self.slot_filler = SlotFillingAgent()
        self.entity_resolver = EntityResolverAgent()
        self.policy_agent = PolicyRiskAgent()
        self.confirmation_agent = ConfirmationAgent()
        self.execution_agent = ExecutionAgent()
        self.support_agent = SupportFAQAgent()
        self.audit_agent = AuditAgent()

    async def process(
        self,
        state: PipelineState,
        db: AsyncSession,
        user_confirmed: bool = False,
        selected_entity: Optional[dict] = None,  # from disambiguation tap
    ) -> PipelineState:
        """
        Run one full pipeline pass. Returns updated PipelineState.
        Called repeatedly for multi-turn dialogue (slot fill, disambiguation).
        """
        try:
            return await self._run_pipeline(state, db, user_confirmed, selected_entity)
        except LLMError as e:
            logger.error("Orchestrator: LLM error — %s", e)
            state.error = f"AI service is temporarily unavailable. Please try again or use text."
            state.done = True
            # Audit BLOCKED
            if state.intent:
                chain = state.audit
                chain.action_id = chain.action_id or "unknown"
                chain.policy_tier = chain.policy_tier or "0"
                await self.audit_agent.write_blocked(chain, str(e), db)
            return state
        except AuditError as e:
            logger.error("Orchestrator: audit chain failure — %s", e)
            state.error = "This action could not be completed due to a security check failure."
            state.done = True
            return state
        except Exception as e:
            logger.exception("Orchestrator: unexpected error")
            state.error = f"Error: {str(e)}"
            state.done = True
            return state

    async def _run_pipeline(
        self,
        state: PipelineState,
        db: AsyncSession,
        user_confirmed: bool,
        selected_entity: Optional[dict],
    ) -> PipelineState:
        # ── Initialise audit chain ─────────────────────────────────────────────
        if not state.audit.session_id:
            state.audit = AuditChain(
                session_id=state.session_id,
                user_id=state.user_id,
                action_id="",
                policy_tier="0",
                voice_session_id=state.voice_session_id,
            )

        # ── Step 1: Intent ─────────────────────────────────────────────────────
        if state.intent is None:
            logger.info("Orchestrator [Step 1]: Intent classification")
            state.intent = await self.intent_agent.classify(
                state.raw_input,
                language_hint=None,
            )
            # Compute transcript hash for voice audit chain
            if state.voice_session_id:
                state.audit.transcript_hash = state.audit.compute_transcript_hash(state.raw_input)

        # ── Step 2: Registry Lookup ────────────────────────────────────────────
        if state.action_entry is None:
            logger.info("Orchestrator [Step 2]: Registry lookup — intent=%s", state.intent.intent_label)
            state.action_entry = self.registry_lookup.lookup(state.intent)
            if state.action_entry is None:
                if state.intent.intent_label == "ambiguous":
                    target = state.intent.extracted_slots.merchant_name or state.intent.extracted_slots.topic
                    if target:
                        state.error = (
                            f"Did you want to **temporarily pause** or **permanently cancel** {target} AutoPay?\n\n"
                            f"- Say **'Pause {target}'** to pause debits for now.\n"
                            f"- Say **'Revoke {target}'** to cancel the mandate completely."
                        )
                    else:
                        state.error = "I didn't quite catch that. Could you please rephrase?"
                elif state.intent.intent_label == "unsupported":
                    state.error = (
                        "I cannot process direct peer-to-peer money transfers in this prototype. "
                        "I can help you view transaction history, pause/revoke AutoPay mandates, file dispute chargebacks, or answer UPI questions."
                    )
                else:
                    state.error = (
                        "I'm not sure how to help with that. "
                        "Try asking about your transactions, mandates, or UPI settings."
                    )
                state.done = True
                return state
            state.audit.action_id = state.action_entry.action_id
            state.audit.policy_tier = str(state.action_entry.risk_tier)

        action_entry = state.action_entry

        # ── FAQ shortcut ───────────────────────────────────────────────────────
        if action_entry.action_id == "faq_support":
            logger.info("Orchestrator: FAQ path")
            answer = await self.support_agent.answer(
                state.raw_input, db, language=state.intent.language.value
            )
            state.execution = None
            state.done = True
            state.user_prompt = answer
            await self.audit_agent.write(state.audit, ActionOutcome.SUCCESS, db)
            return state

        # ── Step 3: Slot Filling ───────────────────────────────────────────────
        logger.info("Orchestrator [Step 3]: Slot filling")

        # If user responded to a disambiguation (tap-to-select)
        if selected_entity and state.entity_resolution and state.entity_resolution.needs_disambiguation:
            slot_name = selected_entity.get("slot")
            entity_id = selected_entity.get("id")
            entity_label = selected_entity.get("label", entity_id)
            if slot_name and entity_id and state.entity_resolution:
                from agents.types import ResolvedEntity
                state.entity_resolution.resolved[slot_name] = ResolvedEntity(
                    slot_name=slot_name,
                    raw_text=entity_label,
                    resolved_id=entity_id,
                    resolved_label=entity_label,
                    confidence=1.0,
                )
                state.entity_resolution.needs_disambiguation.remove(slot_name)
                if slot_name in state.entity_resolution.disambiguation_options:
                    del state.entity_resolution.disambiguation_options[slot_name]
                # Reset downstream state so policy + confirmation are re-evaluated fresh
                state.policy = None
                state.confirmation = None
                state.needs_user_input = False
                state.user_prompt = None

        slot_result = self.slot_filler.check(
            action_entry=action_entry,
            extracted_slots=state.intent.extracted_slots,
            slot_attempts=state.slot_fill_attempts,
            language=state.intent.language.value,
        )
        state.slot_fill = slot_result

        if not slot_result.slots_complete:
            if slot_result.fallback_deep_link:
                state.user_prompt = (
                    f"I wasn't able to get the required information after multiple attempts. "
                    f"Please use this direct link: {slot_result.fallback_deep_link}"
                )
                state.done = True
                await self.audit_agent.write_blocked(state.audit, "slot_fill_max_attempts", db)
            else:
                req = slot_result.pending_request
                # Increment attempt counter
                state.slot_fill_attempts[req.missing_slot] = req.attempt_number
                state.needs_user_input = True
                state.user_prompt = req.prompt_text
            return state

        # ── Step 4: Entity Resolution ──────────────────────────────────────────
        if state.entity_resolution is None:
            logger.info("Orchestrator [Step 4]: Entity resolution")
            state.entity_resolution = await self.entity_resolver.resolve(
                action_entry=action_entry,
                slots=slot_result.filled_slots,
                user_id=state.user_id,
                session=db,
            )
            # Update audit chain with resolved IDs
            state.audit.resolved_entity_ids = {
                k: v.resolved_id for k, v in state.entity_resolution.resolved.items()
            }

        entity_result = state.entity_resolution

        # Check disambiguation need
        if entity_result.needs_disambiguation:
            state.needs_user_input = True
            # Build disambiguation message
            slot = entity_result.needs_disambiguation[0]
            options = entity_result.disambiguation_options.get(slot, [])
            slot_clean = slot.replace('_id', '').replace('_', ' ')
            state.user_prompt = f"Please select the {slot_clean} from the options below:"
            # The disambiguation_options are passed to the UI for tap-to-select
            return state

        # Check unresolvable
        if entity_result.unresolvable:
            slots_str = ", ".join(entity_result.unresolvable)
            state.error = (
                f"I couldn't find a match for {slots_str} in your account. "
                "Please check the name or use the app directly."
            )
            state.done = True
            await self.audit_agent.write_blocked(state.audit, f"unresolvable: {slots_str}", db)
            return state

        # ── Step 5: Policy/Risk ────────────────────────────────────────────────
        logger.info("Orchestrator [Step 5]: Policy evaluation")
        policy = self.policy_agent.evaluate(
            action_entry=action_entry,
            intent_output=state.intent,
            entity_result=entity_result,
            channel=state.channel,
            user_confirmed=user_confirmed,
        )
        state.policy = policy

        if not policy.allowed:
            if policy.block_reason == "__awaiting_confirmation__":
                # Need to show confirmation card first
                logger.info("Orchestrator [Step 6]: Rendering confirmation card")
                confirmation = await self.confirmation_agent.render(
                    action_entry=action_entry,
                    entity_result=entity_result,
                    policy=policy,
                    language=state.intent.language,
                    session=db,
                    channel=state.channel,
                )
                state.confirmation = confirmation
                state.audit.confirmation_event_id = confirmation.event_id
                state.audit.confirmation_template_id = confirmation.template_id
                state.audit.confirmation_template_version = confirmation.template_version
                state.needs_user_input = True
                state.user_prompt = confirmation.card_text
                return state
            else:
                state.error = policy.block_reason
                state.done = True
                await self.audit_agent.write_blocked(state.audit, policy.block_reason or "policy_denied", db)
                return state

        # ── Step 6: Confirmation (already rendered above for tier 1+) ─────────
        # If we get here with user_confirmed=True, confirmation was already shown.
        # For Tier 0 (API_DIRECT), no confirmation needed.

        # ── Step 7 + Step 9: Execution + Audit ────────────────────────────────
        logger.info("Orchestrator [Step 7]: Execution")

        # Verify audit chain before execution
        self.audit_agent.verify_chain(state.audit, str(action_entry.risk_tier))

        exec_result = await self.execution_agent.execute(
            action_entry=action_entry,
            policy=policy,
            entity_result=entity_result,
            confirmation=state.confirmation,
            session=db,
            user_id=state.user_id,
            channel=state.channel,
            slots=state.intent.extracted_slots,
        )
        state.execution = exec_result
        state.audit.downstream_call_id = exec_result.downstream_call_id

        # Handle FAQ execution marker
        if exec_result.response_text == "__FAQ__":
            answer = await self.support_agent.answer(
                state.raw_input, db, language=state.intent.language.value
            )
            state.user_prompt = answer
            state.done = True
            await self.audit_agent.write(state.audit, ActionOutcome.SUCCESS, db)
            return state

        # Write audit log
        await self.audit_agent.write(state.audit, exec_result.outcome, db)

        state.done = True
        return state
