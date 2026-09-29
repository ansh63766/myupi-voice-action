"""
agents/registry_lookup.py — Agent 2: Registry Lookup (deterministic).
intent_label → ActionEntry. Zero feature logic.
"""
from __future__ import annotations

import logging

from agents.types import IntentOutput
from registry.loader import ActionEntry, get_registry

logger = logging.getLogger(__name__)


class RegistryLookupAgent:
    """Agent 2: Deterministic intent → registry entry."""

    def lookup(self, intent_output: IntentOutput) -> ActionEntry | None:
        registry = get_registry()
        entry = registry.lookup_by_intent(intent_output.intent_label)
        if entry is None:
            logger.warning(
                "No registry entry for intent '%s'; user gets fallback message.",
                intent_output.intent_label,
            )
        else:
            logger.info(
                "Registry: '%s' → action '%s' (tier=%s, mode=%s)",
                intent_output.intent_label,
                entry.action_id,
                entry.risk_tier,
                entry.execution_mode,
            )
        return entry
