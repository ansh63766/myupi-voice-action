"""
adapters/llm.py — LLM interface.
Agent logic only calls get_llm_adapter() and uses the abstract interface.
Never imports a specific LLM library directly.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class LLMAdapter(ABC):
    """Abstract interface all LLM providers must implement."""

    @abstractmethod
    async def complete_json(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Generate a JSON response conforming to `schema`.
        Raises LLMError on failure.
        """
        ...

    @abstractmethod
    async def complete_text(self, system_prompt: str, user_message: str) -> str:
        """Generate a free-text response (for FAQ/support agent)."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Returns True if the LLM is reachable and responding."""
        ...


class LLMError(Exception):
    """Raised when LLM call fails."""
    pass


# ── OpenAI-compatible adapter ──────────────────────────────────────────────────

class OpenAICompatibleAdapter(LLMAdapter):
    """Works with vLLM, OpenAI, or any OpenAI-API-compatible backend."""

    def __init__(self, base_url: str, model: str, api_key: str,
                 temperature: float = 0.0, max_tokens: int = 512, timeout_s: int = 30):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout_s,
            )
        return self._client

    async def complete_json(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._get_client()
        # Use JSON mode if available; fallback to schema in system prompt
        schema_str = json.dumps(schema, indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON only, conforming to this schema:\n{schema_str}\n"
            f"No markdown, no explanation, JSON only."
        )
        
        sep = "=" * 60
        sub_sep = "-" * 60
        logger.info(
            f"\n\033[94m{sep}\n[LLM REQUEST - JSON]\n{sep}\033[0m\n"
            f"\033[33mSYSTEM PROMPT:\n{full_system[:400]}...\033[0m\n{sub_sep}\n"
            f"\033[36mUSER MESSAGE:\n{user_message[:200]}...\033[0m\n\033[94m{sep}\033[0m"
        )
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_message},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            logger.info(
                f"\n\033[92m{sep}\n[LLM RESPONSE - JSON]\n{sep}\033[0m\n"
                f"\033[32m{content}\033[0m\n\033[92m{sep}\033[0m"
            )
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"\n\033[91m{sep}\n[LLM PARSE ERROR]\n{e}\n{sep}\033[0m")
            raise LLMError(f"LLM returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"\n\033[91m{sep}\n[LLM CALL ERROR]\n{e}\n{sep}\033[0m")
            raise LLMError(f"LLM call failed: {e}")

    async def complete_text(self, system_prompt: str, user_message: str) -> str:
        client = self._get_client()
        sep = "=" * 60
        sub_sep = "-" * 60
        logger.info(
            f"\n\033[94m{sep}\n[LLM REQUEST - TEXT]\n{sep}\033[0m\n"
            f"\033[33mSYSTEM PROMPT:\n{system_prompt}\033[0m\n{sub_sep}\n"
            f"\033[36mUSER MESSAGE:\n{user_message}\033[0m\n\033[94m{sep}\033[0m"
        )
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=self.max_tokens,
            )
            content = response.choices[0].message.content or ""
            logger.info(
                f"\n\033[92m{sep}\n[LLM RESPONSE - TEXT]\n{sep}\033[0m\n"
                f"\033[32m{content}\033[0m\n\033[92m{sep}\033[0m"
            )
            return content
        except Exception as e:
            logger.error("\n" + "\033[91m="*60 + f"\n[LLM CALL ERROR]\n{e}\n" + "="*60 + "\033[0m")
            raise LLMError(f"LLM call failed: {e}")

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return bool(response.choices)
        except Exception:
            return False


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: LLMAdapter | None = None


def get_llm_adapter() -> LLMAdapter:
    global _adapter
    if _adapter is None:
        from config.settings import get_config
        cfg = get_config().llm
        if cfg.provider in ("openai", "openai_compatible", "vllm"):
            api_key = cfg.api_key
            if api_key and api_key.startswith("${") and api_key.endswith("}"):
                import os
                env_var = api_key[2:-1]
                api_key = os.environ.get(env_var, api_key)
            elif api_key and api_key.startswith("$"):
                import os
                env_var = api_key[1:]
                api_key = os.environ.get(env_var, api_key)

            _adapter = OpenAICompatibleAdapter(
                base_url=cfg.base_url,
                model=cfg.model,
                api_key=api_key,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                timeout_s=cfg.timeout_s,
            )
        else:
            raise ValueError(f"Unknown LLM provider: {cfg.provider}")
    return _adapter


def reset_llm_adapter() -> None:
    global _adapter
    _adapter = None
