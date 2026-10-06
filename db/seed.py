"""
db/seed.py — Seed realistic multi-user mock data.
≥3 users, Hinglish/Indic-ish merchant & payee names with near-duplicates
to stress fuzzy matching (e.g. two "Swiggy"/"Sharma" variants).
Idempotent — safe to run multiple times.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import create_all_tables, get_session_factory
from db.models import (
    BankAccount,
    ConfirmationTemplate,
    FAQEntry,
    Mandate,
    Payee,
    SafetySwitch,
    Transaction,
    UPINumber,
    User,
)

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _pin_hash(pin: str) -> str:
    """Fake bcrypt for prototype (DO NOT use in production)."""
    return hashlib.sha256(pin.encode()).hexdigest()


def _days_ago(n: int) -> datetime:
    return datetime.utcnow() - timedelta(days=n)


def _txn_ref() -> str:
    import secrets, string
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))


# ── Seed data definitions ──────────────────────────────────────────────────────

USERS = [
    {
        "id": "user-001",
        "username": "rahul_sharma",
        "full_name": "Rahul Sharma",
        "phone": "+919876543210",
        "locale": "hi-Latn",
        "pin": "1234",
    },
    {
        "id": "user-002",
        "username": "priya_verma",
        "full_name": "Priya Verma",
        "phone": "+919123456789",
        "locale": "en",
        "pin": "5678",
    },
    {
        "id": "user-003",
        "username": "amit_patel",
        "full_name": "Amit Patel",
        "phone": "+919988776655",
        "locale": "hi",
        "pin": "9012",
    },
]

BANK_ACCOUNTS = [
    # Rahul - 2 banks
    {"id": "ba-001", "user_id": "user-001", "bank_name": "HDFC Bank", "account_last4": "4321", "ifsc": "HDFC0001234", "is_primary": True, "balance": 45000.0},
    {"id": "ba-002", "user_id": "user-001", "bank_name": "SBI", "account_last4": "8765", "ifsc": "SBIN0001234", "is_primary": False, "balance": 12000.0},
    # Priya - 1 bank
    {"id": "ba-003", "user_id": "user-002", "bank_name": "ICICI Bank", "account_last4": "9876", "ifsc": "ICIC0001234", "is_primary": True, "balance": 78000.0},
    # Amit - 2 banks
    {"id": "ba-004", "user_id": "user-003", "bank_name": "Axis Bank", "account_last4": "1234", "ifsc": "UTIB0001234", "is_primary": True, "balance": 32000.0},
    {"id": "ba-005", "user_id": "user-003", "bank_name": "Kotak Bank", "account_last4": "5678", "ifsc": "KKBK0001234", "is_primary": False, "balance": 8500.0},
]

UPI_NUMBERS = [
    {"id": "upi-001", "user_id": "user-001", "number": "+919876543210", "vpa": "9876543210@hdfcbank", "is_primary": True, "linked_bank_id": "ba-001"},
    {"id": "upi-002", "user_id": "user-001", "number": "+919876543211", "vpa": "rahulsharma@sbi", "is_primary": False, "linked_bank_id": "ba-002"},
    {"id": "upi-003", "user_id": "user-002", "number": "+919123456789", "vpa": "priyaverma@icici", "is_primary": True, "linked_bank_id": "ba-003"},
    {"id": "upi-004", "user_id": "user-003", "number": "+919988776655", "vpa": "amitpatel@axisbank", "is_primary": True, "linked_bank_id": "ba-004"},
]

# Near-duplicate payees to stress fuzzy matching
PAYEES = [
    # Rahul's payees — "Swiggy wala" and "Swiggy" as near-duplicates
    {"id": "p-001", "user_id": "user-001", "display_name": "Swiggy wala", "canonical_name": "Swiggy", "vpa": "swiggy@yesbank", "is_merchant": True},
    {"id": "p-002", "user_id": "user-001", "display_name": "Swggy Food", "canonical_name": "Swiggy", "vpa": "swiggy.food@icici", "is_merchant": True},
    {"id": "p-003", "user_id": "user-001", "display_name": "Ramesh Sharma", "canonical_name": "Ramesh Sharma", "vpa": "rameshsharma@upi", "phone": "+919000001111", "is_merchant": False},
    {"id": "p-004", "user_id": "user-001", "display_name": "Rakesh Sharma", "canonical_name": "Rakesh Sharma", "vpa": "rakeshsharma@upi", "phone": "+919000002222", "is_merchant": False},  # near-dup
    {"id": "p-005", "user_id": "user-001", "display_name": "Amazon Pay", "canonical_name": "Amazon", "vpa": "amazon@apl", "is_merchant": True},
    {"id": "p-006", "user_id": "user-001", "display_name": "Zomato", "canonical_name": "Zomato", "vpa": "zomato@axisbank", "is_merchant": True},
    {"id": "p-012", "user_id": "user-001", "display_name": "BookMyShow", "canonical_name": "BookMyShow", "vpa": "bms@kotak", "is_merchant": True},
    # Priya's payees
    {"id": "p-007", "user_id": "user-002", "display_name": "Netflix India", "canonical_name": "Netflix", "vpa": "netflix@icici", "is_merchant": True},
    {"id": "p-008", "user_id": "user-002", "display_name": "Netfliks", "canonical_name": "Netflix", "vpa": "netfliks@upi", "is_merchant": True},  # misspelling near-dup
    {"id": "p-009", "user_id": "user-002", "display_name": "Anita Verma", "canonical_name": "Anita Verma", "vpa": "anitaverma@upi", "phone": "+919111112222", "is_merchant": False},
    # Amit's payees
    {"id": "p-010", "user_id": "user-003", "display_name": "Jio Recharge", "canonical_name": "Jio", "vpa": "jio@jiomoney", "is_merchant": True},
    {"id": "p-011", "user_id": "user-003", "display_name": "Jio wala", "canonical_name": "Jio", "vpa": "jio.prepaid@jiomoney", "is_merchant": True},  # near-dup
]

# Near-duplicate mandates
MANDATES = [
    # Rahul's mandates
    {"id": "m-001", "user_id": "user-001", "merchant_name": "Swiggy One", "merchant_vpa": "swiggy.one@yesbank", "bank_name": "HDFC Bank", "amount": 199.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(180), "next_debit_date": _days_ago(-15), "nickname": "Swiggy membership"},
    {"id": "m-002", "user_id": "user-001", "merchant_name": "Swiggy Instamart", "merchant_vpa": "swiggy.im@yesbank", "bank_name": "HDFC Bank", "amount": 99.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(90), "next_debit_date": _days_ago(-5), "nickname": "Swiggy Instamart"},  # near-dup
    {"id": "m-003", "user_id": "user-001", "merchant_name": "Netflix", "merchant_vpa": "netflix@icici", "bank_name": "SBI", "amount": 649.0, "frequency": "MONTHLY", "status": "PAUSED", "start_date": _days_ago(365), "nickname": "Netflix"},
    {"id": "m-004", "user_id": "user-001", "merchant_name": "Jio Fiber", "merchant_vpa": "jiofib@jiomoney", "bank_name": "HDFC Bank", "amount": 1499.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(200), "next_debit_date": _days_ago(-20), "nickname": "Jio Fiber"},
    {"id": "m-009", "user_id": "user-001", "merchant_name": "Zomato Pro", "merchant_vpa": "zomato.pro@axisbank", "bank_name": "HDFC Bank", "amount": 149.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(60), "next_debit_date": _days_ago(-8), "nickname": "Zomato Pro"},
    {"id": "m-010", "user_id": "user-001", "merchant_name": "Apple Music", "merchant_vpa": "apple@apl", "bank_name": "HDFC Bank", "amount": 99.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(120), "next_debit_date": _days_ago(-3), "nickname": "Apple Music"},
    # Priya's mandates
    {"id": "m-005", "user_id": "user-002", "merchant_name": "Amazon Prime", "merchant_vpa": "prime@apl", "bank_name": "ICICI Bank", "amount": 299.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(365), "next_debit_date": _days_ago(-10)},
    {"id": "m-006", "user_id": "user-002", "merchant_name": "Spotify India", "merchant_vpa": "spotify@icici", "bank_name": "ICICI Bank", "amount": 119.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(120), "next_debit_date": _days_ago(-3)},
    # Amit's mandates
    {"id": "m-007", "user_id": "user-003", "merchant_name": "Tata Play", "merchant_vpa": "tataplay@upi", "bank_name": "Axis Bank", "amount": 350.0, "frequency": "MONTHLY", "status": "ACTIVE", "start_date": _days_ago(240), "next_debit_date": _days_ago(-7)},
    {"id": "m-008", "user_id": "user-003", "merchant_name": "Tata AIG Insurance", "merchant_vpa": "tataaig@upi", "bank_name": "Axis Bank", "amount": 2500.0, "frequency": "YEARLY", "status": "ACTIVE", "start_date": _days_ago(400), "next_debit_date": _days_ago(-35)},  # near-dup with Tata Play
]

TRANSACTIONS = [
    # Rahul's transactions — some eligible for chargeback
    {"user_id": "user-001", "payee_name": "Swiggy", "payee_vpa": "swiggy@yesbank", "amount": 450.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "note": "Food order", "created_at": _days_ago(2), "eligible_chargeback": False},
    {"user_id": "user-001", "payee_name": "Amazon Pay", "payee_vpa": "amazon@apl", "amount": 1299.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "note": "Electronics", "created_at": _days_ago(5), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "BookMyShow", "payee_vpa": "bms@kotak", "amount": 650.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "note": "Movie tickets", "created_at": _days_ago(6), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "Zomato", "payee_vpa": "zomato@axisbank", "amount": 320.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "note": "Food delivery", "created_at": _days_ago(7), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "Ramesh Sharma", "payee_vpa": "rameshsharma@upi", "amount": 5000.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "SBI", "note": "Rent", "created_at": _days_ago(10), "eligible_chargeback": False},
    {"user_id": "user-001", "payee_name": "Rakesh Sharma", "payee_vpa": "rakeshsharma@upi", "amount": 500.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "note": "Loan repayment", "created_at": _days_ago(15), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "Paytm Wallet", "payee_vpa": "paytm@paytm", "amount": 2000.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "created_at": _days_ago(20), "eligible_chargeback": True},
    # Priya's transactions
    {"user_id": "user-002", "payee_name": "Netflix", "payee_vpa": "netflix@icici", "amount": 649.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "ICICI Bank", "created_at": _days_ago(3), "eligible_chargeback": False},
    {"user_id": "user-002", "payee_name": "Flipkart", "payee_vpa": "fk@yesbank", "amount": 3499.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "ICICI Bank", "created_at": _days_ago(8), "eligible_chargeback": True},
    {"user_id": "user-002", "payee_name": "Anita Verma", "payee_vpa": "anitaverma@upi", "amount": 1500.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "ICICI Bank", "created_at": _days_ago(12), "eligible_chargeback": False},
    # Amit's transactions
    {"user_id": "user-003", "payee_name": "Jio", "payee_vpa": "jio@jiomoney", "amount": 299.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "Axis Bank", "created_at": _days_ago(1), "eligible_chargeback": False},
    {"user_id": "user-003", "payee_name": "IRCTC", "payee_vpa": "irctc@sbi", "amount": 1850.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "Axis Bank", "created_at": _days_ago(4), "eligible_chargeback": True},
    # More dummy data
    {"user_id": "user-001", "payee_name": "Zudio", "payee_vpa": "zudio@upi", "amount": 1299.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "HDFC Bank", "created_at": _days_ago(1), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "SXXXXXA PXXXXXXR", "payee_vpa": "sharma@upi", "amount": 1499.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "SBI", "created_at": _days_ago(1), "eligible_chargeback": False},
    {"user_id": "user-001", "payee_name": "WATERWALA LABS PVT", "payee_vpa": "water@upi", "amount": 1425.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "SBI", "created_at": _days_ago(2), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "BLINKIT", "payee_vpa": "blinkit@upi", "amount": 3309.55, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "Axis", "created_at": _days_ago(2), "eligible_chargeback": True},
    {"user_id": "user-001", "payee_name": "I FITNESS ZONE", "payee_vpa": "fitness@upi", "amount": 6000.0, "txn_type": "DEBIT", "status": "SUCCESS", "bank_name": "Axis", "created_at": _days_ago(3), "eligible_chargeback": True},
]

# Auto-generate 120 more transactions for user-001 to simulate heavy usage
import random
_MERCHANTS = ["Swiggy", "Zomato", "Amazon Pay", "BookMyShow", "Paytm Wallet", "Uber", "Ola", "Starbucks", "Blinkit", "Zepto"]
for _i in range(120):
    _m = random.choice(_MERCHANTS)
    _amt = float(random.randint(50, 2000))
    _days = random.randint(3, 180)
    TRANSACTIONS.append({
        "user_id": "user-001",
        "payee_name": _m,
        "payee_vpa": f"{_m.lower().replace(' ', '')}@upi",
        "amount": _amt,
        "txn_type": "DEBIT",
        "status": random.choice(["SUCCESS", "SUCCESS", "SUCCESS", "FAILED"]),
        "bank_name": "HDFC Bank",
        "note": f"Payment {_i}",
        "created_at": _days_ago(_days),
        "eligible_chargeback": random.choice([True, False])
    })

SAFETY_SWITCHES = [
    {"user_id": "user-001", "is_active": False},
    {"user_id": "user-002", "is_active": False},
    {"user_id": "user-003", "is_active": False},
]

CONFIRMATION_TEMPLATES = [
    # mandate_pause
    {"template_id": "confirm_mandate_pause_v2", "version": "2", "language": "en",
     "body": "Pause AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). Tap Confirm to pause.", "legal_reviewed": True},
    {"template_id": "confirm_mandate_pause_v2", "version": "2", "language": "hi",
     "body": "AutoPay रोकें: $merchant_name ($bank_name, ₹$amount/$frequency). पुष्टि के लिए Confirm दबाएं।", "legal_reviewed": True},
    {"template_id": "confirm_mandate_pause_v2", "version": "2", "language": "hi-Latn",
     "body": "AutoPay rokein: $merchant_name ($bank_name, ₹$amount/$frequency). Confirm dabayein.", "legal_reviewed": True},
    # mandate_resume
    {"template_id": "confirm_mandate_resume_v1", "version": "1", "language": "en",
     "body": "Resume AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). Tap Confirm to resume.", "legal_reviewed": True},
    {"template_id": "confirm_mandate_resume_v1", "version": "1", "language": "hi",
     "body": "AutoPay फिर शुरू करें: $merchant_name ($bank_name, ₹$amount/$frequency). Confirm दबाएं।", "legal_reviewed": True},
    # mandate_revoke
    {"template_id": "confirm_mandate_revoke_v1", "version": "1", "language": "en",
     "body": "PERMANENTLY cancel AutoPay: $merchant_name ($bank_name, ₹$amount/$frequency). This cannot be undone.", "legal_reviewed": True},
    {"template_id": "confirm_mandate_revoke_v1", "version": "1", "language": "hi",
     "body": "AutoPay PERMANENTLY cancel करें: $merchant_name ($bank_name, ₹$amount/$frequency). यह वापस नहीं होगा।", "legal_reviewed": True},
    # chargeback
    {"template_id": "confirm_chargeback_v1", "version": "1", "language": "en",
     "body": "Raise chargeback for: $payee_name — ₹$amount on $txn_date. Proceed in app.", "legal_reviewed": True},
    {"template_id": "confirm_chargeback_v1", "version": "1", "language": "hi",
     "body": "Chargeback raise करें: $payee_name — ₹$amount ($txn_date)। App में आगे बढ़ें।", "legal_reviewed": True},
    # replay
    {"template_id": "confirm_replay_v1", "version": "1", "language": "en",
     "body": "Repeat payment: $payee_name — ₹$amount. You will be asked for your UPI PIN. Mic will be disabled.", "legal_reviewed": True},
    {"template_id": "confirm_replay_v1", "version": "1", "language": "hi",
     "body": "Payment दोबारा करें: $payee_name — ₹$amount। UPI PIN डालना होगा। Mic बंद रहेगा।", "legal_reviewed": True},
    # safety_switch
    {"template_id": "confirm_safety_switch_v1", "version": "1", "language": "en",
     "body": "Toggle Safety Switch. This will $action all UPI transactions. Proceed in app.", "legal_reviewed": True},
    {"template_id": "confirm_safety_switch_v1", "version": "1", "language": "hi",
     "body": "Safety Switch बदलें। इससे सभी UPI transactions $action हो जाएंगे। App में आगे बढ़ें।", "legal_reviewed": True},
    # delink
    {"template_id": "confirm_delink_v1", "version": "1", "language": "en",
     "body": "Delink mobile number $number from UPI. This will remove $vpa. Proceed in app.", "legal_reviewed": True},
    {"template_id": "confirm_delink_v1", "version": "1", "language": "hi",
     "body": "Mobile number $number को UPI से delink करें। $vpa हट जाएगा। App में आगे बढ़ें।", "legal_reviewed": True},
]

FAQ_KB = [
    {"question": "What is UPI?", "answer": "UPI (Unified Payments Interface) is a real-time payment system developed by NPCI that allows instant bank-to-bank transfers via mobile. It works 24/7/365.", "language": "en"},
    {"question": "How do I check my UPI transaction history?", "answer": "You can view all your UPI transactions in the Transactions section of BHIM app. Just say 'show my transactions' in this chat.", "language": "en"},
    {"question": "What is an AutoPay mandate?", "answer": "An AutoPay mandate is a standing instruction to automatically debit your bank account for recurring payments (subscriptions, bills, EMIs) on a set schedule.", "language": "en"},
    {"question": "How do I pause an AutoPay?", "answer": "You can pause any AutoPay by saying 'pause [merchant name] autopay' in this chat. I'll confirm before making any changes.", "language": "en"},
    {"question": "What is a chargeback?", "answer": "A chargeback is a reversal of a payment transaction. You can raise one if you didn't receive goods/services you paid for or were charged incorrectly.", "language": "en"},
    {"question": "What is Safety Switch?", "answer": "Safety Switch is a feature that lets you instantly block/unblock all UPI transactions from your account for security. It's a 2-step process in the app.", "language": "en"},
    {"question": "UPI kya hai?", "answer": "UPI (Unified Payments Interface) ek real-time payment system hai jo NPCI ne banaya hai. Isse aap mobile se instantly bank-to-bank transfer kar sakte hain.", "language": "hi-Latn"},
    {"question": "Transaction fail kyu hua?", "answer": "UPI transaction fail hone ke kai karan ho sakte hain: insufficient balance, bank server down, VPA galat, ya network issue. BHIM app mein details check karein.", "language": "hi-Latn"},
    {"question": "AutoPay kaise band kare?", "answer": "Aap is chat mein bol sakte hain 'Swiggy ka autopay band karo' ya 'Netflix mandate revoke karo'. Main confirm karke aagey badhta hoon.", "language": "hi-Latn"},
    {"question": "UPI PIN kya hai?", "answer": "UPI PIN aapka secret 4-6 digit PIN hai jo payment confirm karne ke liye use hota hai. Ise kabhi kisi ke saath share mat karein.", "language": "hi-Latn"},
    {"question": "How to raise a chargeback?", "answer": "To raise a chargeback, say 'raise chargeback for [merchant] payment'. I'll find the transaction and take you to the dispute screen in the app.", "language": "en"},
    {"question": "UPI transaction kaise dekhein?", "answer": "अपने UPI transactions देखने के लिए बस कहें 'मेरे transactions दिखाओ' या 'recent payments show karo'।", "language": "hi"},
    {"question": "Mandate pause kaise karein?", "answer": "Mandate pause karne ke liye kahein 'Swiggy wala autopay pause karo'. Main aapke account mein dhundhkar confirm karoonga.", "language": "hi"},
    {"question": "Can I pause an AutoPay mandate?", "answer": "Yes, you can pause any active AutoPay mandate temporarily and resume it later from the UPI Autopay section.", "language": "en"},
    {"question": "What happens if my transaction fails but money is debited?", "answer": "Don't worry. The money is usually refunded within 3-5 business days. If it's a merchant transaction, you can raise a dispute.", "language": "en"},
    {"question": "How do I find a past transaction?", "answer": "Go to the Transactions tab to see your consolidated UPI activity. You can filter by date or search for specific payees.", "language": "en"},
    {"question": "Can I raise a chargeback for money sent to a friend?", "answer": "No. Chargebacks or disputes can only be raised for merchant payments, not for person-to-person (P2P) transfers.", "language": "en"},
    {"question": "Dost ko bheja paisa wapas kaise lu?", "answer": "Person-to-person transfers me chargeback nahi hota. Agar galti se paisa chala gaya hai toh apne bank se sampark karein.", "language": "hi-Latn"},
]


# ── Seed function ──────────────────────────────────────────────────────────────

async def seed_db(session: AsyncSession) -> None:
    """Idempotent seed: skip if data already exists."""
    # Check if already seeded
    result = await session.execute(select(User).where(User.id == "user-001"))
    if result.scalar_one_or_none():
        logger.info("Seed: data already exists, skipping.")
        return

    logger.info("Seed: inserting mock data...")

    # Users
    for u in USERS:
        session.add(User(
            id=u["id"], username=u["username"], full_name=u["full_name"],
            phone=u["phone"], locale=u["locale"], pin_hash=_pin_hash(u["pin"]),
        ))

    # Bank accounts
    for ba in BANK_ACCOUNTS:
        session.add(BankAccount(**ba))

    # UPI numbers
    for upi in UPI_NUMBERS:
        session.add(UPINumber(**upi))

    # Payees
    for p in PAYEES:
        session.add(Payee(**{k: v for k, v in p.items()}))

    # Mandates
    for m in MANDATES:
        session.add(Mandate(**m))

    # Transactions — generate UUIDs and txn refs
    for t in TRANSACTIONS:
        session.add(Transaction(
            id=str(uuid.uuid4()),
            txn_ref=_txn_ref(),
            currency="INR",
            chargeback_raised=False,
            **t,
        ))

    # Safety switches
    for ss in SAFETY_SWITCHES:
        session.add(SafetySwitch(id=str(uuid.uuid4()), **ss))

    # Confirmation templates
    for ct in CONFIRMATION_TEMPLATES:
        session.add(ConfirmationTemplate(id=str(uuid.uuid4()), **ct))

    # FAQ KB
    for faq in FAQ_KB:
        session.add(FAQEntry(id=str(uuid.uuid4()), **faq))

    await session.commit()
    logger.info("Seed: done.")


async def main():
    logging.basicConfig(level=logging.INFO)
    await create_all_tables()
    factory = get_session_factory()
    async with factory() as session:
        await seed_db(session)
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())

