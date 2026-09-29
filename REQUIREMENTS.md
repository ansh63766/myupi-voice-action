# MyUPI Voice + Action Prototype — Requirements

## Hardware
| Resource | Available |
|---|---|
| CPU | Intel i7-12650H (10 cores, 16 threads) |
| RAM | 15.6 GB |
| GPU | NVIDIA RTX 3060 Laptop (6 GB VRAM, ~5 GB free) |
| Disk (E:) | ~728 GB free |
| OS | Windows 11 |

## Runtime Versions
| Component | Required | Status |
|---|---|---|
| Python | 3.14.7 (cp314 wheels confirmed for torch) | ✅ INSTALLED |
| Node.js | 24.18.0 | ✅ INSTALLED |
| git | 2.53.0 | ✅ INSTALLED |
| ffmpeg | ≥6.0 | ❌ MISSING |
| Ollama | latest | ❌ MISSING |

## Python Libraries
| Package | Version | Purpose | Status |
|---|---|---|---|
| torch | 2.14.0 (cu124) | Model inference (whisper, VAD) | ❌ MISSING |
| torchaudio | 2.6.0 | Audio processing | ❌ MISSING |
| faster-whisper | ≥1.1.0 | ASR (authoritative pass + captions) | ❌ MISSING |
| TTS (coqui) | ≥0.22.0 | Text-to-speech | ❌ MISSING |
| silero-vad | ≥5.0 | Voice activity detection | ❌ MISSING |
| fastapi | ≥0.115 | Web framework + WebSocket | ❌ MISSING |
| uvicorn[standard] | ≥0.32 | ASGI server | ❌ MISSING |
| sqlalchemy[asyncio] | ≥2.0 | DB ORM | ❌ MISSING |
| aiosqlite | ≥0.20 | Async SQLite driver | ❌ MISSING |
| pydantic | ≥2.9 | Typed models | ❌ MISSING |
| pydantic-settings | ≥2.6 | Config from env | ❌ MISSING |
| pyyaml | ≥6.0 | Registry YAML parsing | ❌ MISSING |
| rapidfuzz | ≥3.10 | Fuzzy string matching | ❌ MISSING |
| indic-transliteration | ≥2.3 | Devanagari ↔ Latin for fuzzy match | ❌ MISSING |
| openai | ≥1.50 | Ollama OpenAI-compatible client | ❌ MISSING |
| httpx | ≥0.27 | Async HTTP client | ❌ MISSING |
| sounddevice | ≥0.5 | Audio capture/playback (server tests) | ❌ MISSING |
| numpy | 2.5.3 | Numeric arrays | ✅ INSTALLED |
| scipy | ≥1.14 | Audio resampling | ❌ MISSING |
| pytest | ≥8.3 | Test runner | ❌ MISSING |
| pytest-asyncio | ≥0.24 | Async test support | ❌ MISSING |
| pytest-cov | ≥6.0 | Coverage | ❌ MISSING |
| alembic | ≥1.14 | DB migrations | ❌ MISSING |
| python-multipart | ≥0.0.12 | FastAPI file uploads | ❌ MISSING |
| websockets | ≥13.0 | WebSocket support | ❌ MISSING |
| python-dotenv | ≥1.0 | .env loading | ❌ MISSING |
| colorlog | ≥6.8 | Colored logging | ❌ MISSING |
| rich | ≥13.9 | Pretty terminal output | ❌ MISSING |
| jsonschema | ≥4.23 | Registry schema validation | ❌ MISSING |
| hashlib | stdlib | Audit chain hashing | ✅ STDLIB |
| uuid | stdlib | Session/entity IDs | ✅ STDLIB |

## Models (to be downloaded)
| Model | Size | Purpose | Download Target |
|---|---|---|---|
| qwen2.5:7b-instruct-q4_K_M | ~4.5 GB | Intent classification LLM (via Ollama) | models/ (via ollama pull) |
| faster-whisper medium | ~1.5 GB | ASR authoritative pass | models/whisper/ |
| faster-whisper tiny | ~150 MB | ASR fast lane (captions) | models/whisper/ |
| silero-vad | ~2 MB | VAD | auto-download via torch hub |
| TTS vits/en | ~80 MB | English TTS | models/tts/ |

## System Packages
| Package | Purpose | Status |
|---|---|---|
| ffmpeg | Audio transcoding, whisper input | ❌ MISSING — manual install required |
| CUDA 12.4 | GPU inference | assumed present with RTX driver |

## Manual Actions Required
1. **Install ffmpeg**: Download from https://www.gyan.dev/ffmpeg/builds/ and add to PATH, OR use `choco install ffmpeg` / `scoop install ffmpeg`
2. **Install Ollama**: Download from https://ollama.com/download/windows and run installer
3. **PowerShell execution policy** for npm: run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
