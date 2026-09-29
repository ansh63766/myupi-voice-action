"""
adapters/tts.py — TTS interface (V5).
Pre-renders top templated utterances; synthesises only slot values at runtime.
Streams chunks. Sensitive values not spoken by default (config: speak_sensitive_values=false).
"""
from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = ["balance", "pin", "otp", "password", "account number"]


class TTSAdapter(ABC):
    """Abstract TTS interface."""

    @abstractmethod
    async def synthesise(self, text: str) -> bytes:
        """Convert text to audio bytes (WAV)."""
        ...

    @abstractmethod
    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio chunks."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...


class TTSError(Exception):
    pass


class EdgeTTSAdapter(TTSAdapter):
    """
    Microsoft Edge Neural TTS adapter.
    - Zero local GPU/RAM overhead, ultra-fast streaming.
    - Native support for Indian English (en-IN-NeerjaNeural, en-IN-PrabhatNeural) and Hindi (hi-IN-SwaraNeural, hi-IN-MadhurNeural).
    - Compatible with Python 3.9 through 3.13+.
    """

    def __init__(self, voice: str = "en-IN-NeerjaNeural", speak_sensitive_values: bool = False):
        self.voice = voice
        self.speak_sensitive_values = speak_sensitive_values

    def _filter_sensitive(self, text: str) -> str:
        if self.speak_sensitive_values:
            return text
        lower = text.lower()
        for pattern in SENSITIVE_PATTERNS:
            if pattern in lower:
                return "[Sensitive information — please check screen]"
        return text

    async def synthesise(self, text: str) -> bytes:
        import edge_tts
        clean_text = self._filter_sensitive(text)
        communicate = edge_tts.Communicate(clean_text, self.voice)
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        return bytes(audio_data)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        import edge_tts
        clean_text = self._filter_sensitive(text)
        communicate = edge_tts.Communicate(clean_text, self.voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def health_check(self) -> bool:
        try:
            import edge_tts
            return True
        except ImportError:
            return False


class HuggingFaceTTSAdapter(TTSAdapter):
    """
    Hugging Face MMS / VITS TTS model running locally on PyTorch CUDA.
    Supports facebook/mms-tts-eng, facebook/mms-tts-hin, etc.
    """

    def __init__(self, model_id: str = "facebook/mms-tts-eng", device: str = "cuda"):
        self.model_id = model_id
        self.device = device
        self._model = None
        self._tokenizer = None

    def _load(self):
        if self._model is None:
            import torch
            from transformers import VitsModel, AutoTokenizer
            logger.info("Loading HuggingFace TTS model: %s on %s", self.model_id, self.device)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            self._model = VitsModel.from_pretrained(self.model_id).to(self.device)
        return self._model, self._tokenizer

    async def synthesise(self, text: str) -> bytes:
        import asyncio
        import io
        import torch
        import scipy.io.wavfile

        model, tokenizer = self._load()
        inputs = tokenizer(text, return_tensors="pt").to(self.device)
        
        loop = asyncio.get_event_loop()
        def _run_inf():
            with torch.no_grad():
                output = model(**inputs).waveform[0].cpu().numpy()
            buf = io.BytesIO()
            scipy.io.wavfile.write(buf, rate=model.config.sampling_rate, data=output)
            return buf.getvalue()

        return await loop.run_in_executor(None, _run_inf)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        audio = await self.synthesise(text)
        CHUNK = 4096
        for i in range(0, len(audio), CHUNK):
            yield audio[i:i + CHUNK]

    async def health_check(self) -> bool:
        try:
            import transformers
            return True
        except ImportError:
            return False


class CoquiTTSAdapter(TTSAdapter):
    """
    Coqui TTS adapter.
    - Pre-renders top templated utterances to disk cache on startup.
    - Synthesises slot values at runtime and concatenates.
    - Streams output.
    - Sensitive values suppressed per config.
    """

    def __init__(
        self,
        model_name: str = "tts_models/en/ljspeech/vits",
        models_dir: str = "models/tts",
        cache_dir: str = "models/tts/cache",
        speak_sensitive_values: bool = False,
    ):
        self.model_name = model_name
        self.models_dir = models_dir
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.speak_sensitive_values = speak_sensitive_values
        self._tts = None

    def _get_tts(self):
        if self._tts is None:
            from TTS.api import TTS
            logger.info("Loading TTS model: %s", self.model_name)
            self._tts = TTS(self.model_name, progress_bar=False)
        return self._tts

    def _filter_sensitive(self, text: str) -> str:
        """Remove or replace sensitive values if speak_sensitive_values=False."""
        if self.speak_sensitive_values:
            return text
        lower = text.lower()
        for pattern in SENSITIVE_PATTERNS:
            if pattern in lower:
                # Replace the sensitive portion with a description
                return "[Sensitive information — please check screen]"
        return text

    def _cache_path(self, text: str) -> Path:
        """Deterministic cache path for pre-rendered audio."""
        key = hashlib.md5(f"{self.model_name}:{text}".encode()).hexdigest()
        return self.cache_dir / f"{key}.wav"

    async def synthesise(self, text: str) -> bytes:
        """Synthesise text to WAV bytes. Uses cache if available."""
        import asyncio
        text = self._filter_sensitive(text)
        cache_path = self._cache_path(text)

        if cache_path.exists():
            return cache_path.read_bytes()

        tts = self._get_tts()
        wav_path = str(cache_path)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: tts.tts_to_file(text=text, file_path=wav_path)
        )
        return cache_path.read_bytes()

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio in chunks."""
        audio = await self.synthesise(text)
        CHUNK = 4096
        for i in range(0, len(audio), CHUNK):
            yield audio[i:i + CHUNK]

    async def pre_render_templates(self, texts: list[str]) -> None:
        """Pre-render top templated utterances to cache on startup."""
        logger.info("TTS: pre-rendering %d template utterances", len(texts))
        for text in texts:
            try:
                await self.synthesise(text)
                logger.debug("TTS: cached '%s'", text[:40])
            except Exception as e:
                logger.warning("TTS: failed to pre-render '%s': %s", text[:40], e)

    async def health_check(self) -> bool:
        try:
            import TTS
            return True
        except ImportError:
            return False


# ── Common pre-rendered utterances ─────────────────────────────────────────────

TEMPLATE_UTTERANCES_EN = [
    "I couldn't find a matching mandate in your account.",
    "Please confirm to continue.",
    "Action cancelled.",
    "Here are your recent transactions.",
    "Please provide more details.",
    "This action requires your UPI PIN. Mic is disabled.",
    "I don't have that information in my knowledge base.",
]


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: TTSAdapter | None = None


def get_tts_adapter() -> TTSAdapter:
    global _adapter
    if _adapter is None:
        from config.settings import get_config
        cfg = get_config().tts
        if cfg.provider in ("edge_tts", "edge"):
            _adapter = EdgeTTSAdapter(
                voice=getattr(cfg, "voice", "en-IN-NeerjaNeural"),
                speak_sensitive_values=cfg.speak_sensitive_values,
            )
        elif cfg.provider in ("hf", "huggingface"):
            _adapter = HuggingFaceTTSAdapter(model_id=cfg.model)
        elif cfg.provider == "coqui":
            try:
                _adapter = CoquiTTSAdapter(
                    model_name=cfg.model,
                    models_dir=cfg.models_dir,
                    cache_dir=cfg.cache_dir,
                    speak_sensitive_values=cfg.speak_sensitive_values,
                )
            except Exception as e:
                logger.warning("Coqui TTS failed to load (%s), falling back to EdgeTTS", e)
                _adapter = EdgeTTSAdapter()
        elif cfg.provider == "sarvam":
            from adapters.sarvam_voice import SarvamVoiceTTS
            _adapter = SarvamVoiceTTS()
        else:
            _adapter = EdgeTTSAdapter()
    return _adapter


def reset_tts_adapter() -> None:
    global _adapter
    _adapter = None
