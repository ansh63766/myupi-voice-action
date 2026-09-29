"""
db/models.py — SQLAlchemy ORM models.
All tables per CONTEXT.md §8.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_txn_ref() -> str:
    """16-char uppercase alphanumeric transaction reference."""
    import secrets, string
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


class Base(DeclarativeBase):
    pass


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str] = mapped_column(String(16), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="en")  # en|hi|hi-Latn
    pin_hash: Mapped[str] = mapped_column(String(128), nullable=False)  # bcrypt
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    bank_accounts: Mapped[list["BankAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    upi_numbers: Mapped[list["UPINumber"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    payees: Mapped[list["Payee"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    mandates: Mapped[list["Mandate"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    safety_switch: Mapped[Optional["SafetySwitch"]] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    voice_sessions: Mapped[list["VoiceSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


# ── Bank Accounts ─────────────────────────────────────────────────────────────

class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    ifsc: Mapped[str] = mapped_column(String(11), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)

    user: Mapped["User"] = relationship(back_populates="bank_accounts")


# ── UPI Numbers ───────────────────────────────────────────────────────────────

class UPINumber(Base):
    __tablename__ = "upi_numbers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)  # +91XXXXXXXXXX
    vpa: Mapped[str] = mapped_column(String(64), nullable=False)     # number@bankname
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_bank_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bank_accounts.id"))

    user: Mapped["User"] = relationship(back_populates="upi_numbers")

    __table_args__ = (UniqueConstraint("user_id", "number"),)


# ── Payees ────────────────────────────────────────────────────────────────────

class Payee(Base):
    __tablename__ = "payees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)   # "Swiggy wala"
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False) # "Swiggy"
    vpa: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(16))
    is_merchant: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="payees")


# ── Transactions ──────────────────────────────────────────────────────────────

TxnStatus = Enum("SUCCESS", "FAILED", "PENDING", "REVERSED", name="txn_status")
TxnType = Enum("DEBIT", "CREDIT", name="txn_type")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    txn_ref: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, default=gen_txn_ref)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    txn_type: Mapped[str] = mapped_column(String(10), nullable=False)  # DEBIT|CREDIT
    status: Mapped[str] = mapped_column(String(10), nullable=False)    # SUCCESS|FAILED|...
    payee_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payee_vpa: Mapped[str] = mapped_column(String(128), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    eligible_chargeback: Mapped[bool] = mapped_column(Boolean, default=False)
    chargeback_raised: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="transactions")

    __table_args__ = (Index("ix_txn_user_created", "user_id", "created_at"),)


# ── Mandates ──────────────────────────────────────────────────────────────────

MandateStatus = Enum("ACTIVE", "PAUSED", "REVOKED", "EXPIRED", name="mandate_status")


class Mandate(Base):
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    merchant_name: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. "Swiggy One"
    merchant_vpa: Mapped[str] = mapped_column(String(128), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), default="MONTHLY")  # DAILY|WEEKLY|MONTHLY|YEARLY
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_debit_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    nickname: Mapped[Optional[str]] = mapped_column(String(128))  # user-given name

    user: Mapped["User"] = relationship(back_populates="mandates")


# ── Safety Switch ─────────────────────────────────────────────────────────────

class SafetySwitch(Base):
    __tablename__ = "safety_switch_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # False = UPI enabled
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="safety_switch")


# ── Sessions ──────────────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


# ── Conversations / Messages ───────────────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    channel: Mapped[str] = mapped_column(String(16), default="text")  # text|voice

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent_label: Mapped[Optional[str]] = mapped_column(String(64))
    action_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


# ── Confirmation Templates ────────────────────────────────────────────────────

class ConfirmationTemplate(Base):
    __tablename__ = "confirmation_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)  # en|hi|hi-Latn
    body: Mapped[str] = mapped_column(Text, nullable=False)
    legal_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("template_id", "version", "language"),)


# ── Audit Log ─────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    # Chain links — all must be present for action to fire
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    voice_session_id: Mapped[Optional[str]] = mapped_column(String(36))
    transcript_hash: Mapped[Optional[str]] = mapped_column(String(64))  # SHA-256 of authoritative transcript
    resolved_entity_ids: Mapped[Optional[str]] = mapped_column(Text)    # JSON-encoded dict of slot→id
    policy_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    confirmation_event_id: Mapped[Optional[str]] = mapped_column(String(36))
    confirmation_template_id: Mapped[Optional[str]] = mapped_column(String(64))
    confirmation_template_version: Mapped[Optional[str]] = mapped_column(String(16))
    downstream_call_id: Mapped[Optional[str]] = mapped_column(String(36))
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # SUCCESS|BLOCKED|CANCELLED|ERROR
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ── Voice Sessions ────────────────────────────────────────────────────────────

class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    utterance_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="voice_sessions")


# ── FAQ KB ────────────────────────────────────────────────────────────────────

class FAQEntry(Base):
    __tablename__ = "faq_kb"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en")
    tags: Mapped[Optional[str]] = mapped_column(String(256))  # comma-separated
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
