"""
agents/support_agent.py — Agent 8: Support/FAQ Agent.
LLM-grounded on a small local KB (RAG or in-prompt).
Tier 0 only — no actions. Must say when KB doesn't cover it.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.llm import LLMAdapter, LLMError, get_llm_adapter
from db.models import FAQEntry

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a helpful UPI/BHIM support assistant.
Answer only using the provided knowledge base entries below.
If the answer is not in the knowledge base, say exactly:
"I don't have that information in my knowledge base. Please contact BHIM support or visit bhimupi.org.in."

Rules:
- Never make up facts about UPI, payments, or account details.
- Never give financial advice.
- Keep answers concise and helpful.
- If the user asks to DO something (pause mandate, raise chargeback, etc.), tell them to type their request instead.
- Respond in the same language as the user's question.
"""


async def _fetch_kb_entries(session: AsyncSession, query: str, limit: int = 5) -> list[FAQEntry]:
    """Simple keyword-based retrieval from FAQ KB."""
    # In production this would be vector search.
    # For prototype: load all entries and let LLM pick the relevant ones.
    stmt = select(FAQEntry).limit(50)  # small KB
    result = await session.execute(stmt)
    return result.scalars().all()


class SupportFAQAgent:
    """Agent 8: LLM grounded on local FAQ KB. No actions."""

    def __init__(self, llm: LLMAdapter | None = None):
        self._llm = llm

    @property
    def llm(self) -> LLMAdapter:
        if self._llm is None:
            self._llm = get_llm_adapter()
        return self._llm

    async def answer(
        self,
        query: str,
        session: AsyncSession,
        language: str = "en",
    ) -> str:
        """Answer a support/FAQ question grounded on the KB."""
        entries = await _fetch_kb_entries(session, query)

        if not entries:
            return (
                "I don't have that information in my knowledge base. "
                "Please contact BHIM support or visit bhimupi.org.in."
            )

        kb_text = "\n\n".join(
            f"Q: {e.question}\nA: {e.answer}" for e in entries
        )

        user_msg = f"Knowledge Base:\n{kb_text}\n\nUser question: {query}"

        logger.info("SupportFAQAgent: answering query '%s'", query[:80])
        try:
            response = await self.llm.complete_text(_SYSTEM_PROMPT, user_msg)
            return response
        except LLMError as e:
            logger.warning("SupportFAQAgent: LLM error (%s), using local KB match fallback", e)
            # Find best match by keyword overlap
            words = set(query.lower().split())
            best_entry = max(entries, key=lambda e: len(words.intersection(set(e.question.lower().split()))), default=None)
            if best_entry and len(words.intersection(set(best_entry.question.lower().split()))) > 0:
                return best_entry.answer
            return (
                "UPI (Unified Payments Interface) is a real-time payment system developed by NPCI "
                "that allows instant bank-to-bank transfers via mobile."
            )
