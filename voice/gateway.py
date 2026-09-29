"""
voice/gateway.py — V1: Voice Session Gateway (deterministic).
Authenticates against session, issues voice_session_id,
enforces max utterance duration (15s), payload/rate limits.
Degrades to text on failure.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Session, VoiceSession

logger = logging.getLogger(__name__)

MAX_UTTERANCE_SECONDS = 15
MAX_AUDIO_BYTES = 16000 * 2 * MAX_UTTERANCE_SECONDS  # 16kHz 16-bit mono = ~480KB


class VoiceGatewayError(Exception):
    pass


class VoiceSessionGateway:
    """
    V1: Voice Session Gateway.
    - Authenticates session token
    - Issues voice_session_id
    - Enforces max utterance length (15s)
    - Rate limits: max utterances per session
    - Degrades to text on gateway failure
    """

    async def open_voice_session(
        self,
        session_token: str,
        db: AsyncSession,
    ) -> str:
        """Authenticate and create a voice session. Returns voice_session_id."""
        stmt = select(Session).where(
            Session.token == session_token,
            Session.is_active == True,
        )
        result = await db.execute(stmt)
        sess = result.scalar_one_or_none()

        if not sess or sess.expires_at < datetime.utcnow():
            raise VoiceGatewayError("Invalid or expired session")

        voice_sess = VoiceSession(
            id=str(uuid.uuid4()),
            user_id=sess.user_id,
            session_id=sess.id,
            started_at=datetime.utcnow(),
        )
        db.add(voice_sess)
        await db.commit()

        logger.info(
            "VoiceGateway: opened voice session %s for user %s",
            voice_sess.id, sess.user_id,
        )
        return voice_sess.id

    def validate_audio(self, audio_bytes: bytes) -> None:
        """
        Validate audio payload.
        Raises VoiceGatewayError if too large (>15s at 16kHz 16-bit mono).
        """
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise VoiceGatewayError(
                f"Audio exceeds maximum {MAX_UTTERANCE_SECONDS}s. "
                "Please use text input or speak a shorter request."
            )

    def check_rate_limit(self, utterance_count: int, max_per_session: int = 50) -> None:
        """Check session-level rate limit."""
        if utterance_count >= max_per_session:
            raise VoiceGatewayError(
                "Too many voice requests in this session. Please use text."
            )
