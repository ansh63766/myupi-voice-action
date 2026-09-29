import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from agents.types import PipelineState
from adapters.asr import get_asr_adapter
from adapters.tts import get_tts_adapter
from voice.normalizer import VoiceNormalizer

logger = logging.getLogger(__name__)

voice_router = APIRouter()
normalizer = VoiceNormalizer()


def _get_asr():
    """Lazy-load ASR adapter so it is only instantiated after dotenv is loaded."""
    return get_asr_adapter()


def _get_tts():
    """Lazy-load TTS adapter so it is only instantiated after dotenv is loaded."""
    return get_tts_adapter()


@voice_router.websocket("/api/voice")
async def voice_websocket(websocket: WebSocket, token: str, conversation_id: str):
    from db.engine import get_session_factory
    from app.main import get_session_and_user, _orchestrator, _conversation_states

    await websocket.accept()

    factory = get_session_factory()

    async with factory() as db:
        try:
            sess, user = await get_session_and_user(token, db)
        except Exception:
            await websocket.close(code=1008, reason="Unauthorized")
            return

        asr_adapter = _get_asr()
        state_key = conversation_id
        audio_buffer = bytearray()

        try:
            while True:
                data = await websocket.receive()

                if "bytes" in data:
                    audio_buffer.extend(data["bytes"])

                elif "text" in data:
                    msg = json.loads(data["text"])

                    if msg.get("type") != "stop_speaking":
                        continue

                    if len(audio_buffer) == 0:
                        continue

                    # ── 1. ASR ──────────────────────────────────────────────────
                    try:
                        transcript = await asr_adapter.transcribe_authoritative(bytes(audio_buffer))
                    except Exception as e:
                        logger.error("ASR Error: %s", e)
                        await websocket.send_json({"type": "error", "text": "Sorry, I couldn't hear that clearly. Please try again."})
                        audio_buffer.clear()
                        continue

                    await websocket.send_json({"type": "transcript", "text": transcript.text})

                    # ── 2. Normalizer ────────────────────────────────────────────
                    norm_slots = normalizer.normalize(transcript.text)
                    processed_text = norm_slots.raw_text

                    # ── 3. Build / restore pipeline state ───────────────────────
                    existing_state = _conversation_states.get(state_key)

                    if existing_state and existing_state.needs_user_input:
                        # Continue multi-turn dialogue (slot fill continuation)
                        state = existing_state
                        if state.slot_fill and state.slot_fill.pending_request:
                            missing_slot = state.slot_fill.pending_request.missing_slot
                            state.intent.extracted_slots = _orchestrator.slot_filler.merge_new_input(
                                state.intent.extracted_slots, processed_text, missing_slot
                            )
                        state.slot_fill = None
                        state.entity_resolution = None
                        state.confirmation = None
                        state.needs_user_input = False
                        state.user_prompt = None
                        state.error = None
                        state.done = False
                    else:
                        # Fresh request
                        state = PipelineState(
                            user_id=user.id,
                            session_id=sess.id,
                            conversation_id=conversation_id,
                            channel="voice",
                            raw_input=processed_text,
                        )

                    # ── 4. Run pipeline ──────────────────────────────────────────
                    state = await _orchestrator.process(state=state, db=db)
                    _conversation_states[state_key] = state

                    # ── 5. Build response payload ────────────────────────────────
                    resp_text = None
                    if state.execution and state.execution.response_text and state.execution.response_text != "__FAQ__":
                        resp_text = state.execution.response_text
                    elif state.user_prompt and not state.needs_user_input:
                        resp_text = state.user_prompt
                    elif state.error:
                        resp_text = state.error

                    if state.needs_user_input and state.user_prompt:
                        # Slot fill / disambiguation prompt — send as plain text
                        await websocket.send_json({"type": "response_text", "text": state.user_prompt})
                        audio_buffer.clear()
                        continue

                    if resp_text:
                        payload: dict = {"type": "response_text", "text": resp_text}

                        if state.confirmation:
                            payload["confirmation_card"] = {
                                "text": state.confirmation.spoken_text or state.confirmation.card_text,
                                "action_id": state.confirmation.action_id,
                                "event_id": state.confirmation.event_id,
                            }

                        if state.execution and state.execution.response_data:
                            payload["response_data"] = state.execution.response_data

                        if state.execution and state.execution.deep_link:
                            payload["deep_link"] = state.execution.deep_link

                        await websocket.send_json(payload)

                        # ── 6. TTS Voice Out ─────────────────────────────────────────
                        try:
                            tts_adapter = _get_tts()
                            async for chunk in tts_adapter.stream(resp_text):
                                await websocket.send_bytes(chunk)
                        except Exception as e:
                            logger.error("TTS Error: %s", e)

                    audio_buffer.clear()

        except WebSocketDisconnect:
            logger.info("Voice websocket disconnected for conversation %s", conversation_id)
        except Exception as e:
            logger.error("Voice websocket unexpected error: %s", e)
            try:
                await websocket.send_json({"type": "error", "text": "Something went wrong. Please try again."})
            except Exception:
                pass

