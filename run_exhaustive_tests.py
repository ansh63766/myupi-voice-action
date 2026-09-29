import asyncio
import json
import time
import httpx

BASE_URL = "http://127.0.0.1:8000"

TEST_CASES = [
    # ── Category 1: View Transactions (6) ──
    {"id": 1, "cat": "Transactions", "q": "Show my transactions"},
    {"id": 2, "cat": "Transactions", "q": "Show my recent payments"},
    {"id": 3, "cat": "Transactions", "q": "mere transactions dikhao"},
    {"id": 4, "cat": "Transactions", "q": "pichle payments show karo"},
    {"id": 5, "cat": "Transactions", "q": "transaction history check karni hai"},
    {"id": 6, "cat": "Transactions", "q": "लेन-देन का इतिहास दिखाएं"},

    # ── Category 2: AutoPay Mandates View & Actions (8) ──
    {"id": 7, "cat": "Mandates", "q": "Show my mandates"},
    {"id": 8, "cat": "Mandates", "q": "mere autopay mandates dikhao"},
    {"id": 9, "cat": "Mandates", "q": "check active mandates"},
    {"id": 10, "cat": "Mandates", "q": "Pause Jio Fiber autopay"},
    {"id": 11, "cat": "Mandates", "q": "Jio Fiber ka autopay pause karo"},
    {"id": 12, "cat": "Mandates", "q": "Resume Netflix autopay"},
    {"id": 13, "cat": "Mandates", "q": "Netflix mandate resume karo"},
    {"id": 14, "cat": "Mandates", "q": "Revoke Swiggy Instamart mandate"},

    # ── Category 3: Disambiguation & Ties (5) ──
    {"id": 15, "cat": "Disambiguation", "q": "Pause Swiggy"},
    {"id": 16, "cat": "Disambiguation", "q": "Swiggy wala autopay pause karo"},
    {"id": 17, "cat": "Disambiguation", "q": "Cancel Swiggy"},
    {"id": 18, "cat": "Disambiguation", "q": "Pay Ramesh"},
    {"id": 19, "cat": "Disambiguation", "q": "Pay Rakesh"},

    # ── Category 4: Chargebacks & Disputes (6) ──
    {"id": 20, "cat": "Chargebacks", "q": "Raise chargeback for Zomato payment"},
    {"id": 21, "cat": "Chargebacks", "q": "Zomato transaction dispute karna hai"},
    {"id": 22, "cat": "Chargebacks", "q": "I want to dispute the movie tickets"},
    {"id": 23, "cat": "Chargebacks", "q": "Raise chargeback for Amazon Pay"},
    {"id": 24, "cat": "Chargebacks", "q": "Dispute my Paytm transaction"},
    {"id": 25, "cat": "Chargebacks", "q": "file complaint for payment"},

    # ── Category 5: Safety Switch & UPI Delink (5) ──
    {"id": 26, "cat": "Safety & Security", "q": "Turn on safety switch"},
    {"id": 27, "cat": "Safety & Security", "q": "Disable safety switch"},
    {"id": 28, "cat": "Safety & Security", "q": "Block my UPI"},
    {"id": 29, "cat": "Safety & Security", "q": "Delink my mobile number"},
    {"id": 30, "cat": "Safety & Security", "q": "Unlink 9876543210 from UPI"},

    # ── Category 6: Support & FAQs (8) ──
    {"id": 31, "cat": "FAQ/Support", "q": "What is UPI?"},
    {"id": 32, "cat": "FAQ/Support", "q": "UPI kya hai?"},
    {"id": 33, "cat": "FAQ/Support", "q": "What is a UPI PIN?"},
    {"id": 34, "cat": "FAQ/Support", "q": "UPI PIN kya hota hai?"},
    {"id": 35, "cat": "FAQ/Support", "q": "What is AutoPay?"},
    {"id": 36, "cat": "FAQ/Support", "q": "Why did my transaction fail?"},
    {"id": 37, "cat": "FAQ/Support", "q": "Transaction fail kyu hua?"},
    {"id": 38, "cat": "FAQ/Support", "q": "What is UPI ID or VPA?"},

    # ── Category 7: Ambiguous Cases (4) ──
    {"id": 39, "cat": "Ambiguity", "q": "Stop Netflix"},
    {"id": 40, "cat": "Ambiguity", "q": "Netflix band kar do"},
    {"id": 41, "cat": "Ambiguity", "q": "Stop my Jio bill"},
    {"id": 42, "cat": "Ambiguity", "q": "Cancel my subscription"},

    # ── Category 8: Unsupported & Boundary Cases (5) ──
    {"id": 43, "cat": "Unsupported", "q": "Transfer 500 rupees to Elon Musk"},
    {"id": 44, "cat": "Unsupported", "q": "Send 1000 to Rahul"},
    {"id": 45, "cat": "Unsupported", "q": "What is the weather today in Delhi?"},
    {"id": 46, "cat": "Unsupported", "q": "Tell me a joke"},
    {"id": 47, "cat": "Unsupported", "q": "Can you order pizza for me?"},

    # ── Category 9: Slang, Typos & Edge Cases (5) ──
    {"id": 48, "cat": "Edge Cases", "q": "swigy autopay roko"},
    {"id": 49, "cat": "Edge Cases", "q": "Show tranzactions"},
    {"id": 50, "cat": "Edge Cases", "q": "hello"},
    {"id": 51, "cat": "Edge Cases", "q": "help"},
    {"id": 52, "cat": "Edge Cases", "q": "kya kar sakte ho?"},
]


async def run_suite():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        # 1. Login
        print("Logging in as rahul_sharma...")
        login_res = await client.post("/api/auth/login", json={"username": "rahul_sharma", "pin": "1234"})
        if login_res.status_code != 200:
            print(f"Login failed: {login_res.status_code} {login_res.text}")
            return
        token = login_res.json()["token"]
        print("Login successful! Token acquired.")

        results = []
        passed = 0
        failed = 0

        print(f"\nRunning {len(TEST_CASES)} exhaustive test queries...\n" + "=" * 70)

        for tc in TEST_CASES:
            # Start fresh conversation for each to avoid state pollution
            conv_res = await client.post(f"/api/conversations?token={token}")
            conv_id = conv_res.json()["conversation_id"]

            t0 = time.time()
            chat_res = await client.post(
                "/api/chat",
                json={"token": token, "conversation_id": conv_id, "message": tc["q"]},
            )
            latency = round(time.time() - t0, 2)

            data = chat_res.json()
            status = "PASS"
            notes = []

            if chat_res.status_code != 200:
                status = "FAIL"
                notes.append(f"HTTP {chat_res.status_code}")
            else:
                has_content = any([
                    data.get("response_text"),
                    data.get("user_prompt"),
                    data.get("error"),
                    data.get("confirmation_card"),
                    data.get("disambiguation_options"),
                    data.get("deep_link"),
                    data.get("response_data")
                ])
                if not has_content:
                    status = "FAIL"
                    notes.append("Empty response payload")

            if status == "PASS":
                passed += 1
                icon = "OK"
            else:
                failed += 1
                icon = "FAIL"

            summary_info = ""
            if data.get("response_data"):
                k = list(data["response_data"].keys())
                summary_info = f"[Data: {k}]"
            elif data.get("confirmation_card"):
                summary_info = f"[Card: {data['confirmation_card'].get('card_title', 'Confirm')}]"
            elif data.get("disambiguation_options"):
                summary_info = f"[Disambig: {list(data['disambiguation_options'].keys())}]"
            elif data.get("deep_link"):
                summary_info = f"[DeepLink: {data['deep_link'][:30]}...]"
            elif data.get("error"):
                summary_info = f"[Handled: {data['error'][:35]}...]"
            elif data.get("response_text"):
                summary_info = f"[Text: {data['response_text'][:35]}...]"

            q_disp = tc['q'].encode('ascii', 'replace').decode('ascii')
            sum_disp = summary_info.encode('ascii', 'replace').decode('ascii')
            print(f"[{icon:4}] #{tc['id']:02d} ({tc['cat']:15}) '{q_disp:34}' -> {sum_disp} ({latency}s)")
            results.append({**tc, "status": status, "latency": latency, "notes": notes, "resp": data})

        print("=" * 70)
        print(f"Total: {len(TEST_CASES)} | Passed: {passed} | Failed: {failed} | Pass Rate: {round(passed/len(TEST_CASES)*100, 1)}%")

        with open("test_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print("Results saved to test_results.json")


if __name__ == "__main__":
    asyncio.run(run_suite())
