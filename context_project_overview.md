# MyUPI Voice Action - Project Overview

## What is this project?
MyUPI Voice Action is a prototype conversational AI agent built for the BHIM app. It enables users to interact with their UPI application using natural language via voice or text. The system aims to simplify complex app navigation and actions by allowing users to speak their intent (e.g., "Pause my Swiggy AutoPay", "Show me my last 5 transactions", "Verify this UPI ID").

## What is MyUPI?
MyUPI is a unified hub within the BHIM app that consolidates UPI features like AutoPay mandates, Transaction history, Payee management, and Safety controls. The AI Assistant lives within this hub, providing an intelligent conversational overlay that maps natural language directly to these features.

## Architecture
The system is built on an Orchestrator-Agent design pattern:
1. **Frontend (UI)**: A mobile-first web interface (`index.html`) simulating the BHIM app's MyUPI hub. It connects to the backend via REST for initial rendering and WebSockets for real-time streaming voice/text chat.
2. **Gateway**: Receives audio or text, normalizes it, and sends it to the Orchestrator.
3. **Orchestrator (`orchestrator.py`)**: A state machine that drives the conversation forward through a pipeline of specialized agents:
   - **Intent Agent**: Classifies user intent and extracts slots using an LLM.
   - **Registry**: Maps the intent to an allowed Action (Tier 0 for read-only, Tier 1/2 for state-mutating actions).
   - **Slot Filling Agent**: Ensures all required data (like Merchant Name or UPI ID) is collected. Prompts the user if missing.
   - **Entity Resolver**: Maps raw extracted text (e.g., "swiggy") to actual database IDs using RapidFuzz or an LLM Semantic Rescue. Handles disambiguation if multiple matches are found.
   - **Policy Agent**: Checks risk and enforces business logic (e.g., denying unsupported transactions, checking chargeback eligibility).
   - **Confirmation Agent**: For state-mutating actions, renders a confirmation card before proceeding.
   - **Execution Agent**: Mocks the execution of read actions (returning JSON) or writes (generating deep links to native app screens that request a UPI PIN).
4. **Mock Database (`db/`)**: SQLite database populated with dummy data (Transactions, Mandates, Payees, etc.) to simulate a real user's UPI profile.

## How it works
1. **Input**: User speaks or types. If voice, ASR transcribes it.
2. **Classification**: Intent Agent determines what the user wants to do.
3. **Entity Resolution**: The system fuzzy-matches the user's words against their own database (e.g., finding the correct AutoPay mandate for "Netflix").
4. **Disambiguation**: If the match is ambiguous (e.g., two mandates for "Amazon"), the system pauses and asks the user to tap or type the correct one.
5. **Execution/Deep Linking**: 
   - For read operations (e.g., "Show transactions"), the agent returns a structured payload that the UI renders beautifully inline.
   - For write operations (e.g., "Pause AutoPay"), the agent returns a Deep Link. The UI intercepts this link, asks the user for their UPI PIN, and then securely calls the backend `/api/execute` endpoint to commit the change.

## Key Files & Structure
- `/app/main.py`: FastAPI server, REST routes, deep link execution endpoint.
- `/app/voice_ws.py`: WebSocket handler for real-time voice and chat streaming.
- `/app/templates/index.html`: The complete frontend UI, mimicking the BHIM app.
- `/orchestrator.py`: The core state machine orchestrating the conversation pipeline.
- `/agents/`: The specialized agents (Intent, Entity Resolver, Slot Filling, Execution, Policy).
- `/registry/`: YAML configurations mapping intents to required slots and risk tiers.
- `/db/`: SQLAlchemy models, seeds, and the SQLite DB file.
- `/adapters/`: Wrappers for external AI services (LLM, ASR, TTS).
