"""
agents/confirmation_agent.py — Agent 6: Confirmation Agent (deterministic).
Renders confirmation from versioned template + backend data keyed by resolved ID.
NEVER uses transcript or LLM output for confirmation text.
Logs template_id + version for every confirmation event.
"""
from __future__ import annotations

import logging
import uuid
from string import Template

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.types import (
    ConfirmationCard,
    EntityResolutionResult,
    Language,
    PolicyDecision,
)
from db.models import (
    BankAccount,
    ConfirmationTemplate,
    Mandate,
    Payee,
    Transaction,
    UPINumber,
    User,
)
from registry.loader import ActionEntry

logger = logging.getLogger(__name__)

# Fallback in-memory templates (used if DB template not found)
# These are versioned — production would always use DB with legal_reviewed=True
_INLINE_TEMPLATES: dict[str, dict[str, str]] = {
    "confirm_mandate_pause_v2": {
        "en": "Pause AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). Tap Confirm to pause.",
        "hi": "AutoPay रोकें: $merchant_name ($bank_name, ₹$amount/$frequency). पुष्टि के लिए Confirm दबाएं।",
        "hi-Latn": "AutoPay rokein: $merchant_name ($bank_name, ₹$amount/$frequency). Confirm dabayein.",
    },
    "confirm_mandate_resume_v1": {
        "en": "Resume AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). Tap Confirm to resume.",
        "hi": "AutoPay फिर शुरू करें: $merchant_name ($bank_name, ₹$amount/$frequency). Confirm दबाएं।",
        "hi-Latn": "AutoPay resume karein: $merchant_name ($bank_name, ₹$amount/$frequency). Confirm dabayein.",
    },
    "confirm_mandate_revoke_v1": {
        "en": "PERMANENTLY cancel AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). This cannot be undone.",
        "hi": "AutoPay PERMANENTLY cancel करें: $merchant_name ($bank_name, ₹$amount/$frequency). यह वापस नहीं होगा।",
        "hi-Latn": "AutoPay PERMANENTLY cancel karein: $merchant_name ($bank_name, ₹$amount/$frequency). Yeh wapas nahi hoga.",
    },
    "confirm_safety_switch_v1": {
        "en": "Toggle Safety Switch. This will $action all UPI transactions. Proceed in app.",
        "hi": "Safety Switch बदलें। इससे सभी UPI transactions $action हो जाएंगे। App में आगे बढ़ें।",
        "hi-Latn": "Safety Switch badlein. Isse sabhi UPI transactions $action ho jayenge. App mein aage barhe.",
    },
    "confirm_delink_v1": {
        "en": "Delink mobile number $number from UPI. This will remove $vpa. Proceed in app.",
        "hi": "Mobile number $number को UPI से delink करें। $vpa हट जाएगा। App में आगे बढ़ें।",
        "hi-Latn": "Mobile number $number ko UPI se delink karein. $vpa hat jayega. App mein aage barhe.",
    },
    "confirm_chargeback_v1": {
        "en": "Raise chargeback for: $payee_name — ₹$amount on $txn_date. Proceed in app.",
        "hi": "Chargeback raise करें: $payee_name — ₹$amount ($txn_date)। App में आगे बढ़ें।",
        "hi-Latn": "Chargeback raise karein: $payee_name — ₹$amount ($txn_date). App mein aage barhe.",
    },
    "confirm_replay_v1": {
        "en": "Repeat payment: $payee_name — ₹$amount. You will be asked for your UPI PIN. Mic will be disabled.",
        "hi": "Payment दोबारा करें: $payee_name — ₹$amount। UPI PIN डालना होगा। Mic बंद रहेगा।",
        "hi-Latn": "Payment dobara karein: $payee_name — ₹$amount. UPI PIN dalna hoga. Mic band rahega.",
    },
    "confirm_upi_lite_topup_v1": {
        "en": "Top up UPI Lite with ₹$amount. You will be asked for your UPI PIN.",
        "hi": "UPI Lite को ₹$amount से top up करें। UPI PIN डालना होगा।",
        "hi-Latn": "UPI Lite ko ₹$amount se top up karein. UPI PIN dalna hoga.",
    },
}


async def _fetch_template_body(
    session: AsyncSession,
    template_id_version: str,
    language: str,
) -> str | None:
    """Try to load template from DB first."""
    # template_id_version format: "confirm_mandate_pause_v2" (id already includes version)
    parts = template_id_version.rsplit("_v", 1)
    if len(parts) == 2:
        tid, ver = parts[0] + "_v" + parts[1].split("_")[0], parts[1]
        # simplify: use full string as template_id
    tid = template_id_version
    ver = "1"  # default; proper versioning would parse it

    stmt = (
        select(ConfirmationTemplate)
        .where(ConfirmationTemplate.template_id == tid)
        .where(ConfirmationTemplate.language == language)
    )
    result = await session.execute(stmt)
    tmpl = result.scalar_one_or_none()
    if tmpl:
        return tmpl.body
    return None


async def _get_entity_data(
    session: AsyncSession,
    action_entry: ActionEntry,
    entity_result: EntityResolutionResult,
    user_id: str,
) -> dict:
    """
    Fetch real entity data from DB using resolved IDs.
    Returns substitution dict for template rendering.
    Data comes from DB keyed by ID — never from transcript.
    """
    data: dict[str, str] = {}

    for slot_name, resolved in entity_result.resolved.items():
        entity_id = resolved.resolved_id

        if action_entry.disambiguation_source in ("user_active_mandates", "user_paused_mandates", "user_all_mandates"):
            stmt = select(Mandate).where(Mandate.id == entity_id)
            r = await session.execute(stmt)
            row = r.scalar_one_or_none()
            if row:
                data["merchant_name"] = row.merchant_name
                data["bank_name"] = row.bank_name
                data["amount"] = str(row.amount)
                data["frequency"] = row.frequency.lower()
                data["status"] = row.status
                data["merchant_vpa"] = row.merchant_vpa

        elif action_entry.disambiguation_source in ("user_transactions", "user_eligible_transactions"):
            stmt = select(Transaction).where(Transaction.id == entity_id)
            r = await session.execute(stmt)
            row = r.scalar_one_or_none()
            if row:
                data["payee_name"] = row.payee_name
                data["amount"] = str(row.amount)
                data["txn_date"] = row.created_at.strftime("%d %b %Y")
                data["txn_ref"] = row.txn_ref
                data["status"] = row.status

        elif action_entry.disambiguation_source == "user_upi_numbers":
            stmt = select(UPINumber).where(UPINumber.id == entity_id)
            r = await session.execute(stmt)
            row = r.scalar_one_or_none()
            if row:
                data["number"] = row.number
                data["vpa"] = row.vpa

        elif action_entry.disambiguation_source == "user_saved_payees":
            stmt = select(Payee).where(Payee.id == entity_id)
            r = await session.execute(stmt)
            row = r.scalar_one_or_none()
            if row:
                data["payee_name"] = row.display_name
                data["vpa"] = row.vpa

    # Safety switch defaults
    if action_entry.action_id == "safety_switch":
        from db.models import SafetySwitch
        stmt = select(SafetySwitch).where(SafetySwitch.user_id == user_id)
        r = await session.execute(stmt)
        ss = r.scalar_one_or_none()
        if ss and ss.is_active:
            data.setdefault("action", "unblock")
        else:
            data.setdefault("action", "block")

    return data


class ConfirmationAgent:
    """
    Agent 6: Deterministic, versioned confirmation rendering.
    Template + DB data → card text. Never uses LLM or transcript.
    """

    async def render(
        self,
        action_entry: ActionEntry,
        entity_result: EntityResolutionResult,
        policy: PolicyDecision,
        language: Language,
        session: AsyncSession,
        user_id: str,
        channel: str = "text",
    ) -> ConfirmationCard:
        """
        Render a confirmation card.
        Raises ValueError if template not found.
        """
        template_id = action_entry.confirmation_template_id
        if not template_id:
            raise ValueError(f"Action {action_entry.action_id} has no confirmation template")

        lang_code = language.value
        # Fallback chain: hi-Latn → hi → en
        fallback_chain = [lang_code, "hi", "en"] if lang_code == "hi-Latn" else [lang_code, "en"]

        # Try DB template first, then inline
        body = None
        for lang_try in fallback_chain:
            body = await _fetch_template_body(session, template_id, lang_try)
            if body:
                break
        if not body:
            # Use inline fallback
            tmpl_variants = _INLINE_TEMPLATES.get(template_id, {})
            for lang_try in fallback_chain:
                body = tmpl_variants.get(lang_try)
                if body:
                    break

        if not body:
            raise ValueError(f"No template found for {template_id} in {lang_code}")

        # Fetch real data from DB using resolved IDs
        entity_data = await _get_entity_data(session, action_entry, entity_result, user_id=user_id)

        # Render template (safe substitution — missing keys are left as-is)
        try:
            card_text = Template(body).safe_substitute(entity_data)
        except Exception as e:
            raise ValueError(f"Template render error: {e}")

        # Build resolved_entity_ids map for audit chain
        resolved_ids = {k: v.resolved_id for k, v in entity_result.resolved.items()}

        event_id = str(uuid.uuid4())

        # Spoken text — only if voice active; sensitive values not spoken by default
        spoken = None
        if channel == "voice":
            spoken = card_text  # TTS agent will further filter sensitive values

        version = "1"
        if "_v" in template_id:
            version = template_id.split("_v")[-1]

        logger.info(
            "ConfirmationAgent: rendered template=%s version=%s lang=%s event_id=%s",
            template_id, version, lang_code, event_id,
        )

        return ConfirmationCard(
            action_id=action_entry.action_id,
            template_id=template_id,
            template_version=version,
            language=language,
            card_text=card_text,
            spoken_text=spoken,
            event_id=event_id,
            resolved_entity_ids=resolved_ids,
        )
