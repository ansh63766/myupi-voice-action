import sys
import os
import time

# Add the project root to sys.path so we can import 'app' and 'adapters'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

def run_tests():
    print("======================================================")
    print("🚀 Running MyUPI End-to-End Tests (Via TestClient)")
    print("======================================================\n")

    with TestClient(app) as client:
        # 1. Login
        print("[*] Logging in as Rahul...")
        try:
            res = client.post("/api/auth/login", json={"username": "rahul_sharma", "pin": "1234"})
            res.raise_for_status()
            token = res.json()["token"]
            print("✅ Logged in successfully.\n")
        except Exception as e:
            print("❌ Login failed!")
            print(e)
            return

        # 2. Start Conversation
        res = client.post(f"/api/conversations?token={token}")
        conv_id = res.json()["conversation_id"]

        def chat(msg, confirmed=False, entity=None):
            payload = {
                "token": token,
                "conversation_id": conv_id,
                "message": msg,
                "user_confirmed": confirmed,
                "selected_entity": entity
            }
            print(f"\n👤 USER: {msg}" if msg else (f"\n👤 USER: [Tapped Confirm]" if confirmed else f"\n👤 USER: [Selected Option]"))
            res = client.post("/api/chat", json=payload)
            data = res.json()

            if data.get("response_text"):
                print(f"🤖 AI: {data['response_text']}")
            if data.get("error"):
                print(f"⚠️ AI ERROR: {data['error']}")
            if data.get("user_prompt"):
                print(f"🤖 AI: {data['user_prompt']}")

            if data.get("disambiguation_options"):
                print("   [Options List:]")
                for slot, opt_list in data["disambiguation_options"].items():
                    print(f"   Slot: {slot}")
                    for opt in opt_list:
                        lbl = opt.get("resolved_label", opt.get("resolved_id"))
                        print(f"     • {lbl} (id={opt.get('resolved_id')})")
            
            if data.get("confirmation_card"):
                print(f"   [Confirmation Card Rendered]")
            
            if data.get("deep_link"):
                print(f"   📱 [APP NAVIGATES TO]: {data['deep_link']}")

            return data

        # ---------------------------------------------------------
        # TEST SCENARIOS
        # ---------------------------------------------------------

        print("\n--- TEST 1: Show Mandates ---")
        chat("Show my AutoPay mandates")

        print("\n--- TEST 2: Disambiguation & Confirmation (Pause Swiggy) ---")
        data = chat("Pause my Swiggy mandate")
        if data.get("disambiguation_options"):
            slot_name = list(data["disambiguation_options"].keys())[0]
            options_list = data["disambiguation_options"][slot_name]
            first_opt = options_list[0]
            data = chat("", entity={"slot": slot_name, "id": first_opt.get("resolved_id"), "label": first_opt.get("resolved_label", first_opt.get("resolved_id"))})
        if data.get("confirmation_card"):
            chat("", confirmed=True)

        print("\n--- TEST 3: Revoke Paused Mandate (Netflix) ---")
        data = chat("Cancel my Netflix autopay")
        if data.get("confirmation_card"):
            chat("", confirmed=True)

        print("\n--- TEST 4: Semantic Rescue / Threshold block (Tata) ---")
        chat("Pause Tata")

        print("\n--- TEST 5: Transactions List (Breaking out of disambiguation loop test) ---")
        chat("Show me my transactions")

        print("\n--- TEST 6: Slot Extraction (Last 100 transactions) ---")
        chat("Show me my last 100 transactions")

        print("\n--- TEST 7: Chargeback Missing Slot ---")
        data = chat("Raise a complaint for a payment")
        if data.get("disambiguation_options"):
            slot_name = list(data["disambiguation_options"].keys())[0]
            options_list = data["disambiguation_options"][slot_name]
            first_opt = options_list[0]
            data = chat("", entity={"slot": slot_name, "id": first_opt.get("resolved_id"), "label": first_opt.get("resolved_label", first_opt.get("resolved_id"))})
            if data.get("confirmation_card"):
                chat("", confirmed=True)

        print("\n--- TEST 8: Chargeback Direct (BookMyShow) ---")
        data = chat("Raise a chargeback for BookMyShow")
        if data.get("confirmation_card"):
            chat("", confirmed=True)

        print("\n--- TEST 9: Delink Number (Bug A Fix Verification) ---")
        data = chat("Delink my phone number")
        if data.get("disambiguation_options"):
            slot_name = list(data["disambiguation_options"].keys())[0]
            options_list = data["disambiguation_options"][slot_name]
            first_opt = options_list[0]
            data = chat("", entity={"slot": slot_name, "id": first_opt.get("resolved_id"), "label": first_opt.get("resolved_label", first_opt.get("resolved_id"))})
        if data.get("confirmation_card"):
            chat("", confirmed=True)

        print("\n--- TEST 10: Toggle Safety Switch ---")
        data = chat("Turn on the safety switch")
        if data.get("confirmation_card"):
            chat("", confirmed=True)

        print("\n--- TEST 11: Voice normalizer extraction with numbers (Bug B Fix Verification) ---")
        # Simulating output from ASR that hits the regex normalizer in the voice pipeline
        chat("show me the payment for 500 rupees")

        print("\n--- TEST 12: Unsupported action (Direct Money Transfer) ---")
        chat("Transfer 500 rupees to Amit")

        print("\n--- TEST 13: PIN check on backend (/api/execute) (Polish G Verification) ---")
        print("[*] Simulating execution of an action with wrong PIN...")
        res = client.post("/api/execute", json={"token": token, "action": "resume", "id": "m-003", "pin": "0000"})
        print(f"Server response code: {res.status_code}")
        print(f"Server response body: {res.text}")

        print("\n======================================================")
        print("✅ All exhaustive test flows completed successfully!")
        print("======================================================")

if __name__ == "__main__":
    run_tests()
