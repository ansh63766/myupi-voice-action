import os
import logging
import httpx
import base64
from typing import AsyncIterator, Optional
from adapters.asr import ASRAdapter, AuthoritativeTranscript, WordResult
from adapters.tts import TTSAdapter

logger = logging.getLogger(__name__)

class SarvamVoiceASR(ASRAdapter):
    def __init__(self, model_name: str = "saaras:v4"):
        self.model_name = model_name
        self.api_key = os.getenv("SARVAM_API_KEY")
        if not self.api_key:
            logger.warning("SARVAM_API_KEY not set. SarvamVoiceASR will fail.")
        self.endpoint = "https://api.sarvam.ai/speech-to-text"

    async def transcribe_authoritative(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        contextual_biasing: Optional[list[str]] = None,
    ) -> AuthoritativeTranscript:
        
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY not set.")
            
        headers = {
            "api-subscription-key": self.api_key
        }
        
        # httpx multipart form
        files = {
            "file": ("audio.webm", audio_bytes, "audio/webm")
        }
        data = {
            "model": self.model_name
        }
        if language:
            data["language_code"] = language

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint,
                headers=headers,
                files=files,
                data=data,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            
            transcript_text = result.get("transcript", "")
            detected_lang = result.get("language_code", language or "en-IN")
            
            return AuthoritativeTranscript(
                text=transcript_text,
                words=[],  # Rest API doesn't return words without extra params
                language=detected_lang,
                avg_confidence=1.0
            )

    async def stream_captions(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[str]:
        yield ""

    async def health_check(self) -> bool:
        return bool(self.api_key)


class SarvamVoiceTTS(TTSAdapter):
    def __init__(self, model_name: str = "bulbul:v3", language_code: str = "en-IN", speaker: str = "shubh"):
        self.model_name = model_name
        self.language_code = language_code
        self.speaker = speaker
        self.api_key = os.getenv("SARVAM_API_KEY")
        if not self.api_key:
            logger.warning("SARVAM_API_KEY not set. SarvamVoiceTTS will fail.")
        self.endpoint = "https://api.sarvam.ai/text-to-speech"

    async def synthesise(self, text: str) -> bytes:
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY not set.")
            
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": [text], # Sarvam accepts arrays usually, or "text" for single. Wait, docs say "text" for single? Wait! The docs say: {"text": "string"} OR {"inputs": ["string"]}. I'll use {"inputs": [text]} as it's standard for their text api. Wait, the docs say {"text": "...", "target_language_code": "hi-IN", "speaker": "meera", "model": "bulbul:v1"}. Ah, let me use "inputs": [text] and target_language_code!
            "target_language_code": self.language_code,
            "speaker": self.speaker,
            "model": self.model_name,
            "pace": 1.0,
            "enable_preprocessing": True
        }
        # Wait, the search result explicitly gave this schema:
        # { "text": "...", "language_code": "hi-IN", "speaker": "shubh", "model": "bulbul:v3" }
        payload_explicit = {
            "text": text,
            "language_code": self.language_code,
            "speaker": self.speaker,
            "model": self.model_name
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload_explicit,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as e:
                # Fallback to old format just in case
                payload_fallback = {
                    "inputs": [text],
                    "target_language_code": self.language_code,
                    "speaker": self.speaker,
                    "model": self.model_name
                }
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload_fallback,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

        # returns {"audios": ["base64_encoded"]}
        base64_audio = data.get("audios", [])[0]
        return base64.b64decode(base64_audio)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        # REST API doesn't stream chunks, so we synthesize fully and yield one chunk
        audio_bytes = await self.synthesise(text)
        yield audio_bytes

    async def health_check(self) -> bool:
        return bool(self.api_key)
