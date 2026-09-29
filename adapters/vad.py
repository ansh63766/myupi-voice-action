"""
adapters/vad.py — VAD interface (V2: Capture/VAD Agent).
Push-to-talk mode only (no open mic in v1).
VAD only detects end-of-speech + trims silence.
Never decides content.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VADResult:
    speech_detected: bool
    speech_start_ms: Optional[float] = None
    speech_end_ms: Optional[float] = None
    trimmed_audio: Optional[bytes] = None  # silence-trimmed audio


class VADAdapter(ABC):
    """Abstract VAD interface. Swappable via config."""

    @abstractmethod
    def detect(self, audio_bytes: bytes, sample_rate: int = 16000) -> VADResult:
        """
        Detect speech in audio. Returns VADResult.
        ONLY detects end-of-speech and trims silence.
        Never decides content.
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...


class SileroVADAdapter(VADAdapter):
    """
    Silero-VAD adapter.
    Small, accurate, runs on CPU. BSD license.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        max_silence_ms: int = 700,
    ):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.max_silence_ms = max_silence_ms
        self._model = None
        self._utils = None

    def _load_model(self):
        if self._model is None:
            import torch
            logger.info("Loading Silero VAD model")
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            self._model = model
            self._utils = utils
        return self._model, self._utils

    def detect(self, audio_bytes: bytes, sample_rate: int = 16000) -> VADResult:
        """Detect speech boundaries and trim silence."""
        try:
            import torch
            import numpy as np

            model, utils = self._load_model()
            get_speech_timestamps, _, _, _, _ = utils

            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            audio_tensor = torch.from_numpy(audio_np)

            timestamps = get_speech_timestamps(
                audio_tensor,
                model,
                threshold=self.threshold,
                min_speech_duration_ms=self.min_speech_ms,
                max_silence_ms=self.max_silence_ms,
                sampling_rate=sample_rate,
            )

            if not timestamps:
                return VADResult(speech_detected=False)

            # Trim silence
            start_sample = timestamps[0]["start"]
            end_sample = timestamps[-1]["end"]
            trimmed_np = audio_np[start_sample:end_sample]
            trimmed_bytes = (trimmed_np * 32768).astype(np.int16).tobytes()

            return VADResult(
                speech_detected=True,
                speech_start_ms=start_sample / sample_rate * 1000,
                speech_end_ms=end_sample / sample_rate * 1000,
                trimmed_audio=trimmed_bytes,
            )
        except Exception as e:
            logger.error("VAD error: %s", e)
            return VADResult(speech_detected=False)

    def health_check(self) -> bool:
        try:
            import torch
            return True
        except ImportError:
            return False


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: VADAdapter | None = None


def get_vad_adapter() -> VADAdapter:
    global _adapter
    if _adapter is None:
        from config.settings import get_config
        cfg = get_config().vad
        if cfg.provider == "silero":
            _adapter = SileroVADAdapter(
                threshold=cfg.threshold,
                min_speech_ms=cfg.min_speech_duration_ms,
                max_silence_ms=cfg.max_silence_duration_ms,
            )
        else:
            raise ValueError(f"Unknown VAD provider: {cfg.provider}")
    return _adapter


def reset_vad_adapter() -> None:
    global _adapter
    _adapter = None
