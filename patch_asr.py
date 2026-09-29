import sys

content = open('adapters/asr.py', 'r').read()
idx = content.find('    async def stream_captions(')

new_content = """    async def stream_captions(
        self,
        audio_stream,
        sample_rate: int = 16000,
    ):
        CHUNK_BYTES = sample_rate * 2 * 2
        buffer = bytearray()
        try:
            model = self._load_fast_model()
        except ImportError:
            return
        async for chunk in audio_stream:
            buffer.extend(chunk)
            if len(buffer) >= CHUNK_BYTES:
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    def _fast_transcribe():
                        import numpy as np
                        audio_input = np.frombuffer(bytes(buffer[-CHUNK_BYTES:]), dtype=np.int16).astype(np.float32) / 32768.0
                        segments_gen, _ = model.transcribe(audio_input, language="en")
                        return list(segments_gen)
                    segments = await loop.run_in_executor(None, _fast_transcribe)
                    for seg in segments:
                        if seg.text: yield seg.text
                except Exception:
                    pass

    async def health_check(self) -> bool:
        try:
            import faster_whisper
            return True
        except ImportError:
            return False

from typing import Optional

class IndicTranscribeFlexASR(ASRAdapter):
    \"\"\"
    Bodhan AI / AI4Bharat Indic-Transcribe-Flex ASR.
    1B Canary FastConformer architecture trained for 27 Indian languages.
    \"\"\"
    def __init__(
        self,
        model_name: str = "bodhan-ai/indic-transcribe-flex",
        device: str = "cuda",
        hf_token: Optional[str] = None,
    ):
        self.model_name = model_name
        self.device = device
        import os
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self._model = None
        self._infer_module = None
        self._model_dir = None
        self._failed = False

    def _load_model(self):
        if self._model is not None or self._infer_module is not None or self._failed:
            return
        import os
        from pathlib import Path
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Loading Indic-Transcribe-Flex model: %s", self.model_name)
        try:
            from huggingface_hub import snapshot_download
            self._model_dir = snapshot_download(self.model_name, token=self.hf_token)
            
            infer_py = Path(self._model_dir) / "inference.py"
            if infer_py.exists():
                import importlib.util
                import sys
                spec = importlib.util.spec_from_file_location("indic_infer", str(infer_py))
                self._infer_module = importlib.util.module_from_spec(spec)
                sys.modules["indic_infer"] = self._infer_module
                spec.loader.exec_module(self._infer_module)
                logger.info("Successfully loaded IndicTranscribe inference module.")
            else:
                import nemo.collections.asr as nemo_asr
                self._model = nemo_asr.models.ASRModel.from_pretrained(
                    self.model_name,
                    map_location=self.device,
                )
        except Exception as e:
            self._failed = True
            logger.error("Failed to load %s: %s. Will fallback to faster-whisper.", self.model_name, e)

    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list[str]] = None,
    ):
        import asyncio
        import os
        import tempfile
        import subprocess

        self._load_model()
        
        if self._failed:
            global _adapter
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("IndicTranscribe failed to load. Falling back to faster-whisper.")
            _adapter = FasterWhisperAdapter(auth_model_name="small", device=self.device)
            return await _adapter.transcribe_authoritative(audio_bytes, sample_rate, language, contextual_biasing)

        loop = asyncio.get_event_loop()

        def _run_inference():
            with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as raw_tf:
                raw_tf.write(audio_bytes)
                raw_path = raw_tf.name

            wav_path = raw_path + ".wav"
            try:
                cmd = ["ffmpeg", "-y", "-i", raw_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                text = ""
                if self._infer_module and hasattr(self._infer_module, "transcribe"):
                    res = self._infer_module.transcribe(wav_path, lang=language or "hi")
                    text = str(res[0]) if isinstance(res, (tuple, list)) else str(res)
                elif self._model:
                    res = self._model.transcribe([wav_path])
                    text = res[0] if res else ""
                else:
                    import sys
                    cmd = [sys.executable, os.path.join(self._model_dir, "inference.py"), wav_path]
                    if language: cmd.extend(["--lang", language])
                    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    text = proc.stdout.strip()
                return text
            finally:
                for p in (raw_path, wav_path):
                    if os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass

        transcript_text = await loop.run_in_executor(None, _run_inference)
        from adapters.asr import AuthoritativeTranscript
        return AuthoritativeTranscript(
            text=transcript_text.strip(),
            words=[],
            language=language or "hi",
            avg_confidence=0.95,
        )

    async def stream_captions(self, audio_stream, sample_rate=16000):
        yield ""

    async def health_check(self) -> bool:
        return not self._failed

# ── Factory ────────────────────────────────────────────────────────────────────
_adapter = None

def get_asr_adapter():
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
        elif cfg.provider in ("indic_transcribe", "bodhan", "sarvam"):
            model_name = cfg.model_authoritative if cfg.model_authoritative and cfg.model_authoritative not in ("small", "medium", "saaras:v4") else "bodhan-ai/indic-transcribe-flex"
            _adapter = IndicTranscribeFlexASR(model_name=model_name, device=cfg.device)
        else:
            _adapter = FasterWhisperAdapter(auth_model_name="small")
    return _adapter

def reset_asr_adapter() -> None:
    global _adapter
    _adapter = None
"""

if idx != -1:
    with open('adapters/asr.py', 'w', encoding='utf-8') as f:
        f.write(content[:idx] + new_content)
