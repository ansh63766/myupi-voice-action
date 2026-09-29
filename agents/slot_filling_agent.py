"""
agents/slot_filling_agent.py — Agent 3: Slot Filling / Dialogue Manager (deterministic).
Diffs extracted vs required slots. Missing → templated clarification.
Max 3 attempts per slot, then deep link fallback.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.types import (
    ExtractedSlots,
    SlotFillRequest,
    SlotFillResult,
    Language,
)
from registry.loader import ActionEntry

logger = logging.getLogger(__name__)

# Multilingual slot clarification prompts (templated, not LLM-generated)
_SLOT_PROMPTS: dict[str, dict[str, str]] = {
    "mandate_id": {
        "en": "Which AutoPay/mandate would you like to manage? (e.g., 'Swiggy', 'Netflix', 'Jio')",
        "hi": "आप कौन सा AutoPay/mandate manage करना चाहते हैं? (जैसे 'Swiggy', 'Netflix', 'Jio')",
        "hi-Latn": "Aap kaun sa AutoPay manage karna chahte hain? (e.g., 'Swiggy', 'Netflix', 'Jio')",
    },
    "txn_id": {
        "en": "Which transaction are you referring to? Please provide the merchant name, amount, or transaction reference.",
        "hi": "आप किस transaction के बारे में बात कर रहे हैं? merchant का नाम, amount, या reference दें।",
        "hi-Latn": "Aap kis transaction ke baare mein baat kar rahe hain? Merchant ka naam, amount ya reference dijiye.",
    },
    "payee": {
        "en": "Who would you like to send money to or check? (name or UPI ID)",
        "hi": "आप किसके बारे में जानकारी चाहते हैं? (नाम या UPI ID)",
        "hi-Latn": "Aap kiske baare mein jaankari chahte hain? (naam ya UPI ID)",
    },
    "number": {
        "en": "Which mobile number would you like to delink?",
        "hi": "आप कौन सा mobile number delink करना चाहते हैं?",
        "hi-Latn": "Aap kaun sa mobile number delink karna chahte hain?",
    },
    "amount": {
        "en": "What amount are you referring to?",
        "hi": "आप किस amount के बारे में बात कर रहे हैं?",
        "hi-Latn": "Aap kitne amount ki baat kar rahe hain?",
    },
    "bank_name": {
        "en": "Which bank account should I use?",
        "hi": "कौन सा bank account use करूँ?",
        "hi-Latn": "Kaun sa bank account use karoon?",
    },
}

_DEFAULT_PROMPT = {
    "en": "Could you please provide more details about {slot}?",
    "hi": "कृपया {slot} के बारे में अधिक जानकारी दें।",
    "hi-Latn": "Please {slot} ke baare mein aur batayein.",
}

_FALLBACK_LINK_MESSAGES: dict[str, dict[str, str]] = {
    "en": "I wasn't able to get that information after {attempts} attempts. Here's a direct link to help you: {link}",
    "hi": "{attempts} प्रयासों के बाद मैं यह जानकारी नहीं ले पाया। यहाँ direct link है: {link}",
    "hi-Latn": "{attempts} attempts ke baad main yeh information nahi le paya. Yeh raha direct link: {link}",
}


def _get_prompt(slot: str, language: str) -> str:
    lang = language if language in ("en", "hi", "hi-Latn") else "en"
    prompts = _SLOT_PROMPTS.get(slot, _DEFAULT_PROMPT)
    return prompts.get(lang, prompts.get("en", f"Please provide {slot}."))


class SlotFillingAgent:
    """
    Agent 3: Deterministic slot-filling dialogue manager.
    Diffs required vs filled slots. Prompts for missing ones.
    Max 3 attempts per slot, then emits deep link fallback.
    """

    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts

    def check(
        self,
        action_entry: ActionEntry,
        extracted_slots: ExtractedSlots,
        slot_attempts: dict[str, int],
        language: str = "en",
    ) -> SlotFillResult:
        """
        Returns SlotFillResult:
          - slots_complete=True if all required slots are filled.
          - slots_complete=False + pending_request if a slot is needed.
          - slots_complete=False + fallback_deep_link if max attempts exhausted.
        """
        filled = extracted_slots.to_dict()
        required = action_entry.required_slots

        # ── Slot promotion: if merchant_name or payee is present, use it to
        # satisfy mandate_id / txn_id so entity resolution can fuzzy-match it
        # rather than blindly asking the user to repeat themselves.
        if "mandate_id" in required and not filled.get("mandate_id"):
            proxy = filled.get("merchant_name") or filled.get("payee") or "__ALL__"
            current = extracted_slots.model_dump()
            current["mandate_id"] = proxy
            extracted_slots = ExtractedSlots(**current)
            filled = extracted_slots.to_dict()

        if "txn_id" in required and not filled.get("txn_id"):
            proxy = filled.get("payee") or filled.get("merchant_name") or "__ALL__"
            current = extracted_slots.model_dump()
            current["txn_id"] = proxy
            extracted_slots = ExtractedSlots(**current)
            filled = extracted_slots.to_dict()

        for slot in required:
            if not filled.get(slot):
                attempts = slot_attempts.get(slot, 0) + 1

                if attempts > self.max_attempts:
                    # Exceeded max attempts — emit fallback deep link
                    # Build a generic deep link to the relevant tab
                    fallback_link = self._fallback_link(action_entry, slot)
                    lang = language if language in _FALLBACK_LINK_MESSAGES else "en"
                    msg = _FALLBACK_LINK_MESSAGES[lang].format(
                        attempts=self.max_attempts,
                        link=fallback_link,
                    )
                    logger.warning(
                        "SlotFilling: max attempts (%d) exceeded for slot '%s' on action '%s'",
                        self.max_attempts, slot, action_entry.action_id,
                    )
                    return SlotFillResult(
                        slots_complete=False,
                        filled_slots=extracted_slots,
                        pending_request=None,
                        fallback_deep_link=fallback_link,
                    )

                prompt = _get_prompt(slot, language)
                logger.info(
                    "SlotFilling: missing slot '%s' (attempt %d/%d)",
                    slot, attempts, self.max_attempts,
                )
                return SlotFillResult(
                    slots_complete=False,
                    filled_slots=extracted_slots,
                    pending_request=SlotFillRequest(
                        missing_slot=slot,
                        prompt_text=prompt,
                        attempt_number=attempts,
                    ),
                )

        logger.info("SlotFilling: all required slots filled for '%s'", action_entry.action_id)
        return SlotFillResult(
            slots_complete=True,
            filled_slots=extracted_slots,
        )

    def _fallback_link(self, action_entry: ActionEntry, missing_slot: str) -> str:
        """Generate a fallback app deep link to the relevant section."""
        # Map action → relevant tab deep link
        fallback_map = {
            "mandate_pause": "bhim://myupi/mandates",
            "mandate_resume": "bhim://myupi/mandates",
            "mandate_revoke": "bhim://myupi/mandates",
            "txn_view": "bhim://myupi/transactions",
            "chargeback": "bhim://myupi/transactions",
            "transaction_replay": "bhim://myupi/transactions",
            "upi_number_delink": "bhim://myupi/settings",
            "safety_switch": "bhim://myupi/settings",
            "payee_context": "bhim://myupi/payees",
        }
        return fallback_map.get(action_entry.action_id, "bhim://myupi/home")

    def merge_new_input(
        self,
        existing_slots: ExtractedSlots,
        new_text: str,
        missing_slot: str,
    ) -> ExtractedSlots:
        """
        When user responds to a slot prompt, set the missing slot to the new raw text.
        Deterministic — does not call LLM.
        """
        update = {missing_slot: new_text.strip()}
        current = existing_slots.model_dump()
        current.update(update)
        return ExtractedSlots(**current)
