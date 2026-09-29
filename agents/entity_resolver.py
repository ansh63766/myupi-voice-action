"""
agents/entity_resolver.py — Agent 4: Entity Resolver (deterministic).
Fetches candidates from DB using disambiguation_source,
runs transliteration-aware fuzzy match, returns resolved IDs.
IDs NEVER go through the LLM.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.fuzzy import FuzzyMatcherAdapter, FuzzyCandidate, get_fuzzy_adapter
from agents.types import EntityResolutionResult, ExtractedSlots, ResolvedEntity
from config.settings import get_config
from db.models import Mandate, Payee, Transaction, UPINumber, BankAccount
from registry.loader import ActionEntry

logger = logging.getLogger(__name__)

# Maps disambiguation_source name → (model_class, text_field, label_field)
_SOURCE_MAP = {
    "user_transactions": (Transaction, "payee_name", "payee_name"),
    "user_active_mandates": (Mandate, "merchant_name", "merchant_name"),
    "user_paused_mandates": (Mandate, "merchant_name", "merchant_name"),
    "user_saved_payees": (Payee, "display_name", "display_name"),
    "user_upi_numbers": (UPINumber, "number", "vpa"),
    "user_eligible_transactions": (Transaction, "payee_name", "payee_name"),
    "user_bank_accounts": (BankAccount, "bank_name", "bank_name"),
}

# Maps slot name → which disambiguation_source field to match against
_SLOT_SOURCE = {
    "mandate_id": "user_active_mandates",
    "txn_id": "user_transactions",
    "payee": "user_saved_payees",
    "number": "user_upi_numbers",
    "merchant_name": "user_active_mandates",
}


async def _fetch_candidates(
    session: AsyncSession,
    user_id: str,
    source_name: str,
    extra_filter: Optional[str] = None,  # e.g., only ACTIVE mandates
) -> list[dict]:
    """Fetch candidate rows from the DB for a given user and source."""
    mapping = _SOURCE_MAP.get(source_name)
    if mapping is None:
        return []

    model, text_field, label_field = mapping
    stmt = select(model).where(model.user_id == user_id)  # type: ignore

    # Source-specific filters
    if source_name == "user_active_mandates":
        stmt = stmt.where(model.status == "ACTIVE")  # type: ignore
    elif source_name == "user_paused_mandates":
        stmt = stmt.where(model.status == "PAUSED")  # type: ignore
    elif source_name == "user_eligible_transactions":
        stmt = stmt.where(model.eligible_chargeback == True)  # type: ignore
        stmt = stmt.where(model.chargeback_raised == False)  # type: ignore

    result = await session.execute(stmt)
    rows = result.scalars().all()

    candidates = []
    for row in rows:
        candidates.append({
            "id": row.id,
            "text": getattr(row, text_field, ""),
            "label": getattr(row, label_field, ""),
            "extra": row,  # full row for label building
        })
    return candidates


def _build_label(candidate: dict, source_name: str) -> str:
    """Build human-readable label for disambiguation display."""
    row = candidate.get("extra")
    if row is None:
        return candidate["text"]

    if source_name in ("user_active_mandates", "user_paused_mandates"):
        return f"{row.merchant_name} ({row.bank_name}, ₹{row.amount}/{row.frequency.lower()})"
    elif source_name in ("user_transactions", "user_eligible_transactions"):
        return f"{row.payee_name} — ₹{row.amount} on {row.created_at.strftime('%d %b')}"
    elif source_name == "user_saved_payees":
        return f"{row.display_name} ({row.vpa})"
    elif source_name == "user_upi_numbers":
        return f"{row.number} ({row.vpa})"
    elif source_name == "user_bank_accounts":
        return f"{row.bank_name} (...{row.account_last4})"
    return candidate["text"]


class EntityResolverAgent:
    """
    Agent 4: Deterministic entity resolution.
    - Fetches user's candidates from DB.
    - Fuzzy-matches raw text to closed candidate set.
    - Returns resolved IDs (never the LLM).
    - Thresholds: ≥resolve → silent resolve; 60–84 → disambiguate; <60 → no match.
    """

    def __init__(self, fuzzy: FuzzyMatcherAdapter | None = None):
        self._fuzzy = fuzzy

    @property
    def fuzzy(self) -> FuzzyMatcherAdapter:
        if self._fuzzy is None:
            self._fuzzy = get_fuzzy_adapter()
        return self._fuzzy

    async def resolve(
        self,
        action_entry: ActionEntry,
        slots: ExtractedSlots,
        user_id: str,
        session: AsyncSession,
    ) -> EntityResolutionResult:
        """
        Resolve all required slots that need entity matching.
        Returns EntityResolutionResult with resolved IDs and any disambiguation needs.
        """
        cfg = get_config().fuzzy
        resolve_threshold = cfg.resolve_threshold
        disambiguate_threshold = cfg.disambiguate_threshold

        result = EntityResolutionResult()
        filled = slots.to_dict()

        # Determine which slots need entity resolution
        source_name = action_entry.disambiguation_source
        if not source_name:
            # No entity resolution needed (e.g., faq, safety_switch)
            return result

        # Map each required slot to its source and query
        for slot in action_entry.required_slots:
            raw_text = filled.get(slot)
            if not raw_text:
                continue  # slot-filling agent handles missing slots

            # Prefer action-specific source, fallback to slot-default
            slot_source = action_entry.disambiguation_source or _SLOT_SOURCE.get(slot)

            candidates = await _fetch_candidates(session, user_id, slot_source)
            if not candidates:
                result.unresolvable.append(slot)
                logger.warning("EntityResolver: no candidates found in '%s' for user %s", slot_source, user_id)
                continue

            if raw_text == "__ALL__":
                # User did not provide a slot (e.g., just said "Pause my autopay")
                # Force disambiguation with ALL available choices immediately
                from adapters.fuzzy import FuzzyCandidate
                matches = [FuzzyCandidate(candidate_id=c["id"], matched_text=c["text"], score=75.0) for c in candidates[:5]]
            else:
                matches = self.fuzzy.rank(raw_text, candidates)

            if not matches or matches[0].score < disambiguate_threshold:
                # ── LLM SEMANTIC RESCUE ──
                logger.info("EntityResolver: Fuzzy failed for '%s'. Attempting Semantic Rescue...", raw_text)
                from adapters.llm import get_llm_adapter
                llm = get_llm_adapter()
                
                cand_list = [{"id": c["id"], "name": _build_label(c, slot_source)} for c in candidates]
                sys_prompt = (
                    "You are a semantic entity matcher. Match the user's phrase to the correct ID from the list based on meaning. "
                    "For example, 'food delivery' matches 'Swiggy', 'internet' matches 'Jio'. "
                    "If none of them logically fit, return null."
                )
                user_msg = f"User Phrase: '{raw_text}'\nCandidates: {cand_list}"
                schema = {
                    "type": "object",
                    "properties": {
                        "matched_id": {"type": ["string", "null"]},
                        "reason": {"type": "string"}
                    },
                    "required": ["matched_id", "reason"]
                }
                
                try:
                    llm_res = await llm.complete_json(sys_prompt, user_msg, schema)
                    matched_id = llm_res.get("matched_id")
                    if matched_id:
                        cand_map = {c["id"]: c for c in candidates}
                        cand = cand_map.get(matched_id)
                        if cand:
                            label = _build_label(cand, slot_source)
                            result.resolved[slot] = ResolvedEntity(
                                slot_name=slot,
                                raw_text=raw_text,
                                resolved_id=matched_id,
                                resolved_label=label,
                                confidence=0.80, # Treat semantic rescue as ambiguous to force disambiguation card
                            )
                            logger.info("EntityResolver: SEMANTIC RESCUE success: '%s' → %s", raw_text, label)
                            continue
                except Exception as e:
                    logger.error("EntityResolver: Semantic rescue failed: %s", e)

                # If LLM also failed or returned null:
                result.unresolvable.append(slot)
                logger.info("EntityResolver: no match above threshold for '%s'", raw_text)
                continue

            top = matches[0]

            # Check for ambiguity: if the second best match is tied or too close in score
            is_ambiguous = False
            if len(matches) > 1:
                runner_up = matches[1]
                if (top.score - runner_up.score) < 5.0 and runner_up.score >= disambiguate_threshold:
                    is_ambiguous = True

            if top.score >= resolve_threshold and not is_ambiguous:
                # Silent resolve — use dict for O(1) safe lookup
                cand_map = {c["id"]: c for c in candidates}
                cand = cand_map.get(top.candidate_id)
                label = _build_label(cand, slot_source) if cand else top.matched_text
                result.resolved[slot] = ResolvedEntity(
                    slot_name=slot,
                    raw_text=raw_text,
                    resolved_id=top.candidate_id,
                    resolved_label=label,
                    confidence=top.score / 100.0,
                )
                logger.info(
                    "EntityResolver: '%s' → id=%s label='%s' (score=%.1f)",
                    raw_text, top.candidate_id, label, top.score,
                )
            elif top.score >= disambiguate_threshold or (top.score >= resolve_threshold and is_ambiguous):
                # Need disambiguation
                result.needs_disambiguation.append(slot)
                cand_map = {c["id"]: c for c in candidates}
                resolved_candidates = []
                for m in matches:
                    c = cand_map.get(m.candidate_id)
                    if c:
                        lbl = _build_label(c, slot_source)
                        resolved_candidates.append(ResolvedEntity(
                            slot_name=slot,
                            raw_text=raw_text,
                            resolved_id=m.candidate_id,
                            resolved_label=lbl,
                            confidence=m.score / 100.0,
                        ))
                result.disambiguation_options[slot] = resolved_candidates
                logger.info(
                    "EntityResolver: disambiguation needed for '%s', %d candidates",
                    slot, len(resolved_candidates),
                )
            else:
                result.unresolvable.append(slot)
                logger.info(
                    "EntityResolver: no match above threshold for '%s' (best=%.1f)",
                    raw_text, top.score,
                )

        return result
