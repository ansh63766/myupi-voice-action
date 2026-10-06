import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from agents.types import PipelineState
from adapters.asr import get_asr_adapter
from voice.normalizer import VoiceNormalizer

logger = logging.getLogger(__name__)

voice_router = APIRouter()
normalizer = VoiceNormalizer()


def _get_asr():
    """Lazy-load ASR adapter so it is only instantiated after dotenv is loaded."""
    return get_asr_adapter()


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
                if data.get("type") == "websocket.disconnect":
                    logger.info("Voice websocket received disconnect frame for %s", conversation_id)
                    break

                if "bytes" in data and data["bytes"]:
                    audio_buffer.extend(data["bytes"])

                elif "text" in data and data["text"]:
                    try:
                        msg = json.loads(data["text"])
                    except Exception:
                        continue

                    if msg.get("type") != "stop_speaking":
                        continue

                    if len(audio_buffer) == 0:
                        await websocket.send_json({"type": "error", "text": "No audio received. Please try speaking again."})
                        continue

                    # ── 1. ASR ──────────────────────────────────────────────────
                    try:
                        transcript = await asr_adapter.transcribe_authoritative(bytes(audio_buffer))
                    except Exception as e:
                        logger.error("ASR Error: %s", e)
                        await websocket.send_json({"type": "error", "text": "Sorry, I couldn't hear that clearly. Please try again."})
                        audio_buffer.clear()
                        continue

                    if not transcript or not transcript.text or not transcript.text.strip():
                        await websocket.send_json({"type": "error", "text": "Could not recognize speech. Please speak louder."})
                        audio_buffer.clear()
                        continue

                    await websocket.send_json({"type": "transcript", "text": transcript.text})

                    # Save user message to database
                    from db.models import Message
                    import uuid
                    try:
                        user_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conversation_id,
                            role="user",
                            content=transcript.text,
                        )
                        db.add(user_msg)
                        await db.commit()
                    except Exception as db_err:
                        logger.warning("Failed to save voice user message: %s", db_err)

                    # ── 2. Normalizer ────────────────────────────────────────────
                    norm_slots = normalizer.normalize(transcript.text)
                    processed_text = norm_slots.raw_text

                    # ── 3. Build / restore pipeline state ───────────────────────
                    existing_state = _conversation_states.get(state_key)

                    if existing_state and existing_state.needs_user_input:
                        state = existing_state
                        state.raw_input = processed_text
                        state.pre_extracted_slots = norm_slots.extracted
                        
                        # Let orchestrator handle merging (slot fill or disambiguation)
                        # We just need to reset the flags so the pipeline reruns
                        state.needs_user_input = False
                        state.user_prompt = None
                        state.error = None
                        state.done = False
                    else:
                        state = PipelineState(
                            user_id=user.id,
                            session_id=sess.id,
                            conversation_id=conversation_id,
                            channel="voice",
                            raw_input=processed_text,
                            pre_extracted_slots=norm_slots.extracted,
                        )

                    # ── 4. Run pipeline ──────────────────────────────────────────
                    state = await _orchestrator.process(state=state, db=db)
                    _conversation_states[state_key] = state

                    # ── 5. Build response payload ────────────────────────────────
                    resp_text = None
                    if state.execution and state.execution.response_text and state.execution.response_text != "__FAQ__":
                        resp_text = state.execution.response_text
                    elif state.user_prompt:
                        resp_text = state.user_prompt
                    elif state.error:
                        resp_text = state.error

                    if not resp_text:
                        resp_text = "I've processed your request."

                    # Save assistant message to database
                    try:
                        asst_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conversation_id,
                            role="assistant",
                            content=resp_text,
                            intent_label=state.intent.intent_label if state.intent else None,
                            action_id=state.action_entry.action_id if state.action_entry else None,
                        )
                        db.add(asst_msg)
                        await db.commit()
                    except Exception as db_err:
                        logger.warning("Failed to save voice assistant message: %s", db_err)

                    payload: dict = {"type": "response_text", "text": resp_text}

                    if state.confirmation:
                        payload["confirmation_card"] = {
                            "text": state.confirmation.spoken_text or state.confirmation.card_text,
                            "action_id": state.confirmation.action_id,
                            "event_id": state.confirmation.event_id,
                        }
                    
                    if state.entity_resolution and state.entity_resolution.needs_disambiguation:
                        payload["disambiguation_options"] = {
                            slot: [
                                {
                                    "id": e.resolved_id,
                                    "label": e.resolved_label,
                                    "resolved_id": e.resolved_id,
                                    "resolved_label": e.resolved_label,
                                    "confidence": e.confidence,
                                }
                                for e in options
                            ]
                            for slot, options in state.entity_resolution.disambiguation_options.items()
                        }

                    if state.execution and state.execution.response_data:
                        payload["response_data"] = state.execution.response_data

                    if state.execution and state.execution.deep_link:
                        payload["deep_link"] = state.execution.deep_link

                    await websocket.send_json(payload)

                    await websocket.send_json({"type": "done"})
                    audio_buffer.clear()

        except WebSocketDisconnect:
            logger.info("Voice websocket disconnected for conversation %s", conversation_id)
        except Exception as e:
            if "disconnect" in str(e).lower() or "closed" in str(e).lower():
                logger.info("Voice websocket disconnected (%s)", e)
            else:
                logger.error("Voice websocket unexpected error: %s", e, exc_info=True)
                try:
                    await websocket.send_json({"type": "error", "text": "Something went wrong. Please try again."})
                except Exception:
                    pass

