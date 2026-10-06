"""
agents/intent_agent.py — Agent 1: Intent classification.
LLM classifies intent and extracts raw slot text.
NEVER decides actions, IDs, or confirmation strings.
"""
from __future__ import annotations

import logging

from adapters.llm import LLMAdapter, LLMError, get_llm_adapter
from agents.types import ExtractedSlots, IntentOutput, Language
from registry.loader import get_registry

logger = logging.getLogger(__name__)

# JSON schema for constrained LLM output
_INTENT_SCHEMA = {
    "type": "object",
    "required": ["intent_label", "extracted_slots", "language", "confidence"],
    "properties": {
        "intent_label": {
            "type": "string",
            "description": "One of the valid intent labels from the registry."
        },
        "extracted_slots": {
            "type": "object",
            "properties": {
                "payee": {"type": "string"},
                "merchant_name": {"type": "string"},
                "bank_name": {"type": "string"},
                "amount": {"type": "string"},
                "date_range": {"type": "string"},
                "status": {"type": "string"},
                "number": {"type": "string"},
                "txn_ref": {"type": "string"},
                "mandate_id": {"type": "string"},
                "txn_id": {"type": "string"},
                "topic": {"type": "string"},
                "reason": {"type": "string"},
                "count": {"type": "string", "description": "Number of items requested, e.g. '3', '5', 'last 10'"}
            },
            "additionalProperties": False,
        },
        "language": {
            "type": "string",
            "enum": [
                "en",       # English
                "hi",       # Hindi (Devanagari)
                "hi-Latn",  # Hinglish (Hindi in Latin script)
            ]
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
    },
    "additionalProperties": False,
}


def _build_system_prompt(valid_intents: list[str]) -> str:
    all_intents = sorted(set(valid_intents) | {"unsupported", "ambiguous"})
    intents_str = "\n".join(f"  - {label}" for label in all_intents)
    return f"""You are the Intent Classifier for a UPI (Unified Payments Interface) assistant.

Your ONLY job:
1. Identify the user's intent from the allowed list below.
2. Extract any raw text values the user mentioned (names, amounts, dates, refs, count) — as-is, verbatim.
3. Identify the script/language used (en, hi, hi-Latn).
4. Output a confidence score (0.0 to 1.0).

STRICT ANTI-HALLUCINATION RULES:
- You MUST choose exactly one intent_label from the allowed list below.
- SLOT GROUNDING: Extract ONLY words explicitly present in the user's message. NEVER assume, invent, or hallucinate any merchant name, bank name, amount, or ID.
- If a slot was not mentioned, leave extracted_slots empty or omit that slot.
- Extract slots as verbatim raw text (e.g. "Swiggy wala", NOT a database ID).
- P2P MONEY TRANSFERS / PAYMENTS (e.g. "Transfer 500 rupees to...", "Pay ₹200 to..."): This assistant does NOT handle direct money transfers. Output intent_label: "unsupported".
- OUT OF DOMAIN: If the user asks something completely outside of UPI/Banking (weather, chit-chat, recipes), output intent_label: "unsupported".
- Language:
  - "en" = English
  - "hi" = Hindi in Devanagari script (e.g. "लेन-देन दिखाओ")
  - "hi-Latn" = Hinglish (Hindi words in Latin alphabet, e.g. "mera account band kar do")

Allowed intent labels:
{intents_str}

EXAMPLES:
User: "Pause Swiggy autopay"
{{"intent_label": "pause_autopay", "extracted_slots": {{"merchant_name": "Swiggy"}}, "language": "en", "confidence": 0.96}}

User: "Show me last 3 transactions"
{{"intent_label": "show_transactions", "extracted_slots": {{"count": "3"}}, "language": "en", "confidence": 0.98}}

User: "mere pichle transactions dikhao"
{{"intent_label": "show_transactions", "extracted_slots": {{}}, "language": "hi-Latn", "confidence": 0.95}}

User: "Raise chargeback for Zomato payment"
{{"intent_label": "raise_chargeback", "extracted_slots": {{"payee": "Zomato"}}, "language": "en", "confidence": 0.95}}

User: "Raise a complaint about a UPI payment"
{{"intent_label": "raise_chargeback", "extracted_slots": {{}}, "language": "en", "confidence": 0.98}}

User: "Stop Netflix"
{{"intent_label": "ambiguous", "extracted_slots": {{"merchant_name": "Netflix"}}, "language": "en", "confidence": 0.92}}

User: "Transfer 500 rupees to Elon Musk"
{{"intent_label": "unsupported", "extracted_slots": {{}}, "language": "en", "confidence": 0.98}}

User: "What is a UPI PIN?"
{{"intent_label": "what_is", "extracted_slots": {{}}, "language": "en", "confidence": 0.98}}

Output valid JSON only, no markdown, no explanation.
"""


class IntentAgent:
    """Agent 1: LLM-based intent classification."""

    def __init__(self, llm: LLMAdapter | None = None):
        self._llm = llm

    @property
    def llm(self) -> LLMAdapter:
        if self._llm is None:
            self._llm = get_llm_adapter()
        return self._llm

    async def classify(self, text: str, language_hint: str | None = None) -> IntentOutput:
        """
        Classify intent from normalised text.
        Raises LLMError if the LLM is unreachable.
        """
        registry = get_registry()
        valid_intents = registry.all_intent_labels()

        system_prompt = _build_system_prompt(valid_intents)
        user_msg = text
        if language_hint:
            user_msg = f"[Language hint: {language_hint}]\n{text}"

        logger.info("IntentAgent: classifying '%s'", text[:80])
        try:
            raw = await self.llm.complete_json(system_prompt, user_msg, _INTENT_SCHEMA)
            logger.debug("IntentAgent raw output: %s", raw)

            # Validate the intent label
            allowed_set = set(valid_intents) | {"unsupported", "ambiguous"}
            intent_label = raw.get("intent_label")
            if not intent_label or intent_label not in allowed_set:
                logger.warning(
                    "IntentAgent produced missing or invalid label '%s'; evaluating fallback", intent_label
                )
                # If raw is empty or unhelpful, don't blindly default to faq if query has money/pause words
                text_lower = text.lower()
                if any(w in text_lower for w in ["transfer", "send money", "pay to", "bhejo"]):
                    intent_label = "unsupported"
                elif any(w in text_lower for w in ["pause", "hold"]):
                    intent_label = "pause_autopay"
                elif any(w in text_lower for w in ["stop", "cancel", "band"]):
                    intent_label = "ambiguous"
                else:
                    intent_label = "faq"
                raw["confidence"] = 0.6

            # Parse language
            lang_raw = raw.get("language", "en")
            try:
                language = Language(lang_raw)
            except ValueError:
                language = Language.EN

            # Build slots
            slots_raw = raw.get("extracted_slots", {})
            slots = ExtractedSlots(**{k: v for k, v in slots_raw.items() if v})

            return IntentOutput(
                intent_label=intent_label,
                extracted_slots=slots,
                language=language,
                confidence=float(raw.get("confidence", 0.5)),
                raw_text=text,
            )
        except LLMError as e:
            logger.error("IntentAgent: LLM unavailable (%s)", e)
            raise e
