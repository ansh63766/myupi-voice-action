# MyUPI Voice Assistant — Project Overview

## 1. What is this Project About?
**MyUPI** is an agentic, voice-first assistant designed for UPI (Unified Payments Interface) operations. It enables users to perform everyday payment inquiries, AutoPay mandate actions, and dispute resolutions naturally via voice (in Indian English and Indic languages) while strictly enforcing payment security, risk tiers, and anti-hallucination guardrails.

Rather than acting as a standard generic LLM chatbot, MyUPI implements a **deterministic, multi-agent pipeline** that couples speech-to-text (ASR) with policy checks, entity resolution, and database action execution.

---

## 2. Core Capabilities & Use Cases
- **Transaction Inquiries**: View recent transactions, inspect specific merchant transactions (e.g., Swiggy, Zomato), and check statuses.
- **AutoPay Mandate Management**: List active recurring payments, pause mandates temporarily, or revoke/cancel them.
- **Dispute Resolution & Chargebacks**: Identify eligible disputed transactions and initiate formal chargeback requests.
- **Security & Safety Switch**: Emergency safety lock to suspend UPI access, link/delink UPI numbers.
- **UPI Support & FAQs**: Answer general UPI, PIN, and limit queries.

---

## 3. Architecture & Pipeline Flow

The system processes requests through a deterministic pipeline coordinated by a stateless **Orchestrator**:

```
[User Voice Input (Microphone)]
                │
                ▼
[WebSocket Gateway: /api/voice]
                │
                ▼
   [ASR Adapter: Bodhan-AI Indic-Transcribe-Flex]
                │
                ▼
      [Voice Normalizer] (transliteration / cleaning)
                │
                ▼
     [Orchestrator Pipeline]
        ├── 1. Intent Classification Agent (LLM)
        ├── 2. Registry Lookup Agent (Static Action Mapping & Risk Tiers)
        ├── 3. Slot Filling Agent (Multi-turn parameter gathering)
        ├── 4. Entity Resolver Agent (RapidFuzz over user accounts & merchants)
        ├── 5. Policy & Risk Agent (Tier 0 direct / Tier 1 confirmation required)
        ├── 6. Confirmation Agent (Explicit user confirmation cards for state mutations)
        ├── 7. Execution Agent (Database mutations & mock BHIM actions)
        └── 8. Audit Agent (Cryptographic hash chaining & action audit logs)
                │
                ▼
[Response Payloads & Structured UI Cards sent back over WebSocket]
```

### Risk & Confirmation Model
- **Tier 0 (Read-Only / FAQ)**: `API_DIRECT` — Executes immediately (e.g., viewing transactions).
- **Tier 1 (State Mutations)**: `CONFIRMATION_CARD` — Requires explicit user confirmation via card/click before execution (e.g., pausing an AutoPay mandate).
- **Tier 2 (Sensitive / Financial Transfer)**: Redirects or deep-links to app with PIN entry required (e.g., transaction replay, PIN change).

---

## 4. Key Components & Technologies
- **ASR (Speech-to-Text)**: `bodhan-ai/indic-transcribe-flex` (NVIDIA NeMo architecture), optimized for Indian accented English and Indic phonetics. Cached locally on disk (`/content/asr_model`) for fast initialization.
- **LLM Reasoning**: OpenAI-compatible endpoint (hosted vLLM or local model) used strictly for intent classification and entity slot extraction.
- **Entity Resolution**: RapidFuzz fuzzy matching for resolving spoken merchant names and contacts against local database records.
- **Backend Framework**: FastAPI with WebSocket support for streaming audio packets and returning JSON event payloads.
- **Database**: SQLite with SQLAlchemy AsyncIO (`aiosqlite`) tracking users, bank accounts, UPI numbers, transactions, mandates, and audit logs.
- **Frontend UI**: Responsive HTML/CSS/JavaScript interface supporting live voice recording (WebRTC MediaRecorder), transaction history tables, and interactive confirmation cards.

---

## 5. File Structure

```
myupi-voice-action/
├── adapters/
│   ├── asr.py                 # Bodhan-AI Indic ASR adapter with local cache loader
│   ├── fuzzy.py               # RapidFuzz entity and slot matching
│   └── llm.py                 # OpenAI-compatible LLM client interface
├── agents/
│   ├── audit_agent.py         # Cryptographic chain & audit log writer
│   ├── confirmation_agent.py  # Renders interactive confirmation cards
│   ├── entity_resolver.py     # Resolves merchants/payees to DB records
│   ├── execution_agent.py     # Executes DB queries and business logic
│   ├── intent_agent.py        # LLM-based intent and slot extractor
│   ├── policy_agent.py        # Enforces NPCI/banking tier rules
│   ├── registry_lookup.py     # Maps classified intents to action IDs
│   ├── slot_filling_agent.py  # Tracks missing slots and multi-turn dialogs
│   ├── support_agent.py       # RAG / FAQ retrieval support
│   └── types.py               # Core Pydantic state models (PipelineState, etc.)
├── app/
│   ├── main.py                # FastAPI server, lifecycle startup, and REST endpoints
│   ├── voice_ws.py            # WebSocket endpoint (/api/voice) for audio streaming
│   └── templates/
│       └── index.html         # Web application UI with microphone & chat cards
├── config/
│   ├── config.yaml            # Primary configuration file (LLM URL, ASR model)
│   └── settings.py            # Pydantic Settings configuration loader
├── db/
│   ├── engine.py              # Async SQLAlchemy engine & session factory
│   ├── models.py              # Database models (User, Transaction, Mandate, AuditLog)
│   └── seed.py                # Initial mock data for testing (transactions, mandates)
├── registry/
│   ├── loader.py              # Registry YAML loader
│   └── registry.yaml          # Action definitions, required slots, and risk tiers
├── voice/
│   ├── gateway.py             # Voice session tracking & rate limiting
│   └── normalizer.py          # Transliteration and text cleanup
├── .env.example               # Example environment variable file
├── .gitignore                 # Standard Python/data gitignore
├── context_project_overview.md# Complete project documentation and overview
├── orchestrator.py            # Main pipeline coordinator
└── requirements.txt           # Python application dependencies
```

---

## 6. How It Works (Step-by-Step Request Lifecycle)

1. **User Speaks**: User presses the microphone in the web UI. MediaRecorder sends 250ms chunks of audio over WebSocket.
2. **Audio Transcription**: Upon silence or user stopping, `adapters/asr.py` converts the raw audio buffer to 16kHz WAV and transcribes it using `indic-transcribe-flex`.
3. **Intent & Slot Extraction**: `IntentAgent` queries the configured LLM to classify the user's intent (e.g., `pause_autopay`) and extract slots (e.g., `{"merchant_name": "Swiggy"}`).
4. **Registry Lookup**: `RegistryLookupAgent` checks `registry.yaml` to retrieve the registered action (`mandate_pause`), required slots (`merchant_name`), and risk tier (Tier 1).
5. **Entity Resolution**: `EntityResolverAgent` uses RapidFuzz to resolve "Swiggy" against active mandates for the authenticated user in the database.
6. **Policy Evaluation**: `PolicyRiskAgent` notes that `mandate_pause` is Tier 1 and requires explicit confirmation.
7. **Confirmation Prompt**: `ConfirmationAgent` formats a confirmation card payload (`"Do you want to pause your Swiggy AutoPay of ₹450/month?"`) and sends it over the WebSocket.
8. **User Confirms**: The user clicks "Confirm" (or says "Yes").
9. **Execution**: `ExecutionAgent` updates the mandate status to `PAUSED` in SQLite and logs the state transition.
10. **Audit**: `AuditAgent` writes an immutable `AuditLog` entry tying together the session, transcript hash, policy tier, and outcome.
