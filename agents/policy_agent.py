"""
agents/policy_agent.py — Agent 5: Policy/Risk Agent (deterministic).
Reads tier from registry (immutable). Enforces tier rules, voice_safe, confidence gating.
NEVER infers tier from text or model output.
"""
from __future__ import annotations

import logging

from agents.types import (
    EntityResolutionResult,
    ExecutionMode,
    IntentOutput,
    PolicyDecision,
    RiskTier,
)
from registry.loader import ActionEntry

logger = logging.getLogger(__name__)

# Minimum confidence threshold for intent before we ask for disambiguation
INTENT_CONFIDENCE_THRESHOLD = 0.6


class PolicyRiskAgent:
    """
    Agent 5: Deterministic policy enforcement.
    - Tier is read from registry only — never inferred.
    - voice_safe=False blocks voice-initiated consequential actions.
    - Low confidence → route to disambiguation, not confirmation.
    """

    def evaluate(
        self,
        action_entry: ActionEntry,
        intent_output: IntentOutput,
        entity_result: EntityResolutionResult,
        channel: str = "text",  # "text" or "voice"
        user_confirmed: bool = False,
    ) -> PolicyDecision:
        """
        Returns PolicyDecision with allowed=True/False and reason.
        """
        tier_raw = str(action_entry.risk_tier)

        try:
            tier = RiskTier(tier_raw)
        except ValueError:
            tier = RiskTier.ZERO

        mode_str = action_entry.execution_mode
        try:
            mode = ExecutionMode(mode_str)
        except ValueError:
            mode = ExecutionMode.API_DIRECT

        voice_safe = action_entry.voice_safe

        # Rule 1: voice_safe=False on voice channel → block immediately
        if channel == "voice" and not voice_safe:
            reason = (
                f"Action '{action_entry.action_id}' is not voice-safe. "
                "Please use the app directly."
            )
            logger.warning("PolicyAgent: BLOCKED (voice_safe=False) action=%s", action_entry.action_id)
            return PolicyDecision(
                allowed=False,
                tier=tier,
                execution_mode=mode,
                voice_safe=voice_safe,
                block_reason=reason,
            )

        # Rule 2: Tier 2/2M via voice requires on-screen confirmation (voice never completes)
        # This is enforced by execution mode — DEEP_LINK/DEEP_LINK_THEN_PIN require app screen.
        # We log it but do NOT block — the execution agent enforces the actual screen.

        # Rule 3: Low intent confidence → force disambiguation (not a hard block)
        if intent_output.confidence < INTENT_CONFIDENCE_THRESHOLD:
            logger.info(
                "PolicyAgent: low confidence (%.2f) for intent '%s', suggesting disambiguation",
                intent_output.confidence, intent_output.intent_label,
            )
            # Not a block — return allowed=True but the orchestrator reads confidence
            # and shows disambiguation. Policy doesn't block low-confidence; it flags it.

        # Rule 4: Unresolvable required entities → block
        if entity_result.unresolvable:
            slots_str = ", ".join(entity_result.unresolvable)
            reason = (
                f"I couldn't find a matching {slots_str} in your account. "
                "Please check the name and try again, or use the app directly."
            )
            logger.warning(
                "PolicyAgent: BLOCKED (unresolvable entities=%s) action=%s",
                entity_result.unresolvable, action_entry.action_id,
            )
            return PolicyDecision(
                allowed=False,
                tier=tier,
                execution_mode=mode,
                voice_safe=voice_safe,
                block_reason=reason,
            )

        # Rule 5: Disambiguation still needed → not allowed to execute yet
        if entity_result.needs_disambiguation:
            slots_str = ", ".join(entity_result.needs_disambiguation)
            reason = f"Disambiguation needed for: {slots_str}"
            logger.info("PolicyAgent: disambiguation needed for %s", entity_result.needs_disambiguation)
            return PolicyDecision(
                allowed=False,
                tier=tier,
                execution_mode=mode,
                voice_safe=voice_safe,
                block_reason=reason,
            )

        # Rule 6: Tier 1+ requires confirmation (checked by orchestrator via user_confirmed flag)
        if tier in (RiskTier.ONE, RiskTier.TWO, RiskTier.TWO_M) and not user_confirmed:
            logger.info(
                "PolicyAgent: tier=%s requires confirmation before execution", tier_raw
            )
            return PolicyDecision(
                allowed=False,
                tier=tier,
                execution_mode=mode,
                voice_safe=voice_safe,
                block_reason="__awaiting_confirmation__",  # special sentinel for orchestrator
            )

        logger.info(
            "PolicyAgent: ALLOWED action=%s tier=%s mode=%s channel=%s",
            action_entry.action_id, tier_raw, mode_str, channel,
        )
        return PolicyDecision(
            allowed=True,
            tier=tier,
            execution_mode=mode,
            voice_safe=voice_safe,
        )
