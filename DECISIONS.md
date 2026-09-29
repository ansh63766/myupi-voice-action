# DECISIONS.md

## Architecture & Tooling Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **LLM: Ollama + Qwen2.5:7B (Q4_K_M)** | RTX 3060 6GB VRAM fits a 4-bit 7B model (~4.5 GB). Open-source, local, fast enough for intent classification. Swappable via config. |
| 2 | **ASR: faster-whisper (medium model, CUDA)** | Runs on GPU, per-word confidence, streaming-capable. Faster-whisper is CTranslate2-based — runs on the RTX 3060 efficiently. Swappable via config. |
| 3 | **TTS: Coqui TTS (XTTS-v2 or vits)** | Local, offline, multi-lingual (Hindi/English), good quality. If VRAM clash at runtime, switch to piper-tts (CPU) via config. |
| 4 | **VAD: silero-vad** | Small, accurate, runs on CPU, BSD license. |
| 5 | **Fuzzy matching: rapidfuzz + indic-transliteration** | rapidfuzz for edit-distance; indic-transliteration for Devanagari ↔ Latin mapping. Pure Python, no model. |
| 6 | **DB: SQLite via SQLAlchemy (async with aiosqlite)** | Meets spec, local, swappable. |
| 7 | **Web framework: FastAPI + uvicorn** | Async, typed, good WebSocket support for streaming voice. |
| 8 | **Frontend: Vanilla JS + HTML (no framework)** | Keeps the stack minimal; this is a prototype, not a product. |
| 9 | **Python 3.14.7 — accept as-is** | Already installed. torch/whisper support Python ≤3.12 officially. Will create venv with Python 3.12 via py launcher or install Python 3.12 separately if py launcher available. |
| 10 | **Python version: install 3.12 via winget** | Python 3.14 too new for torch CUDA wheels. Install 3.12 alongside for the project venv. |
| 11 | **ffmpeg: winget install** | Required for audio processing, torchaudio, whisper. |
| 12 | **Audio capture: sounddevice (Python) + browser MediaRecorder API** | sounddevice for server-side tests; browser MediaRecorder for voice UI PTT. |
| 13 | **npm scripts execution policy** | Will use `node` directly or `npx` bypassing ps1 scripts issue; or set execution policy for session only. |
| 14 | **Ollama for LLM serving** | Install via winget; pull qwen2.5:7b-instruct-q4_K_M model. Provides OpenAI-compatible API, easy to swap. |
| 15 | **Config: config/config.yaml + .env** | All model/tool selections in config.yaml; secrets in .env. |
