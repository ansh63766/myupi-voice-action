"""
adapters/asr.py — ASR interface (V3 dual-lane ASR).
Fast lane: streaming partials → captions ONLY (never acted upon).
Authoritative pass: full utterance, per-word confidence, the only transcript entering business logic.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class WordResult:
    word: str
    start: float
    end: float
    probability: float  # per-word confidence 0–1


@dataclass
class AuthoritativeTranscript:
    """The ONLY transcript entering the business pipeline."""
    text: str
    words: list[WordResult]
    language: Optional[str]
    avg_confidence: float

    @property
    def low_confidence(self) -> bool:
        """True if average word confidence below threshold."""
        return self.avg_confidence < 0.7


class ASRAdapter(ABC):
    """Abstract ASR interface. Swappable via config."""

    @abstractmethod
    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list[str]] = None,
    ) -> AuthoritativeTranscript:
        """
        Authoritative pass: full utterance → text + per-word confidence.
        This is the ONLY transcript used for business logic.
        """
        ...

    @abstractmethod
    async def stream_captions(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[str]:
        """
        Fast lane: streaming partials → on-screen captions ONLY.
        Output is NEVER acted upon for business decisions.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...


class ASRError(Exception):
    pass


# ── Faster-Whisper adapter ────────────────────────────────────────────────────

class FasterWhisperAdapter(ASRAdapter):
    """
    Runs faster-whisper for authoritative transcription.
    fast_model (tiny) for captions (lower latency, captions only).
    auth_model (medium) for authoritative pass (higher accuracy).
    Both behind this interface — models selected via config, never hardcoded in agent code.
    """

    def __init__(
        self,
        auth_model_name: str = "medium",
        fast_model_name: str = "tiny",
        device: str = "cuda",
        compute_type: str = "float16",
        models_dir: str = "models/whisper",
        beam_size: int = 5,
    ):
        self.auth_model_name = auth_model_name
        self.fast_model_name = fast_model_name
        self.device = device
        self.compute_type = compute_type
        self.models_dir = models_dir
        self.beam_size = beam_size
        self._auth_model = None
        self._fast_model = None

    def _load_auth_model(self):
        if self._auth_model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading authoritative ASR model: %s on %s", self.auth_model_name, self.device)
            self._auth_model = WhisperModel(
                self.auth_model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.models_dir,
            )
        return self._auth_model

    def _load_fast_model(self):
        if self._fast_model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading fast lane ASR model: %s on cpu", self.fast_model_name)
            self._fast_model = WhisperModel(
                self.fast_model_name,
                device="cpu",  # fast lane always CPU (GPU for authoritative)
                compute_type="int8",
                download_root=self.models_dir,
            )
        return self._fast_model

    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list[str]] = None,
    ) -> AuthoritativeTranscript:
        """
        Full utterance transcription with per-word confidence.
        Contextual biasing uses per-user lexicon (payee names, VPAs, mandate nicknames).
        """
        import asyncio
        import io
        import numpy as np

        try:
            model = self._load_auth_model()
        except ImportError:
            raise ASRError("faster-whisper not installed")

        # Support container formats (WebM/Opus from browser MediaRecorder, WAV, OGG)
        if len(audio_bytes) > 4 and (audio_bytes[:4] in (b'\x1a\x45\xdf\xa3', b'RIFF', b'OggS') or b'webm' in audio_bytes[:40].lower()):
            audio_input = io.BytesIO(audio_bytes)
        else:
            try:
                audio_input = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception:
                audio_input = io.BytesIO(audio_bytes)

        # Run in thread pool (faster-whisper is synchronous)
        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            lambda: model.transcribe(
                audio_input,
                beam_size=self.beam_size,
                language=language,
                word_timestamps=True,
                initial_prompt=", ".join(contextual_biasing) if contextual_biasing else None,
                condition_on_previous_text=False,
            )
        )

        words = []
        full_text_parts = []
        for segment in segments:
            full_text_parts.append(segment.text)
            if segment.words:
                for w in segment.words:
                    words.append(WordResult(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        probability=w.probability,
                    ))

        full_text = "".join(full_text_parts).strip()
        avg_conf = sum(w.probability for w in words) / len(words) if words else 0.0

        logger.info(
            "ASR authoritative: '%s' (avg_confidence=%.2f, language=%s)",
            full_text[:80], avg_conf, info.language,
        )

        return AuthoritativeTranscript(
            text=full_text,
            words=words,
            language=info.language,
            avg_confidence=avg_conf,
        )

    async def stream_captions(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[str]:
        """
        Fast lane: streaming captions ONLY — for display, never business logic.
        Uses fast (tiny) model.
        """
        # In the prototype, we buffer enough audio before transcribing
        # Real implementation would use VAD segments
        buffer = bytearray()
        CHUNK_SECS = 2  # transcribe every 2 seconds of audio
        CHUNK_BYTES = sample_rate * 2 * CHUNK_SECS  # 16-bit mono

        async for chunk in audio_stream:
            buffer.extend(chunk)
            if len(buffer) >= CHUNK_BYTES:
                try:
                    result = await self.transcribe_authoritative(
                        bytes(buffer[-CHUNK_BYTES:]),
                        sample_rate=sample_rate,
                    )
                    if result.text:
                        yield result.text  # caption only
                except Exception:
                    pass  # caption failure is silent; never blocks business logic

    async def health_check(self) -> bool:
        try:
            import faster_whisper
            return True
        except ImportError:
            return False


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: ASRAdapter | None = None


def get_asr_adapter() -> ASRAdapter:
    global _adapter
    if _adapter is None:
        from config.settings import get_config
        cfg = get_config().asr
        if cfg.provider == "faster_whisper":
            _adapter = FasterWhisperAdapter(
                auth_model_name=cfg.model_authoritative,
                fast_model_name=cfg.model_fast_lane,
                device=cfg.device,
                compute_type=cfg.compute_type,
                models_dir=cfg.models_dir,
                beam_size=cfg.beam_size,
            )
        elif cfg.provider == "sarvam":
            from adapters.sarvam_voice import SarvamVoiceASR
            _adapter = SarvamVoiceASR()
        else:
            raise ValueError(f"Unknown ASR provider: {cfg.provider}")
    return _adapter


def reset_asr_adapter() -> None:
    global _adapter
    _adapter = None
