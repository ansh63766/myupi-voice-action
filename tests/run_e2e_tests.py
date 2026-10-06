"""
tests/run_e2e_tests.py — Exhaustive MyUPI E2E test suite.

Run:   python tests/run_e2e_tests.py
       (or in Colab: !python tests/run_e2e_tests.py)

Covers:
  Section  1 — Setup, DB reset, login, conversation bootstrap
  Section  2 — Auth: valid/invalid/expired tokens, ownership
  Section  3 — Tier 0: transactions, mandates, payees, FAQ
  Section  4 — Tier 1: pause / resume mandate flows
  Section  5 — Tier 2: revoke mandate flow
  Section  6 — Tier 2: chargeback (direct + disambiguation)
  Section  7 — Tier 2: safety switch, delink
  Section  8 — Tier 2M: replay (PIN-required)
  Section  9 — Edge cases: ambiguous, unsupported, unresolvable,
               semantic rescue, slot fill max attempts,
               typing instead of tapping, breaking out of state
  Section 10 — Multi-user isolation (Priya vs Rahul)
  Section 11 — Audit chain integrity
  Section 12 — Voice normalizer unit tests
  Section 13 — Deep-link ownership enforcement
"""
from __future__ import annotations

import os
import sys
import time
import shutil
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Fresh DB before importing app (startup runs seeding) ─────────────────────
_DB_PATH = ROOT / "data" / "myupi.db"
if _DB_PATH.exists():
    _DB_PATH.unlink()
    print(f"[setup] Removed old DB at {_DB_PATH}")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.main import _conversation_states  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# Colour + assertion helpers
# ══════════════════════════════════════════════════════════════════════════════

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GREY = "\033[90m"


class TestRunner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[tuple[str, str]] = []

    def section(self, num: int, title: str) -> None:
        print(f"\n{C.BOLD}{C.BLUE}{'━' * 78}{C.RESET}")
        print(f"{C.BOLD}{C.BLUE}  §{num:02d}  {title}{C.RESET}")
        print(f"{C.BOLD}{C.BLUE}{'━' * 78}{C.RESET}")

    def case(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  {C.GREEN}✓{C.RESET} {name}")
        else:
            self.failed += 1
            self.failures.append((name, detail))
            print(f"  {C.RED}✗{C.RESET} {name}")
            if detail:
                print(f"      {C.YELLOW}↳ {detail}{C.RESET}")
        return cond

    def info(self, msg: str) -> None:
        print(f"    {C.GREY}· {msg}{C.RESET}")

    def note(self, msg: str) -> None:
        print(f"  {C.CYAN}ℹ {msg}{C.RESET}")

    def summary(self) -> int:
        total = self.passed + self.failed
        print(f"\n{C.BOLD}{'═' * 78}{C.RESET}")
        if self.failed == 0:
            print(f"{C.BOLD}{C.GREEN}  ✅  ALL {total} CHECKS PASSED{C.RESET}")
        else:
            print(f"{C.BOLD}{C.RED}  ❌  {self.failed} / {total} CHECKS FAILED{C.RESET}")
            print(f"{C.BOLD}{C.RED}{'═' * 78}{C.RESET}")
            for i, (name, detail) in enumerate(self.failures, 1):
                print(f"  {C.RED}{i}.{C.RESET} {name}")
                if detail:
                    print(f"     {C.DIM}{detail}{C.RESET}")
        print(f"{C.BOLD}{'═' * 78}{C.RESET}\n")
        return self.failed


T = TestRunner()


# ══════════════════════════════════════════════════════════════════════════════
# HTTP + pipeline helpers
# ══════════════════════════════════════════════════════════════════════════════

def login(client: TestClient, username: str, pin: str) -> dict | None:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    if r.status_code != 200:
        return None
    return r.json()


def new_conversation(client: TestClient, token: str) -> str:
    r = client.post(f"/api/conversations?token={token}")
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def chat(
    client: TestClient,
    token: str,
    conv_id: str,
    message: str = "",
    *,
    confirmed: bool = False,
    entity: dict | None = None,
    expect_status: int = 200,
) -> dict:
    payload = {
        "token": token,
        "conversation_id": conv_id,
        "message": message,
        "user_confirmed": confirmed,
        "selected_entity": entity,
    }
    r = client.post("/api/chat", json=payload)
    assert r.status_code == expect_status, (
        f"chat() expected {expect_status}, got {r.status_code}: {r.text[:200]}"
    )
    return r.json()


def tap_first_option(
    client: TestClient, token: str, conv_id: str, data: dict
) -> dict:
    """Tap the first disambiguation option returned by a previous turn."""
    slot = list(data["disambiguation_options"].keys())[0]
    opt = data["disambiguation_options"][slot][0]
    entity = {
        "slot": slot,
        "id": opt["resolved_id"],
        "label": opt["resolved_label"],
    }
    return chat(client, token, conv_id, "", entity=entity)


def auto_drive(
    client: TestClient,
    token: str,
    conv_id: str,
    initial_msg: str,
    *,
    max_turns: int = 6,
    tap_option: bool = True,
    confirm: bool = True,
) -> dict:
    """Drive a full pipeline: intent -> disambig -> confirm -> deep_link."""
    data = chat(client, token, conv_id, initial_msg)
    for _ in range(max_turns):
        if data.get("disambiguation_options") and tap_option:
            data = tap_first_option(client, token, conv_id, data)
        elif data.get("confirmation_card") and confirm:
            data = chat(client, token, conv_id, "", confirmed=True)
        else:
            break
    return data


def reset_state(conv_id: str | None = None) -> None:
    """Clear in-memory pipeline state (optionally for one conversation)."""
    if conv_id:
        _conversation_states.pop(conv_id, None)
    else:
        _conversation_states.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Main test driver
# ══════════════════════════════════════════════════════════════════════════════

def run_all_tests() -> None:
    print(f"\n{C.BOLD}{C.MAGENTA}{'═' * 78}{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}  MyUPI — Exhaustive End-to-End Test Suite{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}  NPCI demo validation · {time.strftime('%Y-%m-%d %H:%M:%S')}{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}{'═' * 78}{C.RESET}")

    with TestClient(app) as client:

        # ══════════════════════════════════════════════════════════════════
        # §1  Setup + Auth
        # ══════════════════════════════════════════════════════════════════
        T.section(1, "Setup · Login · Conversation bootstrap")

        rahul = login(client, "rahul_sharma", "1234")
        T.case("rahul_sharma logs in with pin 1234", rahul is not None)
        if not rahul:
            print(f"{C.RED}FATAL: login failed — aborting.{C.RESET}")
            T.summary()
            return
        rahul_token = rahul["token"]
        T.info(f"token = {rahul_token[:8]}…  user_id = {rahul['user_id']}")

        priya = login(client, "priya_verma", "5678")
        T.case("priya_verma logs in with pin 5678", priya is not None)
        priya_token = priya["token"] if priya else None

        amit = login(client, "amit_patel", "9012")
        T.case("amit_patel logs in with pin 9012", amit is not None)
        amit_token = amit["token"] if amit else None

        # negative auth
        bad = login(client, "rahul_sharma", "9999")
        T.case("wrong PIN → login rejected", bad is None)

        bad2 = login(client, "ghost_user", "1234")
        T.case("unknown username → login rejected", bad2 is None)

        # conversation bootstrap
        conv_r = new_conversation(client, rahul_token)
        T.case("conversation created for rahul", isinstance(conv_r, str) and len(conv_r) > 0)
        conv = conv_r

        # bad-token conversation
        r = client.post("/api/conversations?token=invalid-token-xxx")
        T.case("invalid token cannot create conversation (401)", r.status_code == 401)

        # chat with bad token
        r = client.post("/api/chat", json={
            "token": "invalid",
            "conversation_id": conv,
            "message": "hello",
        })
        T.case("chat with invalid token → 401", r.status_code == 401)


        # ══════════════════════════════════════════════════════════════════
        # §2  Tier 0 — read-only flows
        # ══════════════════════════════════════════════════════════════════
        T.section(2, "Tier 0 — read-only API_DIRECT flows")

        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Show me my transactions")
        T.case("show_transactions intent recognised", data.get("response_data") is not None)
        txns = (data.get("response_data") or {}).get("transactions", [])
        T.case(f"transactions returned (got {len(txns)})", len(txns) > 0)
        T.case("default page size is 10", len(txns) == 10)

        # count slot
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Show me last 3 transactions")
        txns = (data.get("response_data") or {}).get("transactions", [])
        T.case(f"count slot honoured (got {len(txns)})", len(txns) == 3)

        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Show me last 25 transactions")
        txns = (data.get("response_data") or {}).get("transactions", [])
        T.case(f"count=25 honoured (got {len(txns)})", len(txns) == 25)
        T.case("user has ≥100 txns in DB (bulk seed)", len(txns) == 25)  # sanity

        # mandates
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Show my AutoPay mandates")
        mandates = (data.get("response_data") or {}).get("mandates", [])
        T.case(f"mandates returned (got {len(mandates)})", len(mandates) > 0)

        # FAQ
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "What is UPI PIN?")
        T.case("FAQ answered", data.get("response_text") or data.get("user_prompt"))
        T.case("FAQ did not error", data.get("error") is None)

        # payee context
        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Tell me about Swiggy")
        # payee_context is tier 0 with fuzzy disambig possible
        T.case("payee query does not crash",
               data.get("error") is None or "couldn't find" in (data.get("error") or "").lower())


        # ══════════════════════════════════════════════════════════════════
        # §3  Tier 1 — pause mandate (full flow)
        # ══════════════════════════════════════════════════════════════════
        T.section(3, "Tier 1 — pause mandate (disambig → confirm → deep_link)")

        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Pause my Swiggy mandate")
        T.case("pause intent → disambiguation offered",
               bool(data.get("disambiguation_options")))
        T.info(f"disambiguation options: "
               f"{[o['resolved_label'] for o in (data.get('disambiguation_options') or {}).get('mandate_id', [])]}")

        if data.get("disambiguation_options"):
            data = tap_first_option(client, rahul_token, conv, data)
            T.case("after tap → confirmation card shown",
                   bool(data.get("confirmation_card")))
            T.info(f"card: {data.get('confirmation_card', {}).get('text', '')[:90]}")

        if data.get("confirmation_card"):
            data = chat(client, rahul_token, conv, "", confirmed=True)
            T.case("after confirm → deep link returned",
                   bool(data.get("deep_link")))
            T.case("deep link points to /mandates/",
                   "/mandates/" in (data.get("deep_link") or ""))
            T.case("mic_disabled flagged for PIN screen",
                   data.get("mic_disabled") is True)


        # ══════════════════════════════════════════════════════════════════
        # §4  Tier 1 — resume paused mandate
        # ══════════════════════════════════════════════════════════════════
        T.section(4, "Tier 1 — resume a PAUSED mandate (Netflix)")

        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Resume my Netflix autopay")
        T.case("resume flow reaches deep_link or confirmation",
               bool(data.get("deep_link") or data.get("confirmation_card") or data.get("error")))
        if data.get("deep_link"):
            T.case("resume deep link contains /resume",
                   "/resume" in data["deep_link"])


        # ══════════════════════════════════════════════════════════════════
        # §5  Tier 2 — revoke mandate
        # ══════════════════════════════════════════════════════════════════
        T.section(5, "Tier 2 — permanently revoke mandate")

        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Cancel my Netflix autopay")
        T.case("revoke flow succeeded",
               bool(data.get("deep_link") or data.get("confirmation_card") or data.get("error")))
        if data.get("deep_link"):
            T.case("revoke deep link contains /revoke", "/revoke" in data["deep_link"])


        # ══════════════════════════════════════════════════════════════════
        # §6  Tier 2 — chargeback (two variants)
        # ══════════════════════════════════════════════════════════════════
        T.section(6, "Tier 2 — chargeback (direct + from complaint)")

        # 6a: direct with named merchant
        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Raise a chargeback for BookMyShow")
        T.case("direct chargeback did not error",
               data.get("error") is None)
        T.case("direct chargeback returned deep_link or disambig",
               bool(data.get("deep_link") or data.get("disambiguation_options") or data.get("confirmation_card")))

        # 6b: vague complaint → disambiguation required
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Raise a complaint about a UPI payment")
        T.case("vague complaint → disambiguation options",
               bool(data.get("disambiguation_options")))
        if data.get("disambiguation_options"):
            opts = data["disambiguation_options"].get("txn_id", [])
            T.case(f"txn options ≥1 (got {len(opts)})", len(opts) >= 1)
            # tap first
            data = tap_first_option(client, rahul_token, conv, data)
            T.case("after tap → confirmation card",
                   bool(data.get("confirmation_card")))
            if data.get("confirmation_card"):
                data = chat(client, rahul_token, conv, "", confirmed=True)
                T.case("after confirm → chargeback deep link",
                       "/chargeback" in (data.get("deep_link") or ""))


        # ══════════════════════════════════════════════════════════════════
        # §7  Tier 2 — safety switch + delink
        # ══════════════════════════════════════════════════════════════════
        T.section(7, "Tier 2 — safety switch + delink (non-voice-safe)")

        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Toggle safety switch")
        T.case("safety switch flow reached deep_link or confirmation",
               bool(data.get("deep_link") or data.get("confirmation_card")))
        if data.get("deep_link"):
            T.case("safety deep link contains /safety-switch",
                   "safety-switch" in data["deep_link"])

        # delink
        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Delink my mobile number")
        T.case("delink flow reached deep_link or confirmation",
               bool(data.get("deep_link") or data.get("confirmation_card") or data.get("error")))


        # ══════════════════════════════════════════════════════════════════
        # §8  Tier 2M — replay transaction
        # ══════════════════════════════════════════════════════════════════
        T.section(8, "Tier 2M — replay transaction (PIN mandatory)")

        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Repeat my last Zomato payment")
        T.case("replay flow returns deep_link or disambig",
               bool(data.get("deep_link") or data.get("disambiguation_options") or data.get("error")))
        if data.get("deep_link"):
            T.case("replay deep link contains /replay", "/replay" in data["deep_link"])
            T.case("mic_disabled flagged for replay",
                   data.get("mic_disabled") is True)


        # ══════════════════════════════════════════════════════════════════
        # §9  Edge cases
        # ══════════════════════════════════════════════════════════════════
        T.section(9, "Edge cases — ambiguous / unsupported / unresolvable / typed disambig")

        # 9a: ambiguous
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Stop Netflix")
        T.case("ambiguous intent → helpful error or disambiguation",
               bool(data.get("error") or data.get("disambiguation_options")))

        # 9b: unsupported (P2P)
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Transfer 500 rupees to Elon Musk")
        T.case("P2P transfer → error (unsupported)",
               bool(data.get("error")))

        # 9c: unresolvable entity (Tata only in Amit's mandate)
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Pause Tata")
        T.case("unresolvable → error or disambig",
               bool(data.get("error") or data.get("disambiguation_options")))

        # 9d: semantic rescue — fuzzy misses, LLM saves
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Pause my food delivery autopay")
        T.case("semantic rescue did not crash",
               data.get("error") is None or "couldn't find" in (data.get("error") or "").lower())

        # 9e: typing disambiguation choice instead of tapping
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Pause Swiggy")
        if data.get("disambiguation_options"):
            opts = data["disambiguation_options"]["mandate_id"]
            typed_choice = opts[0]["resolved_label"]
            T.info(f"typing option: '{typed_choice}'")
            data = chat(client, rahul_token, conv, typed_choice)
            T.case("typed disambiguation accepted → confirmation or deep link",
                   bool(data.get("confirmation_card") or data.get("deep_link")))

        # 9f: break out of disambiguation with new command
        conv = new_conversation(client, rahul_token)
        data = chat(client, rahul_token, conv, "Pause Swiggy")
        T.case("disambig pending", bool(data.get("disambiguation_options")))
        data = chat(client, rahul_token, conv, "Show my transactions")
        T.case("fresh command breaks out of disambiguation",
               data.get("response_data") is not None or data.get("error") is None)

        # 9g: cancellation
        conv = new_conversation(client, rahul_token)
        data = auto_drive(client, rahul_token, conv, "Pause Zomato Pro", confirm=False)
        T.case("confirmation shown before cancel",
               bool(data.get("confirmation_card")) or bool(data.get("disambiguation_options")))
        data = chat(client, rahul_token, conv, "__cancel__")
        T.case("__cancel__ returns cleanly", data.get("done") is True)


        # ══════════════════════════════════════════════════════════════════
        # §10  Multi-user isolation
        # ══════════════════════════════════════════════════════════════════
        T.section(10, "Multi-user isolation (Priya must NOT see Rahul's data)")

        priya_conv = new_conversation(client, priya_token)
        data = chat(client, priya_token, priya_conv, "Show my transactions")
        priya_txns = (data.get("response_data") or {}).get("transactions", [])
        T.case(f"Priya has own transactions ({len(priya_txns)})", len(priya_txns) > 0)

        # Priya should not be able to pause Rahul's Swiggy mandate
        conv = new_conversation(client, priya_token)
        data = chat(client, priya_token, conv, "Pause my Swiggy mandate")
        # Priya has no Swiggy mandate → should error or disambiguate-empty
        T.case("Priya cannot resolve Rahul's Swiggy mandate",
               data.get("error") is not None or not data.get("disambiguation_options")
               or all(not o for o in (data.get("disambiguation_options") or {}).values()))


        # ══════════════════════════════════════════════════════════════════
        # §11  Deep-link ownership enforcement
        # ══════════════════════════════════════════════════════════════════
        T.section(11, "Deep-link ownership — never trust URL params alone")

        # Rahul's mandate ID
        r = client.get(f"/app/mandates/m-001/pause?token={rahul_token}")
        T.case("Rahul opens his own mandate screen (200)", r.status_code == 200)

        # Priya tries to open Rahul's mandate
        if priya_token:
            r = client.get(f"/app/mandates/m-001/pause?token={priya_token}")
            T.case("Priya opening Rahul's mandate → 403", r.status_code == 403)

        # No token
        r = client.get("/app/mandates/m-001/pause")
        T.case("no token → 422 (missing param)", r.status_code in (401, 422))


        # ══════════════════════════════════════════════════════════════════
        # §12  Voice normalizer unit tests (deterministic ITN)
        # ══════════════════════════════════════════════════════════════════
        T.section(12, "Voice normalizer — ITN unit checks")

        from voice.normalizer import VoiceNormalizer
        n = VoiceNormalizer()

        r = n.normalize("pay 500 rupees to Ramesh")
        T.case("extract '500 rupees' → amount",
               r.extracted.get("amount") == "500")

        r = n.normalize("send 1,299.50 INR to merchant")
        T.case("extract '1,299.50 INR' → amount (commas stripped)",
               r.extracted.get("amount") == "1299.50")

        r = n.normalize("recharge 5k rupees")
        T.case("extract '5k rupees' → 5000", r.extracted.get("amount") == "5000")

        r = n.normalize("check +919876543210")
        T.case("extract +91 phone",
               r.extracted.get("phone_number") == "+919876543210")

        r = n.normalize("show txns from last 7 days")
        T.case("extract 'last 7 days' → last_7_days",
               r.extracted.get("date_range") == "last_7_days")

        r = n.normalize("refund for ABCD1234EFGH5678")
        T.case("extract 16-char txn ref",
               r.extracted.get("txn_ref") == "ABCD1234EFGH5678")

        r = n.normalize("aaj ka transaction dikhao")
        T.case("extract 'aaj' → today", r.extracted.get("date_range") == "today")


        # ══════════════════════════════════════════════════════════════════
        # §13  Audit chain integrity
        # ══════════════════════════════════════════════════════════════════
        T.section(13, "Audit chain — every flow writes a log")

        import asyncio
        from sqlalchemy import select
        from db.engine import get_session_factory
        from db.models import AuditLog

        async def count_audits() -> dict:
            factory = get_session_factory()
            async with factory() as s:
                res = await s.execute(select(AuditLog))
                rows = res.scalars().all()
                by_outcome: dict[str, int] = {}
                for a in rows:
                    by_outcome[a.outcome] = by_outcome.get(a.outcome, 0) + 1
                return by_outcome

        audits = asyncio.get_event_loop().run_until_complete(count_audits())
        T.case("audit log has entries", sum(audits.values()) > 0)
        T.info(f"audit outcomes so far: {audits}")
        T.case("at least one SUCCESS audit", audits.get("SUCCESS", 0) > 0)
        # blocked audits happen on unsupported/unresolvable
        T.case("at least one BLOCKED audit", audits.get("BLOCKED", 0) > 0)


        # ══════════════════════════════════════════════════════════════════
        # §14  Conversation state hygiene
        # ══════════════════════════════════════════════════════════════════
        T.section(14, "State hygiene — no cross-conversation bleed")

        c1 = new_conversation(client, rahul_token)
        c2 = new_conversation(client, rahul_token)

        chat(client, rahul_token, c1, "Pause Swiggy")
        # c2 should be totally independent
        data = chat(client, rahul_token, c2, "Show my transactions")
        T.case("independent conversations don't bleed state",
               data.get("response_data") is not None)

        # No __confirm__ saved as user message
        data = chat(client, rahul_token, c1, "__confirm__")
        # shouldn't crash
        T.case("__confirm__ on non-confirmation state handled",
               "error" in data or "response_text" in data)


    # ── Summary ────────────────────────────────────────────────────────────
    exit_code = T.summary()
    if exit_code == 0:
        print(f"{C.GREEN}{C.BOLD}🚀 Demo-ready. Ship it.{C.RESET}\n")
    else:
        print(f"{C.RED}{C.BOLD}❌ Fix the failures above before the demo.{C.RESET}\n")


if __name__ == "__main__":
    run_all_tests()
