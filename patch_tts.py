import sys

content = open('adapters/tts.py', 'r', encoding='utf-8').read()
idx = content.find('# ── Factory')

new_content = """class IndicParlerTTSAdapter(TTSAdapter):
    \"\"\"
    AI4Bharat Indic-Parler-TTS model.
    Generates natural, expressive speech from text using descriptive prompts.
    Supports English and 20+ Indian languages.
    \"\"\"
    def __init__(self, model_id: str = "ai4bharat/indic-parler-tts", device: str = "cuda"):
        self.model_id = model_id
        self.device = device
        self._model = None
        self._tokenizer = None
        self._failed = False

    def _load_model(self):
        if self._model is not None or self._failed:
            return
        logger.info("Loading Indic-Parler-TTS model: %s on %s", self.model_id, self.device)
        try:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer
            
            # fallback to cpu if cuda not available
            if self.device == "cuda" and not torch.cuda.is_available():
                self.device = "cpu"
                logger.warning("CUDA not available, falling back to CPU for Indic-Parler-TTS.")

            self._model = ParlerTTSForConditionalGeneration.from_pretrained(self.model_id).to(self.device)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            logger.info("Indic-Parler-TTS loaded successfully.")
        except ImportError:
            self._failed = True
            logger.error("parler-tts or transformers not installed. Cannot use Indic-Parler-TTS.")
        except Exception as e:
            self._failed = True
            logger.error("Failed to load Indic-Parler-TTS: %s", e)

    async def synthesise(self, text: str) -> bytes:
        import io
        import soundfile as sf
        self._load_model()
        
        if self._failed:
            logger.warning("IndicParlerTTS failed. Falling back to EdgeTTS.")
            global _adapter
            _adapter = EdgeTTSAdapter()
            return await _adapter.synthesise(text)
            
        import asyncio
        loop = asyncio.get_event_loop()
        
        def _generate():
            description = "A female speaker delivers a clear and expressive speech with moderate speed."
            input_ids = self._tokenizer(description, return_tensors="pt").input_ids.to(self.device)
            prompt_input_ids = self._tokenizer(text, return_tensors="pt").input_ids.to(self.device)
            
            generation = self._model.generate(
                input_ids=input_ids,
                prompt_input_ids=prompt_input_ids
            )
            audio_arr = generation.cpu().numpy().squeeze()
            
            out_buf = io.BytesIO()
            sf.write(out_buf, audio_arr, self._model.config.sampling_rate, format="WAV")
            return out_buf.getvalue()
            
        try:
            return await loop.run_in_executor(None, _generate)
        except Exception as e:
            logger.error("IndicParlerTTS generation error: %s", e)
            logger.warning("Falling back to EdgeTTS.")
            _adapter = EdgeTTSAdapter()
            return await _adapter.synthesise(text)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        audio_bytes = await self.synthesise(text)
        if audio_bytes:
            yield audio_bytes

    async def health_check(self) -> bool:
        return not self._failed

# ── Factory ────────────────────────────────────────────────────────────────────
_adapter = None

def get_tts_adapter():
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
        elif cfg.provider in ("parler", "parler_tts", "indic_parler"):
            _adapter = IndicParlerTTSAdapter(model_id="ai4bharat/indic-parler-tts")
        else:
            _adapter = EdgeTTSAdapter()
    return _adapter

def reset_tts_adapter() -> None:
    global _adapter
    _adapter = None
"""

if idx != -1:
    with open('adapters/tts.py', 'w', encoding='utf-8') as f:
        f.write(content[:idx] + new_content)
