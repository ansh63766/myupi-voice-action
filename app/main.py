"""
app/main.py — FastAPI application.
Chat API, deep-link app screens, conversation management.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.types import AuditChain, PipelineState
from db.engine import create_all_tables, get_db, get_session_factory
from db.models import (
    Conversation, Mandate, Message, SafetySwitch, Session,
    Transaction, UPINumber, User,
)
from db.seed import seed_db
from orchestrator import Orchestrator

# ── Logging setup ───────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

# Generate a sorted log filename using timestamp (e.g. run_20260929_123045.log)
_LOG_FILE = os.path.join(_LOG_DIR, datetime.now().strftime("run_%Y%m%d_%H%M%S.log"))

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[90m",    # Gray
        logging.INFO: "\033[92m",     # Green
        logging.WARNING: "\033[93m",  # Yellow
        logging.ERROR: "\033[91m",    # Red
        logging.CRITICAL: "\033[95m"  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        # We don't modify the record itself to avoid changing the file output
        fmt = f"%(asctime)s [{color}%(levelname)s{self.RESET}] \033[36m%(name)s\033[0m: %(message)s"
        formatter = logging.Formatter(fmt)
        return formatter.format(record)

# Root logger setup
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
# Remove existing handlers to avoid duplicates on reload
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

import re

class PlainTextFormatter(logging.Formatter):
    ANSI_REGEX = re.compile(r'\x1b\[[0-9;]*m')
    def format(self, record):
        formatted = super().format(record)
        return self.ANSI_REGEX.sub('', formatted)

file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
file_handler.setFormatter(PlainTextFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(ColorFormatter())

root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)
logger = logging.getLogger(__name__)

app = FastAPI(title="MyUPI Prototype", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_orchestrator = Orchestrator()

# Dev mode: always read HTML from disk (edits take effect on browser refresh)
_INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "index.html")


@app.on_event("startup")
async def startup():
    await create_all_tables()
    factory = get_session_factory()
    async with factory() as session:
        await seed_db(session)
    server_url = os.environ.get("ASR_SERVER_URL")
    if server_url:
        logger.info(f"Using remote ASR server at {server_url}. Skipping local GPU preload.")
    else:
        logger.info("Pre-loading ASR model into GPU...")
        import asyncio
        loop = asyncio.get_event_loop()
        def _preload():
            from adapters.asr import get_asr_adapter
            get_asr_adapter()._load_model()
        asyncio.create_task(loop.run_in_executor(None, _preload))
        
    logger.info("MyUPI app started.")

from app.voice_ws import voice_router  # noqa: E402
app.include_router(voice_router)



# ── Auth helpers (simplified for prototype) ────────────────────────────────────

async def get_session_and_user(
    token: str,
    db: AsyncSession,
) -> tuple[Session, User]:
    """Validate session token, return (session, user). Raises 401 if invalid."""
    stmt = select(Session).where(Session.token == token, Session.is_active == True)
    result = await db.execute(stmt)
    sess = result.scalar_one_or_none()
    if not sess or sess.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    stmt2 = select(User).where(User.id == sess.user_id)
    result2 = await db.execute(stmt2)
    user = result2.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return sess, user


# ── Auth routes ────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    pin: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    username: str
    full_name: str


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    import hashlib
    stmt = select(User).where(User.username == req.username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    pin_hash = hashlib.sha256(req.pin.encode()).hexdigest()
    if user.pin_hash != pin_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = str(uuid.uuid4())
    sess = Session(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=8),
        is_active=True,
    )
    db.add(sess)
    await db.commit()
    return LoginResponse(token=token, user_id=user.id, username=user.username, full_name=user.full_name)


# ── Conversation routes ────────────────────────────────────────────────────────

class StartConversationResponse(BaseModel):
    conversation_id: str


@app.post("/api/conversations", response_model=StartConversationResponse)
async def start_conversation(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    sess, user = await get_session_and_user(token, db)
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        channel="text",
    )
    db.add(conv)
    await db.commit()
    return StartConversationResponse(conversation_id=conv.id)


class ChatRequest(BaseModel):
    token: str
    conversation_id: str
    message: str
    user_confirmed: bool = False
    selected_entity: Optional[dict] = None   # for disambiguation tap


class ChatResponse(BaseModel):
    response_text: Optional[str] = None
    needs_input: bool = False
    user_prompt: Optional[str] = None
    error: Optional[str] = None
    done: bool = False
    disambiguation_options: Optional[dict] = None
    confirmation_card: Optional[dict] = None
    deep_link: Optional[str] = None
    response_data: Optional[dict] = None
    mic_disabled: bool = False


# In-memory conversation state (keyed by conversation_id)
# In production this would be Redis / persistent store
_conversation_states: dict[str, PipelineState] = {}
_MAX_STATES = 500  # max in-memory conversations


def _prune_states() -> None:
    """Evict finished states if we're over the cap."""
    if len(_conversation_states) < _MAX_STATES:
        return
    # First remove completed conversations
    done_keys = [k for k, s in _conversation_states.items() if s.done]
    for k in done_keys[:100]:
        del _conversation_states[k]
    # If still over cap, evict oldest entries (Python dicts preserve insertion order)
    if len(_conversation_states) >= _MAX_STATES:
        for k in list(_conversation_states.keys())[:50]:
            del _conversation_states[k]



@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(req.token, db)

    # Verify conversation belongs to this user
    stmt = select(Conversation).where(
        Conversation.id == req.conversation_id,
        Conversation.user_id == user.id,
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Build or restore state
    state_key = req.conversation_id
    existing_state = _conversation_states.get(state_key)

    # Restore state for any continuation turn: slot fill reply, disambiguation tap, or confirmation
    is_continuation = existing_state and (
        existing_state.needs_user_input
        or req.user_confirmed
        or req.selected_entity
    )

    if is_continuation:
        state = existing_state
        if req.user_confirmed:
            # User tapped "Confirm" — clear confirmation so orchestrator runs execution
            state.confirmation = None
            state.needs_user_input = False
            state.done = False
        elif req.selected_entity:
            # User tapped a disambiguation option button.
            # The orchestrator NEEDS state.entity_resolution to still be set to merge this tap.
            # So we only touch what needs resetting — DO NOT clear entity_resolution.
            state.needs_user_input = False
            state.done = False
        elif req.message.strip():
            msg_text = req.message.strip()
            state.raw_input = msg_text  # Always update raw_input

            # Case A: We were waiting for a slot fill answer (e.g., "Which bank?")
            if state.slot_fill and state.slot_fill.pending_request:
                missing_slot = state.slot_fill.pending_request.missing_slot
                state.intent.extracted_slots = _orchestrator.slot_filler.merge_new_input(
                    state.intent.extracted_slots, msg_text, missing_slot
                )
                state.slot_fill = None
                state.entity_resolution = None
                state.confirmation = None
                state.needs_user_input = False
                state.user_prompt = None
                state.error = None
                state.done = False

            elif state.entity_resolution and state.entity_resolution.needs_disambiguation:
                # Case B: We were waiting for a disambiguation tap, but user TYPED instead.
                # Check if their text looks like it matches one of the options (e.g. "Swiggy One").
                # If it doesn't clearly select an option, treat it as a FRESH request.
                slot_name = state.entity_resolution.needs_disambiguation[0]
                raw_options = state.entity_resolution.disambiguation_options.get(slot_name, [])
                matched_id = None
                matched_label = None

                # raw_options is a list[ResolvedEntity]
                if isinstance(raw_options, list):
                    for cand in raw_options:
                        c_id = getattr(cand, "resolved_id", None) or cand.get("resolved_id")
                        c_lbl = getattr(cand, "resolved_label", None) or cand.get("resolved_label", "")
                        if msg_text.lower() in str(c_lbl).lower():
                            matched_id = c_id
                            matched_label = c_lbl
                            break
                elif isinstance(raw_options, dict):
                    for opt_id, opt_label in raw_options.items():
                        if msg_text.lower() in str(opt_label).lower():
                            matched_id = opt_id
                            matched_label = opt_label
                            break

                if matched_id:
                    # User typed something that maps to an option — resolve it directly
                    from agents.types import ResolvedEntity
                    state.entity_resolution.resolved[slot_name] = ResolvedEntity(
                        slot_name=slot_name,
                        raw_text=msg_text,
                        resolved_id=matched_id,
                        resolved_label=matched_label or matched_id,
                        confidence=1.0,
                    )
                    state.entity_resolution.needs_disambiguation.remove(slot_name)
                    if slot_name in state.entity_resolution.disambiguation_options:
                        del state.entity_resolution.disambiguation_options[slot_name]
                        
                    if state.audit.resolved_entity_ids is None:
                        state.audit.resolved_entity_ids = {}
                    state.audit.resolved_entity_ids[slot_name] = matched_id
                    state.policy = None
                    state.confirmation = None
                    state.needs_user_input = False
                    state.user_prompt = None
                    state.error = None
                    state.done = False
                else:
                    # User typed a completely new command — start fresh
                    _prune_states()
                    state = PipelineState(
                        user_id=user.id,
                        session_id=sess.id,
                        conversation_id=conv.id,
                        channel=conv.channel,
                        raw_input=msg_text,
                    )
                    _conversation_states[state_key] = state
            else:
                # Generic continuation (e.g., typing after an error)
                state.needs_user_input = False
                state.done = False
                state.error = None
    else:
        # Fresh request — start new pipeline
        _prune_states()  # evict old states before adding new one
        state = PipelineState(
            user_id=user.id,
            session_id=sess.id,
            conversation_id=conv.id,
            channel=conv.channel,
            raw_input=req.message,
        )
        _conversation_states[state_key] = state


    # Save user message (skip system commands like __confirm__ or __cancel__)
    if req.message and req.message.strip() and not req.message.startswith("__"):
        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role="user",
            content=req.message,
        )
        db.add(msg)
        await db.commit()

    # Handle cancel action from UI (user clicked Cancel on confirmation card)
    if req.message == '__cancel__':
        _conversation_states.pop(state_key, None)
        return ChatResponse(response_text=None, done=True)

    # Run pipeline
    state = await _orchestrator.process(
        state=state,
        db=db,
        user_confirmed=req.user_confirmed,
        selected_entity=req.selected_entity,
    )
    _conversation_states[state_key] = state

    # Build response text
    resp_text = None
    if state.execution and state.execution.response_text and state.execution.response_text != "__FAQ__":
        resp_text = state.execution.response_text
    elif state.user_prompt and not state.needs_user_input:
        # Only use user_prompt as response_text when it is a final answer (FAQ, error fallback),
        # NOT when we are still waiting for user input (slot fill prompts show via user_prompt field)
        resp_text = state.user_prompt

    # Save assistant message
    content_to_save = resp_text or state.user_prompt or state.error
    if content_to_save and str(content_to_save).strip():
        amsg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role="assistant",
            content=content_to_save,
            intent_label=state.intent.intent_label if state.intent else None,
            action_id=state.action_entry.action_id if state.action_entry else None,
        )
        db.add(amsg)
        await db.commit()

    # Disambiguation options
    disambig = None
    if state.entity_resolution and state.entity_resolution.needs_disambiguation:
        disambig = {
            slot: [e.model_dump() for e in options]
            for slot, options in state.entity_resolution.disambiguation_options.items()
        }

    # Confirmation card
    conf_card = None
    if state.confirmation:
        conf_card = {
            "text": state.confirmation.card_text,
            "action_id": state.confirmation.action_id,
            "event_id": state.confirmation.event_id,
        }

    # Deep link
    deep_link = None
    mic_disabled = False
    if state.execution and state.execution.deep_link:
        deep_link = state.execution.deep_link
    if state.execution and state.execution.response_data:
        mic_disabled = state.execution.response_data.get("mic_disabled", False)

    return ChatResponse(
        response_text=resp_text,
        needs_input=state.needs_user_input,
        user_prompt=state.user_prompt if state.needs_user_input else None,
        error=state.error,
        done=state.done,
        disambiguation_options=disambig,
        confirmation_card=conf_card,
        deep_link=deep_link,
        response_data=state.execution.response_data if state.execution else None,
        mic_disabled=mic_disabled,
    )


# ── Deep-link handler (mock BHIM screens) ─────────────────────────────────────

@app.get("/app/mandates/{mandate_id}/pause")
async def screen_mandate_pause(mandate_id: str, token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    # CRITICAL: re-fetch from DB using authenticated user — never trust URL param alone
    stmt = select(Mandate).where(Mandate.id == mandate_id, Mandate.user_id == user.id)
    result = await db.execute(stmt)
    mandate = result.scalar_one_or_none()
    if not mandate:
        raise HTTPException(status_code=403, detail="Access denied")
    return JSONResponse({"screen": "mandate_pause", "mandate": {"id": mandate.id, "merchant": mandate.merchant_name, "amount": float(mandate.amount), "status": mandate.status}})


@app.get("/app/mandates/{mandate_id}/revoke")
async def screen_mandate_revoke(mandate_id: str, token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(Mandate).where(Mandate.id == mandate_id, Mandate.user_id == user.id)
    result = await db.execute(stmt)
    mandate = result.scalar_one_or_none()
    if not mandate:
        raise HTTPException(status_code=403, detail="Access denied")
    return JSONResponse({"screen": "mandate_revoke", "mandate": {"id": mandate.id, "merchant": mandate.merchant_name}})


@app.get("/app/transactions/{txn_id}/chargeback")
async def screen_chargeback(txn_id: str, token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(Transaction).where(Transaction.id == txn_id, Transaction.user_id == user.id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=403, detail="Access denied")
    if not txn.eligible_chargeback:
        raise HTTPException(status_code=400, detail="Transaction not eligible for chargeback")
    return JSONResponse({"screen": "chargeback", "transaction": {"id": txn.id, "ref": txn.txn_ref, "payee": txn.payee_name, "amount": float(txn.amount)}})


@app.get("/app/transactions/{txn_id}/replay")
async def screen_replay(txn_id: str, token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(Transaction).where(Transaction.id == txn_id, Transaction.user_id == user.id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=403, detail="Access denied")
    # mic_disabled enforced at app shell level — signal it in the response
    return JSONResponse({"screen": "replay_with_pin", "mic_disabled": True, "transaction": {"id": txn.id, "payee": txn.payee_name, "amount": float(txn.amount)}})


@app.get("/app/settings/safety-switch")
async def screen_safety_switch(token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(SafetySwitch).where(SafetySwitch.user_id == user.id)
    result = await db.execute(stmt)
    ss = result.scalar_one_or_none()
    return JSONResponse({"screen": "safety_switch", "is_active": ss.is_active if ss else False})


@app.get("/app/settings/delink/{number_id}")
async def screen_delink(number_id: str, token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(UPINumber).where(UPINumber.id == number_id, UPINumber.user_id == user.id)
    result = await db.execute(stmt)
    upi = result.scalar_one_or_none()
    if not upi:
        raise HTTPException(status_code=403, detail="Access denied")
    return JSONResponse({"screen": "delink_number", "id": upi.id, "number": upi.number, "vpa": upi.vpa})


# ── UI Data Endpoints (REST) ──────────────────────────────────────────────────

@app.get("/api/mandates")
async def get_mandates(token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(Mandate).where(Mandate.user_id == user.id).order_by(Mandate.start_date.desc())
    result = await db.execute(stmt)
    mandates = result.scalars().all()
    data = [
        {
            "id": m.id,
            "merchant": m.merchant_name,
            "bank": m.bank_name,
            "amount": float(m.amount),
            "frequency": m.frequency,
            "status": m.status,
            "next_debit": m.next_debit_date.isoformat() if m.next_debit_date else None,
        }
        for m in mandates
    ]
    return JSONResponse({"mandates": data})

@app.get("/api/transactions")
async def get_transactions(token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    txns = result.scalars().all()
    data = [
        {
            "id": t.id,
            "ref": t.txn_ref,
            "payee": t.payee_name,
            "amount": float(t.amount),
            "type": t.txn_type,
            "status": t.status,
            "date": t.created_at.isoformat(),
            "bank": t.bank_name,
            "eligible_chargeback": t.eligible_chargeback,
        }
        for t in txns
    ]
    return JSONResponse({"transactions": data})

@app.get("/api/safety")
async def get_safety(token: str, db: AsyncSession = Depends(get_db)):
    sess, user = await get_session_and_user(token, db)
    stmt = select(SafetySwitch).where(SafetySwitch.user_id == user.id)
    result = await db.execute(stmt)
    ss = result.scalar_one_or_none()
    
    stmt2 = select(UPINumber).where(UPINumber.user_id == user.id)
    result2 = await db.execute(stmt2)
    numbers = [{"id": n.id, "number": n.number} for n in result2.scalars().all()]
    
    return JSONResponse({
        "safety_switch": ss.is_active if ss else False,
        "upi_numbers": numbers
    })

@app.post("/api/execute")
async def execute_action(req: Request, db: AsyncSession = Depends(get_db)):
    data = await req.json()
    token = data.get("token")
    action = data.get("action")
    target_id = data.get("id")
    
    sess, user = await get_session_and_user(token, db)
    
    pin = data.get("pin")
    expected = {"user-001": "1234", "user-002": "5678", "user-003": "9012"}.get(user.id)
    if expected and pin != expected:
        raise HTTPException(status_code=401, detail="Incorrect UPI PIN")
    if action == "pause":
        await db.execute(update(Mandate).where(Mandate.id == target_id, Mandate.user_id == user.id).values(status="PAUSED"))
    elif action == "resume":
        await db.execute(update(Mandate).where(Mandate.id == target_id, Mandate.user_id == user.id).values(status="ACTIVE"))
    elif action == "revoke":
        await db.execute(update(Mandate).where(Mandate.id == target_id, Mandate.user_id == user.id).values(status="REVOKED"))
    elif action == "chargeback":
        await db.execute(update(Transaction).where(Transaction.id == target_id, Transaction.user_id == user.id).values(chargeback_raised=True))
    elif action == "replay":
        stmt = select(Transaction).where(Transaction.id == target_id, Transaction.user_id == user.id)
        result = await db.execute(stmt)
        old_txn = result.scalar_one_or_none()
        if old_txn:
            new_txn = Transaction(
                id=str(uuid.uuid4()),
                user_id=user.id,
                payee_name=old_txn.payee_name,
                payee_vpa=old_txn.payee_vpa,
                amount=old_txn.amount,
                txn_type=old_txn.txn_type,
                status="SUCCESS",
                bank_name=old_txn.bank_name,
                txn_ref=f"T{uuid.uuid4().hex[:8].upper()}",
                created_at=datetime.utcnow(),
                eligible_chargeback=True
            )
            db.add(new_txn)
    elif action == "delink":
        from db.models import UPINumber
        from sqlalchemy import delete
        await db.execute(delete(UPINumber).where(UPINumber.id == target_id, UPINumber.user_id == user.id))
    elif action == "safety-switch":
        from db.models import SafetySwitch
        # mock toggling it
        stmt = select(SafetySwitch).where(SafetySwitch.user_id == user.id)
        result = await db.execute(stmt)
        ss = result.scalar_one_or_none()
        if ss:
            await db.execute(update(SafetySwitch).where(SafetySwitch.user_id == user.id).values(is_active=not ss.is_active))
    
    await db.commit()
    return JSONResponse({"status": "ok"})

# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Chat UI (served from static files) ────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Always read from disk — edits take effect on Ctrl+Shift+R, no server restart needed
    if os.path.exists(_INDEX_HTML_PATH):
        with open(_INDEX_HTML_PATH, encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        })
    return HTMLResponse("<h1>MyUPI Prototype — UI loading...</h1>")

