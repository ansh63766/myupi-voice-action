"""
adapters/asr.py — ASR using bodhan-ai/indic-transcribe-flex
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WordResult:
    word: str
    start: float
    end: float
    probability: float


@dataclass
class AuthoritativeTranscript:
    text: str
    words: list
    language: Optional[str]
    avg_confidence: float


class ASRError(Exception):
    pass


class IndicTranscribeFlexASR:
    """
    ASR using bodhan-ai/indic-transcribe-flex via NeMo.
    Supports Hindi, English, Hinglish and 27 Indian languages.
    """

    def __init__(
        self,
        model_name: str = "bodhan-ai/indic-transcribe-flex",
        device: str = "cuda",
    ):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        logger.info("Loading ASR model: %s", self.model_name)
        try:
            import nemo.collections.asr as nemo_asr
            self._model = nemo_asr.models.ASRModel.from_pretrained(self.model_name)
            if self.device == "cuda":
                import torch
                if torch.cuda.is_available():
                    self._model = self._model.cuda()
            logger.info("ASR model loaded successfully.")
        except Exception as e:
            logger.error("Failed to load ASR model: %s", e)
            raise ASRError(f"Failed to load ASR model: {e}")
        return self._model

    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list] = None,
    ) -> AuthoritativeTranscript:
        loop = asyncio.get_event_loop()

        def _run():
            model = self._load_model()

            # Write raw input to temp file and convert to 16kHz WAV via ffmpeg
            with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as tf:
                tf.write(audio_bytes)
                raw_path = tf.name

            wav_path = raw_path + ".wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", raw_path,
                     "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
                results = model.transcribe([wav_path])
                text = results[0] if results else ""
                return str(text).strip()
            finally:
                for p in (raw_path, wav_path):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        try:
            text = await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.error("ASR transcription error: %s", e)
            raise ASRError(str(e))

        logger.info("ASR transcript: '%s'", text[:80])
        return AuthoritativeTranscript(
            text=text,
            words=[],
            language=language or "hi",
            avg_confidence=0.95,
        )

    async def health_check(self) -> bool:
        try:
            import nemo
            return True
        except ImportError:
            return False


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: Optional[IndicTranscribeFlexASR] = None


def get_asr_adapter() -> IndicTranscribeFlexASR:
    global _adapter
    if _adapter is None:
        _adapter = IndicTranscribeFlexASR(
            model_name="bodhan-ai/indic-transcribe-flex",
            device="cuda",
        )
    return _adapter


def reset_asr_adapter() -> None:
    global _adapter
    _adapter = None
