# CONTEXT.md — MyUPI Voice + Action Prototype

> Single source of truth for this project. Read fully before writing any code. If anything here conflicts with an assumption of yours, this file wins. Do not add features not listed here.

## 1. What we're building
A local prototype (personal laptop, no real BHIM/NPCI systems) that proves two things:
1. **Chatbot that mimics MyUPI (BHIM)**: understands UPI help requests, resolves things against the user's own data, and carries out actions safely.
2. **Voice layer on top of it**: speech in → same chatbot pipeline → speech out. Voice is only an input/output modality. It has **no business logic of its own** and must converge on the exact same intent/tool-call path as text. Two separate paths into one action is the failure mode to avoid.

MyUPI = UPI Help 2.0 in BHIM: consolidated view of UPI transactions + AutoPay mandates across banks/apps, multilingual self-service support. Real system runs on NPCI's FiMI small LLM (English/Hindi/Hinglish). In the prototype, **any small LLM stands in for FiMI** behind an adapter.

## 2. Non-negotiable principles
1. **The LLM only classifies intent (and extracts raw slot text).** Deterministic code does everything else: entity resolution, risk tier, execution mode, confirmation text. No model output is ever trusted with an identifier, a risk decision, or a confirmation string.
2. **Resolve, don't transcribe.** Anything that addresses a real object (mandate, transaction, payee) is matched against a candidate set from *that user's own data* using transliteration-aware fuzzy matching ("Swiggy wala", "swggy" → Swiggy mandate). Open-vocabulary problem → closed-set ranking problem. For now we will test it against combination of both fuzzy matching as well as semantic check if the score is low by using small llm/embeddings.
3. **Never guess.** One high-confidence match resolves silently; close candidates → tap-to-select disambiguation list; no match → clear message + alternative path.
4. **Voice proposes, the system disposes.** Voice never authenticates and never completes a consequential action without an on-screen confirmation.
5. **Everything is config-driven via the Action Registry.** Adding a new action (e.g. UPI Lite top-up) = one registry entry, zero changes to orchestrator/agents.
6. **Every model/tool is behind an interface** (LLM, ASR, TTS, VAD, translation, fuzzy matcher, DB). Swapping any of them = config change only. Never hardcode a model/vendor in agent logic.
7. **Fail visibly, never silently degrade.** Each failure has a defined user-visible behavior (see §9).

## 3. Agents (pipeline stages)
Each agent = one class, one responsibility, typed input/output (Pydantic or equivalent), independently unit-testable, no agent does another's job. Orchestrator only passes state between them and reads the registry; it contains **zero feature-specific logic**.

| # | Agent | Type | Responsibility |
|---|---|---|---|
| 1 | **Intent Agent** | LLM (schema-constrained JSON out) | Normalised text → `{intent_label, extracted_slots{raw text}, language, confidence}`. Constrained/validated output; invalid output is impossible/rejected. Never decides the action. |
| 2 | **Registry Lookup** | Deterministic | intent_label → registry entry (slots, tier, mode, template, disambiguation source, voice_safe). |
| 3 | **Slot-Filling Agent (Dialogue Manager)** | Deterministic + templated prompts | Diff extracted vs required slots. Missing → templated (not LLM-generated, multilingual) clarification. Max **3 attempts** per slot, then hand user a direct link to the relevant tab instead of guessing. Iterative. |
| 4 | **Entity Resolver Agent** | Deterministic | Fetch candidates from the registry's `disambiguation_source` for *this user*; transliteration-aware fuzzy match; thresholds: resolve / disambiguate / no-match. Outputs resolved IDs as structured slots that travel **around** the LLM. |
| 5 | **Policy/Risk Agent** | Deterministic | Reads tier from registry (immutable, never inferred). Enforces tier rules, voice_safe flag, confidence gating (low word/entity confidence → disambiguate, not confirm). |
| 6 | **Confirmation Agent** | Deterministic (versioned templates) | Renders confirmation from **versioned template + backend data keyed by resolved ID** — never from transcript or LLM. On-screen card, and spoken too when voice is active (Tier 1+). Logs template ID+version shown. |
| 7 | **Execution Agent** | Deterministic | Executes per mode (§5). Tier 0 API direct; Tier 1+ per registry. |
| 8 | **Support/FAQ Agent** | LLM grounded on small local KB (RAG or in-prompt) | Tier 0 general UPI support/FAQ answers only. No actions. Must say when KB doesn't cover it. |
| 9 | **Audit Agent** | Deterministic | Writes the unbroken audit chain (§8). Blocks execution if a link is missing. |
| V1 | **Voice Session Gateway** | Deterministic | Auth against session, issues `voice_session_id`, enforces max utterance (15 s), payload/rate limits, backpressure → degrade to text. |
| V2 | **Capture/VAD Agent** | Model behind interface | Push-to-talk (no open mic in v1). VAD only detects end-of-speech + trims silence; never decides content. 16 kHz mono. |
| V3 | **ASR Agent (2 lanes)** | Model behind interface | **Fast lane**: streaming partials → on-screen captions ONLY, never acted upon. **Authoritative pass**: full utterance at end-of-speech, native script, per-word confidence → the only transcript entering the business path. Supports contextual biasing with per-user lexicon (own payee names, VPAs, bank names, mandate nicknames). |
| V4 | **Normalizer Agent (ITN)** | Rule/FST-style, deterministic | Inverse text normalisation + rule/gazetteer extraction of amounts, relative dates, mobile numbers, txn refs, mandate nicknames. Rule-based on purpose: rules can't silently alter a digit, generative models can. |
| V5 | **TTS Agent** | Model behind interface | Pre-render + cache the ~top templated utterances (confirmations, errors, greetings, disambiguation prompts); synthesise only slot values (amount/date/payee) at runtime and concatenate; stream chunks. Speaking sensitive values (e.g. balance) is a **user setting, OFF by default**. |
| V6 | **Language Router** *(final phase, optional)* | Deterministic + translation model | Telugu/Tamil: ASR native script → V4 extract & resolve ALL entities first → translate **only residual intent text** to English → Intent Agent gets text + already-resolved slots. Translation never sees a digit/ref/name, so it can't corrupt one. |

Language: taken from user profile locale with a visible switch. **No auto language detection in v1.** Core languages: English, Hindi, Hinglish. Telugu/Tamil via V6 only after everything else passes.

## 4. Pipeline (text and voice converge here)
```
[Voice: PTT → VAD → Gateway → ASR authoritative → Normalizer(ITN) ]──┐
[Text input ─────────────────────────────────────────────────────────]├─> normalised text
                                                                       ▼
 1 Intent Agent → 2 Registry Lookup → 3 Slot Filling → 4 Entity Resolver
   → 5 Policy/Risk → 6 Confirmation → 7 Execution → response (text card + TTS if voice)
                         every step ──> 9 Audit Agent
```
Mic input from ASR fast lane only feeds captions. Resolved entity IDs bypass the LLM as structured slots.

## 5. Action Registry (declarative, e.g. `registry.yaml`)
Fields per action: `action_id`, `intent_labels[]`, `required_slots[]`, `optional_slots[]`, `disambiguation_source`, `risk_tier` (immutable), `execution_mode`, `deep_link_template`, `confirmation_template_id` (versioned), `voice_safe` (bool), `version`, `enabled`.

| action_id | Tier | execution_mode | required slots | disambiguation_source |
|---|---|---|---|---|
| txn_view / mandate_view | 0 | API_DIRECT | (filters optional) | user_transactions / user_active_mandates |
| payee_context | 0 | API_DIRECT | payee | user_saved_payees |
| faq_support | 0 | API_DIRECT | — | — |
| mandate_pause / mandate_resume | 1 | API_WITH_CONFIRMATION | mandate_id | user_active_mandates |
| mandate_revoke | 2 | DEEP_LINK | mandate_id | user_active_mandates |
| safety_switch | 2 | DEEP_LINK | — | — |
| upi_number_delink | 2 | DEEP_LINK | number | user_upi_numbers |
| chargeback | 2 | DEEP_LINK | txn_id | user_eligible_transactions |
| transaction_replay | **2M** | DEEP_LINK_THEN_PIN | txn_id | user_transactions |

Example entry: `mandate_pause` → intents `pause_mandate, stop_mandate, halt_autopay`; required `mandate_id`; optional `bank_name, merchant_name`; DEEP/API per table; template `confirm_mandate_pause_v2`; deep link `bhim://myupi/mandates/{mandate_id}/pause`; voice_safe true.

**Execution modes**
- `API_DIRECT`: backend call, result rendered in chat. Tier 0 read-only only.
- `API_WITH_CONFIRMATION`: backend call after user confirms card. Tier 1 toggles.
- `DEEP_LINK`: open the final pre-filled action screen directly (no tab switching, scrolling or simulated clicks). One tap completes. Tier 2.
- `DEEP_LINK_THEN_PIN`: same, ending in simulated UPI PIN entry, **mic hard-disabled** on that screen. Tier 2M.

Rejected approaches (don't build): stepping through tabs/screens programmatically; direct API for state-changing actions.

## 6. Risk tiers
| Tier | Category | Required confirmation |
|---|---|---|
| 0 | Read only | None; execute + respond |
| 1 | Reversible | Templated read-back + on-screen card, single tap |
| 2 | Consequential | On-screen confirmation + app's auth factor (simulate with an OTP/biometric mock). **Never voice-only** |
| 2M | Moves money (Transaction Replay) | All of Tier 2, ending in UPI PIN entry, mic disabled |

Tier is set in the registry only. Never inferred from text/model. Whether Transaction Replay may be *initiated* by voice is a config flag (`voice_safe`), default on for demo but must always terminate at the PIN screen with mic off.

## 7. Deep-link simulation (no real BHIM app)
Build a small web app with mock "BHIM" screens acting as the app: chat screen + final action screens (mandate pause/revoke, safety switch, number delink, chargeback, replay w/ PIN pad). Deep link = navigation event from chat to `/app/...` final screen, pre-filled. Route handlers must:
- never trust URI params alone; **re-fetch the entity from the DB using the current authenticated user session**, reject anything not owned by that user;
- after completion, return the user to the **same conversation** (state preserved);
- disable mic capture on any screen that can accept a PIN, enforced in the app shell (not by convention).

## 8. Data / DB (SQLite is fine; keep it swappable)
Seed realistic multi-user mock data (≥3 users, Hinglish/Indic-ish merchant & payee names incl. near-duplicates to stress fuzzy matching, e.g. two "Swiggy"/"Sharma" variants):
`users`, `bank_accounts`, `upi_numbers`, `payees`, `transactions` (id 16-char refs, amount, status, eligibility for chargeback), `mandates` (merchant, bank, amount, state: active/paused/revoked), `safety_switch_state`, `sessions`, `conversations`/`messages`, `confirmation_templates` (id, version, language, body, legal_reviewed flag), `audit_log`, `voice_sessions`, `faq_kb`.
**Audit invariant:** every effectful action carries an unbroken chain: `voice_session_id (or text session id)` → `authoritative transcript hash` → `resolved entity IDs` → `policy tier` → `confirmation event id (+template id/version)` → `downstream API call id`. If any link is missing, the action does not fire.

## 9. Failure behavior (each visible to user)
- ASR down → mic disabled with message, text offered
- Low confidence / close candidates → disambiguation list
- Unsupported language → offer supported set
- TTS down → on-screen text, action still completes
- Intent LLM down → voice disabled (no degraded intent path); text shows error
- Slot unfilled after 3 tries → direct link to relevant tab
- Never silently fall back to a lower-quality model

## 10. Latency targets (hypotheses; measure locally, report actuals)
End-of-speech detect 150 ms · upload 120 · gateway 40 · authoritative ASR 400 · normalise+resolve 30 · intent decode 250 · policy+template 10 · first TTS byte 80 → **~1.08 s**; objective p95 ≤ 1.2 s, p99 ≤ 2.0 s on paper. On a laptop these will differ: measure per stage, log them, don't fake them.

## 11. Evaluation & tests (must be built, must pass)
Priority order (non-negotiable):
1. **False action rate** — side-effecting action on wrong object/intent after confirmation. **Target 0; one occurrence fails the phase.**
2. **Entity preservation rate** — exact match on every critical slot after resolution (primary selection metric).
3. Task success rate (end-to-end w/o typing fallback)
4. Disambiguation rate (watch item)
5. WER strict AND tolerant reported side by side (diagnostic only; gap is the signal)
6. Latency percentiles (end of speech → first audio byte)

Eval set: own labelled set, each utterance labelled with **entities + intended action**, not only transcript. Include code-mixing (English words like "chargeback", "mandate", "UPI ID" inside Hindi), noisy audio, misspellings, transliteration variants, near-duplicate names, ambiguous references, out-of-scope asks. ≥ ~100 utterances per language for the prototype (scaled down from the 500 production target). Generate audio for voice tests with the TTS agent plus noise/speed augmentation.

Required test layers: unit per agent · registry-schema validation · scenario tests per action (happy path, missing slot, ambiguous, no match, wrong-user entity, tampered deep link, cancel mid-flow) · Tier enforcement tests (voice-only can never complete Tier 2/2M) · mic-off-on-PIN-screen test · audit-chain-break test (action must NOT fire) · e2e text and e2e voice on the same scenarios giving identical action outcomes.

## 12. Out of scope (do not build)
Real BHIM/NPCI integrations, real payments, production infra/GPUs/Triton, licensing/legal/compliance work, always-on mic, auto language detection, multi-speaker diarization, model fine-tuning.

## 13. Definition of done
Preflight passes · text chatbot passes all §11 tests · voice layer passes same scenarios via audio · false action rate = 0 on eval set · metrics report + latency table generated · README with one-command run · every model/tool swappable via config only.
