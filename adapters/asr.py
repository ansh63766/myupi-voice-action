"""
adapters/asr.py — ASR using bodhan-ai/indic-transcribe-flex
Uses the model's own custom loader (load_nemo.py) shipped in the repo.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
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
    words: list = field(default_factory=list)
    language: Optional[str] = None
    avg_confidence: float = 0.95


class ASRError(Exception):
    pass


class IndicTranscribeFlexASR:
    """
    ASR using bodhan-ai/indic-transcribe-flex.
    Uses the model's own custom loader (load_nemo.py) shipped inside the repo.
    """

    def __init__(
        self,
        model_name: str = "bodhan-ai/indic-transcribe-flex",
        device: str = "cuda",
        source_lang: str = "en",
        target_lang: str = "en",
    ):
        self.model_name = model_name
        self.device = device
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._model = None
        self._model_dir = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        # Check pre-downloaded local directories first (avoids repeated HF network calls)
        candidate_paths = [
            os.environ.get("ASR_MODEL_DIR"),
            "/content/asr_model",
            os.path.join(os.getcwd(), "models", "indic-transcribe-flex"),
            self.model_name if os.path.isdir(self.model_name) else None,
        ]

        found_path = None
        for path in candidate_paths:
            if path and os.path.isdir(path):
                nemo_file = os.path.join(path, "nemo", "indic_transcribe_flex.nemo")
                loader_file = os.path.join(path, "nemo", "load_nemo.py")
                if os.path.exists(nemo_file) and os.path.exists(loader_file):
                    found_path = path
                    break

        if found_path:
            logger.info("Using local pre-downloaded ASR model at: %s", found_path)
            self._model_dir = found_path
        else:
            logger.info("Local model not found. Downloading ASR model: %s", self.model_name)
            from huggingface_hub import snapshot_download
            hf_token = os.environ.get("HF_TOKEN")
            target_dir = os.environ.get("ASR_MODEL_DIR") or "/content/asr_model"
            try:
                self._model_dir = snapshot_download(
                    repo_id=self.model_name,
                    local_dir=target_dir,
                    token=hf_token,
                )
            except Exception as dl_err:
                logger.warning("Download to %s failed (%s), using default HF cache...", target_dir, dl_err)
                self._model_dir = snapshot_download(repo_id=self.model_name, token=hf_token)

            logger.info("ASR Model files ready at: %s", self._model_dir)

        # Use the model's own loader shipped inside the repo
        nemo_loader_path = os.path.join(self._model_dir, "nemo")
        if nemo_loader_path not in sys.path:
            sys.path.insert(0, nemo_loader_path)

        from load_nemo import load_nemo_model
        self._model = load_nemo_model(self._model_dir)
        logger.info("ASR model loaded successfully.")
        return self._model

    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list] = None,
    ) -> AuthoritativeTranscript:

        import os
        import httpx
        
        server_url = os.environ.get("ASR_SERVER_URL")
        if server_url:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    files = {'audio_file': ('audio.webm', audio_bytes, 'audio/webm')}
                    data = {'language': language or self.source_lang}
                    resp = await client.post(f"{server_url}/transcribe", data=data, files=files)
                    resp.raise_for_status()
                    text = resp.json().get("text", "")
                    logger.info("ASR server transcript: '%s'", text[:80])
                    return AuthoritativeTranscript(
                        text=text,
                        language=language or self.source_lang,
                        avg_confidence=0.95,
                    )
            except Exception as e:
                logger.error("ASR server error: %s", e)
                raise ASRError(str(e))
                
        loop = asyncio.get_event_loop()

        def _run():
            model = self._load_model()

            # Write raw audio to temp file and convert to 16kHz mono WAV
            with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as tf:
                tf.write(audio_bytes)
                raw_path = tf.name

            wav_path = raw_path + ".wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", raw_path,
                     "-ar", "16000", "-ac", "1",
                     "-c:a", "pcm_s16le", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )

                src_lang = language or self.source_lang
                tgt_lang = language or self.target_lang

                results = model.transcribe(
                    [wav_path],
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    pnc="yes",
                )

                if isinstance(results, list) and results:
                    r = results[0]
                    text = r.text if hasattr(r, "text") else str(r)
                else:
                    text = str(results)

                return text.strip()
            finally:
                for p in (raw_path, wav_path):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        try:
            text = await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.error("ASR error: %s", e)
            raise ASRError(str(e))

        logger.info("ASR transcript: '%s'", text[:80])
        return AuthoritativeTranscript(
            text=text,
            language=language or self.source_lang,
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
            source_lang="en",
            target_lang="en",
        )
    return _adapter


def reset_asr_adapter() -> None:
    global _adapter
    _adapter = None
