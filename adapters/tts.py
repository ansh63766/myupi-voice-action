"""
adapters/tts.py — TTS using ai4bharat/indic-parler-tts
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IndicParlerTTS:
    """
    TTS using ai4bharat/indic-parler-tts.
    Generates natural Indian English / Hindi speech.
    """

    def __init__(
        self,
        model_id: str = "ai4bharat/indic-parler-tts",
        device: str = "cuda",
    ):
        self.model_id = model_id
        self.device = device
        self._model = None
        self._tokenizer = None
        self._desc_tokenizer = None

    def _load_model(self):
        if self._model is not None:
            return
        logger.info("Loading TTS model: %s on %s", self.model_id, self.device)
        try:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer

            if self.device == "cuda" and not torch.cuda.is_available():
                self.device = "cpu"
                logger.warning("CUDA not available, using CPU for TTS.")

            self._model = ParlerTTSForConditionalGeneration.from_pretrained(
                self.model_id
            ).to(self.device)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            # Use description tokenizer if available separately
            try:
                self._desc_tokenizer = AutoTokenizer.from_pretrained(
                    self.model_id, subfolder="description_tokenizer"
                )
            except Exception:
                self._desc_tokenizer = self._tokenizer

            logger.info("TTS model loaded successfully.")
        except Exception as e:
            logger.error("Failed to load TTS model: %s", e)
            raise RuntimeError(f"Failed to load TTS model: {e}")

    async def synthesise(self, text: str) -> bytes:
        import soundfile as sf

        loop = asyncio.get_event_loop()

        def _generate():
            self._load_model()
            import torch

            description = (
                "A female speaker delivers a clear, natural, and expressive "
                "speech in Indian English at a moderate pace."
            )

            desc_ids = self._desc_tokenizer(
                description, return_tensors="pt"
            ).input_ids.to(self.device)

            prompt_ids = self._tokenizer(
                text, return_tensors="pt"
            ).input_ids.to(self.device)

            with torch.no_grad():
                generation = self._model.generate(
                    input_ids=desc_ids,
                    prompt_input_ids=prompt_ids,
                )

            audio_arr = generation.cpu().numpy().squeeze()
            buf = io.BytesIO()
            sf.write(buf, audio_arr, self._model.config.sampling_rate, format="WAV")
            return buf.getvalue()

        return await loop.run_in_executor(None, _generate)

    async def health_check(self) -> bool:
        try:
            from parler_tts import ParlerTTSForConditionalGeneration
            return True
        except ImportError:
            return False


# ── Factory ────────────────────────────────────────────────────────────────────

_adapter: Optional[IndicParlerTTS] = None


def get_tts_adapter() -> IndicParlerTTS:
    global _adapter
    if _adapter is None:
        _adapter = IndicParlerTTS(
            model_id="ai4bharat/indic-parler-tts",
            device="cuda",
        )
    return _adapter


def reset_tts_adapter() -> None:
    global _adapter
    _adapter = None
