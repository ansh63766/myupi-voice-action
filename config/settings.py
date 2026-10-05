"""
config/settings.py — Central configuration loader.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent.parent


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    base_url: str = "http://localhost:8000/v1"
    model: str = "/model"
    api_key: str = "sk-mock-key"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_s: int = 30


class ASRConfig(BaseModel):
    provider: str = "indic_transcribe"
    model: str = "bodhan-ai/indic-transcribe-flex"
    device: str = "cuda"


class FuzzyConfig(BaseModel):
    provider: str = "rapidfuzz"
    resolve_threshold: float = 85.0
    disambiguate_threshold: float = 60.0
    max_candidates: int = 5


class DBConfig(BaseModel):
    provider: str = "sqlite"
    url: str = "sqlite+aiosqlite:///./data/myupi.db"


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 1
    max_utterance_s: int = 15
    format: str = "wav"


class SessionConfig(BaseModel):
    max_voice_retries: int = 3
    slot_fill_max_attempts: int = 3


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    fuzzy: FuzzyConfig = Field(default_factory=FuzzyConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    if config_path is None:
        config_path = ROOT / "config" / "config.yaml"

    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    # Env-var overrides: MYUPI_LLM__MODEL=foo overrides llm.model
    for key, val in os.environ.items():
        if key.startswith("MYUPI_"):
            parts = key[6:].lower().split("__")
            d = raw
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = val

    return AppConfig.model_validate(raw)


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    global _config
    _config = None
