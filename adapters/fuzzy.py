"""
adapters/fuzzy.py — Fuzzy Matcher Adapter (V2).
Swappable via config. Falls back to naive matching if rapidfuzz is missing.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FuzzyCandidate:
    candidate_id: str
    score: float
    matched_text: str


class FuzzyMatcherAdapter(ABC):
    """Abstract interface for closed-set entity resolution."""

    @abstractmethod
    def rank(
        self,
        query: str,
        candidates: list[dict],
        text_key: str = "text",
        id_key: str = "id"
    ) -> list[FuzzyCandidate]:
        """Rank candidates against query."""
        ...


class RapidFuzzAdapter(FuzzyMatcherAdapter):
    """
    Uses rapidfuzz + indic-transliteration.
    Falls back to a naive matcher if rapidfuzz isn't installed.
    """

    def __init__(
        self,
        resolve_threshold: float = 85.0,
        disambiguate_threshold: float = 60.0,
        max_candidates: int = 3,
    ):
        self.resolve_threshold = resolve_threshold
        self.disambiguate_threshold = disambiguate_threshold
        self.max_candidates = max_candidates
        
        try:
            import rapidfuzz
            self._has_rapidfuzz = True
        except ImportError:
            self._has_rapidfuzz = False
            logger.warning("rapidfuzz not installed — falling back to naive substring matching.")

    def rank(
        self,
        query: str,
        candidates: list[dict],
        text_key: str = "text",
        id_key: str = "id"
    ) -> list[FuzzyCandidate]:
        if not query or not candidates:
            return []

        if self._has_rapidfuzz:
            return self._rank_rapidfuzz(query, candidates, text_key, id_key)
        else:
            return self._rank_naive(query, candidates, text_key, id_key)

    def _rank_rapidfuzz(
        self,
        query: str,
        candidates: list[dict],
        text_key: str,
        id_key: str,
    ) -> list[FuzzyCandidate]:
        from rapidfuzz import fuzz, process, utils

        # Expand query with transliterations if available
        queries = [query]
        try:
            from indic_transliteration import sanscript
            hi_query = sanscript.transliterate(query, sanscript.ITRANS, sanscript.DEVANAGARI)
            if hi_query != query:
                queries.append(hi_query)
        except ImportError:
            pass

        results = []
        for cand in candidates:
            cand_text = str(cand.get(text_key, ""))
            if not cand_text:
                continue

            best_score = 0.0
            for q in queries:
                score = fuzz.WRatio(
                    q, cand_text,
                    processor=utils.default_process
                )
                if score > best_score:
                    best_score = score

            if best_score >= self.disambiguate_threshold:
                results.append(FuzzyCandidate(
                    candidate_id=cand.get(id_key),
                    score=best_score,
                    matched_text=cand_text,
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.max_candidates]

    def _rank_naive(
        self,
        query: str,
        candidates: list[dict],
        text_key: str,
        id_key: str,
    ) -> list[FuzzyCandidate]:
        """Naive fallback when rapidfuzz is missing."""
        results = []
        query_lower = query.lower()
        for cand in candidates:
            cand_text = str(cand.get(text_key, ""))
            if not cand_text:
                continue
            
            cand_lower = cand_text.lower()
            if query_lower == cand_lower:
                score = 100.0
            elif query_lower in cand_lower or cand_lower in query_lower:
                score = 80.0
            else:
                # Poor man's word match
                q_words = set(query_lower.split())
                c_words = set(cand_lower.split())
                if q_words & c_words:
                    score = 65.0
                else:
                    score = 0.0
                    
            if score >= self.disambiguate_threshold:
                results.append(FuzzyCandidate(
                    candidate_id=cand.get(id_key),
                    score=score,
                    matched_text=cand_text,
                ))
                
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.max_candidates]


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: FuzzyMatcherAdapter | None = None

def get_fuzzy_adapter() -> FuzzyMatcherAdapter:
    global _adapter
    if _adapter is None:
        from config.settings import get_config
        cfg = get_config().fuzzy
        if cfg.provider == "rapidfuzz":
            _adapter = RapidFuzzAdapter(
                resolve_threshold=cfg.resolve_threshold,
                disambiguate_threshold=cfg.disambiguate_threshold,
                max_candidates=cfg.max_candidates,
            )
        else:
            raise ValueError(f"Unknown fuzzy provider: {cfg.provider}")
    return _adapter

def reset_fuzzy_adapter() -> None:
    global _adapter
    _adapter = None
