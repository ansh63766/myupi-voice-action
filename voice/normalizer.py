"""
voice/normalizer.py — V4: Normalizer Agent (ITN).
Inverse text normalisation: extracts amounts, dates, phone numbers, txn refs.
Rule/FST-style, deterministic — rules can't silently alter a digit, generative models can.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Amount patterns
_AMOUNT_PATTERNS = [
    (r"₹\s*(\d+(?:,\d+)*(?:\.\d+)?)", "amount"),     # ₹1,299.50
    (r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:rupees?|rs\.?|INR)", "amount"),
    (r"(\d+)\s*(?:k|K)\s*rupees?", "amount_k"),        # 5k rupees → 5000
]

# Phone number patterns
_PHONE_PATTERNS = [
    r"\+91\s*(\d{10})",
    r"(?:91)?(\d{10})",
]

# Transaction ref patterns (16-char alphanumeric)
_TXN_REF_PATTERN = r"\b([A-Z0-9]{16})\b"

# Relative date patterns
_DATE_PATTERNS = [
    (r"(?:aaj|today)", "today"),
    (r"(?:kal|yesterday)", "yesterday"),
    (r"last\s+(\d+)\s+days?", "last_n_days"),
    (r"is\s+(?:hafte|week)", "this_week"),
    (r"is\s+(?:mahine|month)", "this_month"),
]

# Hindi/Hinglish amount words
_HINDI_AMOUNTS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5,
    "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "bees": 20, "pachas": 50, "sau": 100, "hazaar": 1000,
    "lakh": 100000, "crore": 10000000,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "hundred": 100, "thousand": 1000, "lakh": 100000,
}


@dataclass
class NormalizedSlots:
    amount: Optional[str] = None          # normalized numeric string "1299.50"
    phone_number: Optional[str] = None    # "+91XXXXXXXXXX"
    txn_ref: Optional[str] = None         # 16-char ref
    date_range: Optional[str] = None      # "today" | "last_7_days" etc.
    raw_text: str = ""                    # residual text after extraction
    extracted: dict = field(default_factory=dict)  # all extracted values


class VoiceNormalizer:
    """
    V4: Inverse Text Normalisation (ITN) + entity extraction from ASR output.
    Deterministic rule-based — no generative model can silently alter a digit.
    """

    def normalize(self, asr_text: str) -> NormalizedSlots:
        """
        Extract structured slots from ASR text using deterministic rules.
        Returns NormalizedSlots with extracted values and residual text.
        """
        text = asr_text.strip()
        result = NormalizedSlots(raw_text=text)
        residual = text

        # Extract amount
        for pattern, kind in _AMOUNT_PATTERNS:
            m = re.search(pattern, residual, re.IGNORECASE)
            if m:
                amount_str = m.group(1).replace(",", "")
                if kind == "amount_k":
                    amount_str = str(float(amount_str) * 1000)
                result.amount = amount_str
                result.extracted["amount"] = amount_str
                residual = residual[:m.start()] + " " + residual[m.end():]
                break

        # Extract phone number
        for pattern in _PHONE_PATTERNS:
            m = re.search(pattern, residual)
            if m:
                number = m.group(1)
                result.phone_number = f"+91{number}"
                result.extracted["number"] = result.phone_number
                residual = residual[:m.start()] + " " + residual[m.end():]
                break

        # Extract transaction reference (16-char alphanumeric)
        m = re.search(_TXN_REF_PATTERN, residual)
        if m:
            result.txn_ref = m.group(1)
            result.extracted["txn_ref"] = result.txn_ref
            residual = residual[:m.start()] + " " + residual[m.end():]

        # Extract date
        for pattern, kind in _DATE_PATTERNS:
            m = re.search(pattern, residual, re.IGNORECASE)
            if m:
                if kind == "last_n_days":
                    n = m.group(1)
                    result.date_range = f"last_{n}_days"
                else:
                    result.date_range = kind
                result.extracted["date_range"] = result.date_range
                residual = residual[:m.start()] + " " + residual[m.end():]
                break

        result.raw_text = re.sub(r"\s+", " ", residual).strip()

        logger.debug(
            "Normalizer: '%s' → amount=%s, phone=%s, txn_ref=%s, date=%s, residual='%s'",
            asr_text[:60], result.amount, result.phone_number,
            result.txn_ref, result.date_range, result.raw_text[:60],
        )

        return result
