"""
config/settings.py — Central configuration loader.
All agent code imports from here. Never references model names directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).parent.parent


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    base_url: str = "http://localhost:8000/v1"
    model: str = "qwen2.5:3b-instruct"
    api_key: str = "sk-mock-key"
    temperature: float = 0.0
    max_tokens: int = 512
    timeout_s: int = 30


class ASRConfig(BaseModel):
    provider: str = "faster_whisper"
    model_authoritative: str = "medium"
    model_fast_lane: str = "tiny"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None
    beam_size: int = 5
    word_timestamps: bool = True
    models_dir: str = "models/whisper"


class TTSConfig(BaseModel):
    provider: str = "edge_tts"
    model: str = "en-IN-NeerjaNeural"
    voice: str = "en-IN-NeerjaNeural"
    models_dir: str = "models/tts"
    cache_dir: str = "models/tts/cache"
    stream: bool = True
    speak_sensitive_values: bool = False


class VADConfig(BaseModel):
    provider: str = "silero"
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    max_silence_duration_ms: int = 700


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
    format: str = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    fuzzy: FuzzyConfig = Field(default_factory=FuzzyConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML, then apply env-var overrides (MYUPI__ prefix)."""
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


# Singleton
_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Force reload (tests)."""
    global _config
    _config = None
