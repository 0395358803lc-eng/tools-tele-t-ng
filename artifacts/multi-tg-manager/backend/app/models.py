from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    first_name: Mapped[str] = mapped_column(String(64), default="")
    last_name: Mapped[str] = mapped_column(String(64), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    bio: Mapped[str] = mapped_column(String(140), default="")
    session_file: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(16), default="disconnected"
    )  # connected/disconnected/banned
    has_2fa: Mapped[bool] = mapped_column(Boolean, default=False)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["SecurityMessage"]] = relationship(
        back_populates="account", cascade="all,delete-orphan"
    )


class SecurityMessage(Base):
    __tablename__ = "security_messages"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "tg_msg_id", name="uq_security_messages_account_tg_msg"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    tg_msg_id: Mapped[int] = mapped_column(BigInteger)
    message_text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32), default="unknown")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(back_populates="messages")


class GoneAccount(Base):
    """Tombstone for an account that left the active list — either banned by
    Telegram or manually removed. `account_id` is a plain int (NOT a FK) so the
    record survives the hard delete of the Account row. `old_serial` is the
    1-based position the account held among active (non-banned) accounts at the
    moment it left."""

    __tablename__ = "gone_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    first_name: Mapped[str] = mapped_column(String(64), default="")
    last_name: Mapped[str] = mapped_column(String(64), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    old_serial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(16), default="removed")  # banned/removed
    gone_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TelegramSessionBlob(Base):
    __tablename__ = "telegram_session_blobs"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RememberedTwoFa(Base):
    __tablename__ = "remembered_twofa"
    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(Integer)
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
